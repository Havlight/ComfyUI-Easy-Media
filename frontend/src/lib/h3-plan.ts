import {
  continuityModeChangePatch,
  DRIFT_TAPER_CONTINUITY_MODE,
  DRIFT_TAPER_DEFAULT_FLOOR,
  DRIFT_TAPER_DEFAULT_STEPS,
  driftTaperFloorPatch,
  driftTaperLowOnlyPatch,
  driftTaperStepsPatch,
  getDriftTaperFloor,
  getDriftTaperLowOnly,
  getDriftTaperSteps,
  normalizeDriftTaperFloor,
  normalizeDriftTaperSteps,
  type DriftTaperContent,
} from '@/lib/h3-drift-taper'
import { H3_NATIVE_CONTEXT_FRAMES, isH3Continuation } from '@/lib/h3-native-timing'
import {
  H3_MEDIA_REFERENCE_LIMIT,
  H3_TRACK_SOURCE,
  h3NextTaskStart,
  h3PlaceholderKey,
  h3ReferenceLabels,
  h3SharedReferenceClip,
  planH3TaskReferences,
  type H3MediaReference,
  type H3TaskReferencePlan,
} from '@/lib/h3-references'
import {
  getSelectedTaskUserPrompt,
  getSelectedTaskUserPromptPatch,
  MULTITRACK_CONTINUITY_MODES,
  MULTITRACK_DEFAULT_VOLUME_DB,
  MULTITRACK_MEDIA_TRACK_LIMIT,
  MULTITRACK_REF_IMAGE_SIZES,
  MULTITRACK_TASK_MODES,
  MULTITRACK_TRACK_COLORS,
} from '@/lib/multitrack-utils'
import { MAX_TASK_IMAGES, taskImageIdentity } from '@/lib/task-image-utils'
import { taskMediaIdentity } from '@/lib/task-media-utils'
import { uuid } from '@/lib/uuid'
import type {
  MultiTrack,
  MultiTrackContinuityMode,
  MultiTrackRefImageSize,
  MultiTrackSegment,
  MultiTrackSegmentContent,
  MultiTrackTaskImage,
  MultiTrackTaskMedia,
  MultiTrackTaskMode,
  TrackData,
} from '@/types/multitrack'

/**
 * H3 plan files: the task track and the timeline's video and audio tracks as JSON that a
 * person or an agent can edit and import, and that rebuilds the Editor in another workflow.
 *
 * The file lists every task in order. A task with an `id` updates that task and keeps its
 * saved results where nothing relevant changed; a task without one is new; tasks missing
 * from the file are removed. Fields left out of an existing task keep their value. Subtitle
 * tracks, system prompts, prompt B, resolution, Project settings and segment LoRAs are not
 * part of the plan and stay as they are.
 *
 * References are strings or objects. A string is a local path or URL to copy into ComfyUI's
 * input folder (`file`), or "previous_frame". Objects name the source with one of `file`,
 * `local` (kept in place), `url` (kept as a URL), `input`, `output`, `slot`, `track` (a
 * timeline placeholder, with `clip` for a shared clip) or `previous_frame: true`, plus
 * `muted`, `shared`, `fit` (audio) and `soundtrack` (video).
 *
 * `tracks`, when present, lists every video and audio track; tracks and clips missing from it
 * are removed. A clip with a source is described in full, so its unwritten settings are the
 * defaults. A clip with only an `id` is an existing clip, which `at` or `start` can move.
 * `at` places a clip at a task's start: a task id, or the task's 1-based position in the file.
 * `until` ends a clip, new or existing, at a task's end the same way, trimming the rest of
 * its source; a locked video can then end exactly where its last task does.
 */
export const H3_PLAN_FORMAT = 'easy-media-h3-plan'
export const H3_PLAN_VERSION = 1

export interface H3PlanReferenceObject {
  file?: string
  local?: string
  url?: string
  input?: string
  output?: string
  slot?: string
  track?: string
  clip?: string
  previous_frame?: boolean
  muted?: boolean
  shared?: boolean
  fit?: boolean
  soundtrack?: boolean
}

export type H3PlanReference = string | H3PlanReferenceObject

/**
 * The Drift seam. Agents pick "soft" (taper 4 to 0.4, the plan default for new Drift tasks)
 * or "clean" (taper 4 to 0, original Drift-Control); other settings export as an object.
 */
export type H3PlanSeam = 'soft' | 'clean' | { taper?: number; floor?: number; low_only?: boolean }

export interface H3PlanTask {
  id?: string
  seconds?: number
  gap_before?: number
  mode?: MultiTrackTaskMode
  continuity?: MultiTrackContinuityMode
  seam?: H3PlanSeam
  ref_image_size?: MultiTrackRefImageSize
  prompt?: string
  images?: H3PlanReference[]
  videos?: H3PlanReference[]
  audios?: H3PlanReference[]
}

/** A timeline clip; times are seconds on the timeline, `from` is seconds into the source. */
export interface H3PlanClip extends Pick<H3PlanReferenceObject, 'file' | 'local' | 'url' | 'input' | 'output' | 'slot'> {
  id?: string
  at?: string | number
  until?: string | number
  start?: number
  end?: number
  from?: number
  volume?: number
  muted?: boolean
  shared?: boolean
}

export interface H3PlanTrack {
  id?: string
  type: 'video' | 'audio'
  audio_locked?: boolean
  /** A locked video's sound: "output" (the default) is the task's audio; "reference" is only heard. */
  soundtrack?: 'output' | 'reference'
  muted?: boolean
  volume?: number
  clips: H3PlanClip[]
}

export interface H3PlanFile {
  format: typeof H3_PLAN_FORMAT
  version: typeof H3_PLAN_VERSION
  frame_rate?: number
  /** The timeline's master volume and mute; left out, they stay as they are. */
  volume?: number
  muted?: boolean
  tasks: H3PlanTask[]
  tracks?: H3PlanTrack[]
}

export type H3PlanMediaType = 'image' | 'video' | 'audio'

/** A file the server copies or checks: exactly one of file, local, input or output. */
export interface H3PlanMediaRequest {
  media_type: H3PlanMediaType
  file?: string
  local?: string
  input?: string
  output?: string
  /** Also read the length (and for video, whether it has sound) of a clip placed without an end. */
  probe?: boolean
}

export type H3PlanMediaResult =
  | {
    source_type: 'input' | 'output' | 'local'
    file_path?: string
    local_path?: string
    file_name: string
    duration?: number | null
    has_audio?: boolean | null
  }
  | { error: string }

export interface H3PlanIssue {
  level: 'error' | 'warning'
  /** 1-based task number in the file; absent for the whole file. */
  task?: number
  /** 1-based track number in `tracks`, and clip number in that track. */
  track?: number
  clip?: number
  code: string
  params?: Record<string, string | number>
}

export type H3PlanChange = 'unchanged' | 'modified' | 'added' | 'removed'

export interface H3PlanRow {
  id: string
  /** 1-based position after import; removed tasks keep their old position. */
  index: number
  change: H3PlanChange
  seconds: number
  beforeSeconds?: number
  mode: MultiTrackTaskMode
  continuity: MultiTrackContinuityMode
  images: number
  videos: number
  audios: number
  prompt: string
  /** What each prompt label stands for, in label order; absent on removed tasks. */
  labels?: Record<string, string>
}

export interface H3PlanClipSummary {
  name: string
  start: number
  end: number
}

export interface H3PlanTrackRow {
  id: string
  /** 1-based position among the video and audio tracks; removed tracks keep their old one. */
  index: number
  type: 'video' | 'audio'
  change: H3PlanChange
  audioLocked: boolean
  /** Present when a locked video's sound is only referenced. */
  soundtrack?: 'reference'
  muted: boolean
  clips: H3PlanClipSummary[]
}

export interface H3PlanImport {
  data: TrackData | null
  rows: H3PlanRow[]
  /** Video and audio tracks; empty when the plan leaves them out. */
  trackRows: H3PlanTrackRow[]
  issues: H3PlanIssue[]
}

type FileReference = { kind: 'file' | 'local' | 'url' | 'input' | 'output' | 'slot'; value: string; flags: H3PlanReferenceObject }

type ParsedReference =
  | { kind: 'previous_frame'; flags: H3PlanReferenceObject }
  | FileReference
  | { kind: 'track'; value: string; clip?: string; flags: H3PlanReferenceObject }

const SOURCE_KEYS = ['file', 'local', 'url', 'input', 'output', 'slot', 'track', 'previous_frame'] as const
const CLIP_SOURCE_KEYS = ['file', 'local', 'url', 'input', 'output', 'slot'] as const
const TASK_KEYS = new Set(['id', 'seconds', 'gap_before', 'mode', 'continuity', 'seam', 'ref_image_size', 'prompt', 'images', 'videos', 'audios'])
const TRACK_KEYS = new Set(['id', 'type', 'audio_locked', 'soundtrack', 'muted', 'volume', 'clips'])
const CLIP_KEYS = new Set([...CLIP_SOURCE_KEYS, 'id', 'at', 'until', 'start', 'end', 'from', 'volume', 'muted', 'shared'])
const REFERENCE_FIELDS = [['images', 'image'], ['videos', 'video'], ['audios', 'audio']] as const
const PROMPT_TAG = /<(Picture|Video|Audio) (\d+)>/g

interface SeamSettings {
  steps: number
  floor: number
  lowOnly: boolean
}

const SEAM_PRESETS: Record<'soft' | 'clean', SeamSettings> = {
  soft: { steps: 4, floor: 0.4, lowOnly: false },
  clean: { steps: DRIFT_TAPER_DEFAULT_STEPS, floor: DRIFT_TAPER_DEFAULT_FLOOR, lowOnly: false },
}
/** New Drift tasks from a plan; the Editor's own default stays "clean". */
const PLAN_DEFAULT_SEAM: keyof typeof SEAM_PRESETS = 'soft'

function round3(value: number): number {
  return Math.round(value * 1000) / 1000
}

function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

function taskTrackOf(data: TrackData): MultiTrack | undefined {
  return data.tracks.find((track) => track.type === 'task')
}

function orderedTasks(data: TrackData): MultiTrackSegment[] {
  return [...(taskTrackOf(data)?.segments ?? [])].sort((a, b) => a.start_frame - b.start_frame)
}

function isMediaTrack(track: MultiTrack): track is MultiTrack & { type: 'video' | 'audio' } {
  return track.type === 'video' || track.type === 'audio'
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

function withFlags(source: H3PlanReference, flags: H3PlanReferenceObject): H3PlanReference {
  const set = Object.fromEntries(Object.entries(flags).filter(([, value]) => value !== undefined))
  if (Object.keys(set).length === 0) return source
  return typeof source === 'string' ? { ...(source === 'previous_frame' ? { previous_frame: true } : { file: source }), ...set } : { ...source, ...set }
}

function exportSource(item: MultiTrackTaskImage | MultiTrackTaskMedia): H3PlanReference {
  switch (item.source_type) {
    case 'previous_frame': return 'previous_frame'
    case 'output': return { output: item.file_path ?? '' }
    case 'local': return { local: item.local_path ?? '' }
    case 'url': return { url: item.url ?? '' }
    case 'slot': return { slot: item.slot_name ?? '' }
    default: return { input: item.file_path ?? '' }
  }
}

function exportImage(image: MultiTrackTaskImage): H3PlanReference {
  return withFlags(exportSource(image), {
    muted: image.muted === true ? true : undefined,
    shared: image.shared_reference === true ? true : undefined,
  })
}

function exportMedia(item: MultiTrackTaskMedia, mediaType: 'video' | 'audio'): H3PlanReference {
  const source: H3PlanReference = item.source_type === H3_TRACK_SOURCE
    ? { track: item.track_id ?? '', ...(item.segment_id ? { clip: item.segment_id } : {}) }
    : exportSource(item)
  return withFlags(source, {
    muted: typeof item.muted === 'boolean' && (item.muted || item.source_type === H3_TRACK_SOURCE) ? item.muted : undefined,
    shared: item.shared_reference === true ? true : undefined,
    fit: mediaType === 'audio' ? item.fit : undefined,
    soundtrack: mediaType === 'video' ? item.soundtrack : undefined,
  })
}

function exportSeam(content: DriftTaperContent): H3PlanSeam {
  const seam: SeamSettings = { steps: getDriftTaperSteps(content), floor: getDriftTaperFloor(content), lowOnly: getDriftTaperLowOnly(content) }
  for (const [name, preset] of Object.entries(SEAM_PRESETS) as [keyof typeof SEAM_PRESETS, SeamSettings][]) {
    if (preset.steps === seam.steps && preset.floor === seam.floor && preset.lowOnly === seam.lowOnly) return name
  }
  return {
    ...(seam.steps !== DRIFT_TAPER_DEFAULT_STEPS ? { taper: seam.steps } : {}),
    ...(seam.floor !== DRIFT_TAPER_DEFAULT_FLOOR ? { floor: seam.floor } : {}),
    ...(seam.lowOnly ? { low_only: true } : {}),
  }
}

function exportClip(segment: MultiTrackSegment, fps: number): H3PlanClip {
  const content = segment.content
  const from = segment.origin_start_frame === undefined ? 0 : segment.start_frame - segment.origin_start_frame
  return {
    id: segment.id,
    ...(exportSource({ id: segment.id, ...content }) as H3PlanReferenceObject),
    start: round3(segment.start_frame / fps),
    end: round3(segment.end_frame / fps),
    ...(from !== 0 ? { from: round3(from / fps) } : {}),
    ...(content.volume_db ? { volume: content.volume_db } : {}),
    ...(content.muted === true ? { muted: true } : {}),
    ...(content.shared_reference === true ? { shared: true } : {}),
  }
}

function exportTrack(track: MultiTrack, fps: number): H3PlanTrack {
  return {
    id: track.id,
    type: track.type as H3PlanTrack['type'],
    ...(track.audio_locked === true ? { audio_locked: true } : {}),
    ...(track.type === 'video' && track.soundtrack === 'reference' ? { soundtrack: 'reference' as const } : {}),
    ...(track.muted ? { muted: true } : {}),
    ...(track.volume_db ? { volume: track.volume_db } : {}),
    clips: [...track.segments].sort((a, b) => a.start_frame - b.start_frame).map((segment) => exportClip(segment, fps)),
  }
}

/** The task track and the video and audio tracks as a plan file; importing it unchanged changes nothing. */
export function exportH3Plan(data: TrackData): H3PlanFile {
  const fps = data.frame_rate
  let cursor = 0
  const tasks = orderedTasks(data).map((segment): H3PlanTask => {
    const gap = segment.start_frame - cursor
    cursor = segment.end_frame
    const content = segment.content
    return {
      id: segment.id,
      seconds: round3((segment.end_frame - segment.start_frame) / fps),
      ...(gap > 0 ? { gap_before: round3(gap / fps) } : {}),
      mode: content.task_mode ?? 'default',
      continuity: content.continuity_mode ?? 'shot',
      ...(content.continuity_mode === DRIFT_TAPER_CONTINUITY_MODE ? { seam: exportSeam(content) } : {}),
      ...(content.ref_image_size === 'max' ? { ref_image_size: content.ref_image_size } : {}),
      prompt: getSelectedTaskUserPrompt(content),
      images: (content.images ?? []).map(exportImage),
      videos: (content.videos ?? []).map((item) => exportMedia(item, 'video')),
      audios: (content.audios ?? []).map((item) => exportMedia(item, 'audio')),
    }
  })
  return {
    format: H3_PLAN_FORMAT,
    version: H3_PLAN_VERSION,
    frame_rate: fps,
    ...(data.volume_db ? { volume: data.volume_db } : {}),
    ...(data.muted ? { muted: true } : {}),
    tasks,
    tracks: data.tracks.filter(isMediaTrack).map((track) => exportTrack(track, fps)),
  }
}

// ---------------------------------------------------------------------------
// Parse
// ---------------------------------------------------------------------------

/** Read a plan file; only the outline is checked here, tasks are checked on import. */
export function parseH3Plan(text: string): { plan: H3PlanFile | null; issues: H3PlanIssue[] } {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch (cause: unknown) {
    return { plan: null, issues: [{ level: 'error', code: 'invalid_json', params: { message: cause instanceof Error ? cause.message : String(cause) } }] }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { plan: null, issues: [{ level: 'error', code: 'invalid_format' }] }
  }
  const file = value as Partial<H3PlanFile>
  if (file.version !== H3_PLAN_VERSION) {
    return { plan: null, issues: [{ level: 'error', code: 'invalid_version', params: { version: String(file.version) } }] }
  }
  if (!Array.isArray(file.tasks)) return { plan: null, issues: [{ level: 'error', code: 'invalid_format' }] }
  return {
    plan: {
      format: H3_PLAN_FORMAT,
      version: H3_PLAN_VERSION,
      frame_rate: file.frame_rate,
      ...(file.volume !== undefined ? { volume: file.volume } : {}),
      ...(file.muted !== undefined ? { muted: file.muted } : {}),
      tasks: file.tasks,
      ...(file.tracks !== undefined ? { tracks: file.tracks } : {}),
    },
    issues: [],
  }
}

/** A clip's source, or null for an id-only clip; undefined when it names more than one. */
function parseClipSource(clip: H3PlanClip): FileReference | null | undefined {
  const keys = CLIP_SOURCE_KEYS.filter((key) => clip[key] !== undefined)
  if (keys.length === 0) return null
  const value = clip[keys[0]]
  if (keys.length > 1 || typeof value !== 'string' || !value) return undefined
  return { kind: keys[0], value, flags: {} }
}

function planClips(plan: H3PlanFile): { track: H3PlanTrack; clip: H3PlanClip }[] {
  if (!Array.isArray(plan.tracks)) return []
  return plan.tracks.flatMap((track) => (
    track && typeof track === 'object' && Array.isArray(track.clips)
      ? track.clips.filter((clip) => clip && typeof clip === 'object').map((clip) => ({ track, clip }))
      : []
  ))
}

function parseReference(raw: unknown): ParsedReference | null {
  if (typeof raw === 'string') {
    if (raw === 'previous_frame') return { kind: 'previous_frame', flags: {} }
    return raw ? { kind: 'file', value: raw, flags: {} } : null
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const object = raw as H3PlanReferenceObject
  const keys = SOURCE_KEYS.filter((key) => object[key] !== undefined)
  if (keys.length !== 1) return null
  const [kind] = keys
  if (kind === 'previous_frame') return object.previous_frame === true ? { kind, flags: object } : null
  const value = object[kind]
  if (typeof value !== 'string' || !value) return null
  if (kind === 'track') return { kind, value, clip: typeof object.clip === 'string' && object.clip ? object.clip : undefined, flags: object }
  return { kind, value, flags: object }
}

function mediaRequest(reference: ParsedReference, mediaType: H3PlanMediaType): H3PlanMediaRequest | null {
  if (reference.kind === 'file' || reference.kind === 'local' || reference.kind === 'input' || reference.kind === 'output') {
    return { media_type: mediaType, [reference.kind]: reference.value }
  }
  return null
}

export function h3PlanMediaKey(request: H3PlanMediaRequest): string {
  const kind = (['file', 'local', 'input', 'output'] as const).find((key) => request[key] !== undefined) ?? 'file'
  return `${request.media_type}\u0000${kind}\u0000${request[kind]}`
}

/** Every file the server must copy or check before the import can be previewed. */
export function h3PlanMediaRequests(plan: H3PlanFile): H3PlanMediaRequest[] {
  const requests = new Map<string, H3PlanMediaRequest>()
  for (const task of plan.tasks) {
    if (!task || typeof task !== 'object') continue
    for (const [field, mediaType] of REFERENCE_FIELDS) {
      const list = task[field]
      if (!Array.isArray(list)) continue
      for (const raw of list) {
        const reference = parseReference(raw)
        const request = reference ? mediaRequest(reference, mediaType) : null
        if (request) requests.set(h3PlanMediaKey(request), request)
      }
    }
  }
  for (const { track, clip } of planClips(plan)) {
    const reference = parseClipSource(clip)
    if (!reference || (track.type !== 'video' && track.type !== 'audio')) continue
    const request = mediaRequest(reference, track.type)
    if (!request) continue
    // A new clip records its file's length as the Editor does, even with an end, so a plan
    // restored into another tab matches the saved results generated from it.
    requests.set(h3PlanMediaKey(request), { ...request, probe: true })
  }
  return [...requests.values()]
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

interface ImportContext {
  /** Where a problem is: a task, or a track and clip of `tracks`. */
  at: Pick<H3PlanIssue, 'task' | 'track' | 'clip'>
  tracks: MultiTrack[]
  media: ReadonlyMap<string, H3PlanMediaResult>
  issues: H3PlanIssue[]
}

function issue(context: ImportContext, level: H3PlanIssue['level'], code: string, params?: H3PlanIssue['params']) {
  context.issues.push({ level, ...context.at, code, ...(params ? { params } : {}) })
}

/** The source fields of a reference that names a file; null after reporting why it cannot. */
function sourceFields(
  reference: ParsedReference,
  mediaType: H3PlanMediaType,
  context: ImportContext,
): Partial<MultiTrackTaskImage> | null {
  if (reference.kind === 'url') {
    return { source_type: 'url', url: reference.value, file_name: basename(reference.value.split('?')[0]) }
  }
  if (reference.kind === 'slot') return { source_type: 'slot', slot_name: reference.value, file_name: reference.value }
  const request = mediaRequest(reference, mediaType)
  if (!request) return null
  const result = context.media.get(h3PlanMediaKey(request))
  if (!result || 'error' in result) {
    issue(context, 'error', 'file_failed', { source: reference.kind === 'previous_frame' ? '' : reference.value, message: result && 'error' in result ? result.error : '' })
    return null
  }
  return result.source_type === 'local'
    ? { source_type: 'local', local_path: result.local_path, file_name: result.file_name }
    : { source_type: result.source_type, file_path: result.file_path, file_name: result.file_name }
}

function warnIgnoredFlags(reference: ParsedReference, allowed: ReadonlySet<string>, field: string, context: ImportContext) {
  for (const flag of ['muted', 'shared', 'fit', 'soundtrack'] as const) {
    if (reference.flags[flag] !== undefined && !allowed.has(flag)) issue(context, 'warning', 'flag_ignored', { flag, field })
  }
}

function withoutKeys<T extends object>(item: T, ...keys: string[]): T {
  const next = { ...item } as Record<string, unknown>
  for (const key of keys) delete next[key]
  return next as T
}

/** Set a flag whose unset value means false; an equal flag is left as stored. */
function setSwitch<T extends object>(item: T, key: string, enabled: boolean, ...companions: string[]): T {
  const current = (item as Record<string, unknown>)[key] === true
  if (current === enabled) return item
  return enabled ? { ...item, [key]: true } : withoutKeys(item, key, ...companions)
}

/** Set a flag whose unset value has its own meaning, such as a default. */
function setOptional<T extends object>(item: T, key: string, value: boolean | undefined): T {
  if ((item as Record<string, unknown>)[key] === value) return item
  return value === undefined ? withoutKeys(item, key) : { ...item, [key]: value }
}

function parseSeam(raw: unknown): SeamSettings | null {
  if (raw === 'soft' || raw === 'clean') return SEAM_PRESETS[raw]
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const object = raw as Record<string, unknown>
  if (Object.keys(object).some((key) => key !== 'taper' && key !== 'floor' && key !== 'low_only')) return null
  const steps = object.taper === undefined ? DRIFT_TAPER_DEFAULT_STEPS : normalizeDriftTaperSteps(object.taper)
  const floor = object.floor === undefined ? DRIFT_TAPER_DEFAULT_FLOOR : normalizeDriftTaperFloor(object.floor)
  if (steps === undefined || floor === undefined) return null
  if (object.low_only !== undefined && typeof object.low_only !== 'boolean') return null
  return { steps, floor, lowOnly: object.low_only === true }
}

function seamPatch(seam: SeamSettings): DriftTaperContent {
  return { ...driftTaperStepsPatch(seam.steps), ...driftTaperFloorPatch(seam.floor), ...driftTaperLowOnlyPatch(seam.lowOnly) }
}

function buildImages(raw: unknown[], old: MultiTrackTaskImage[], context: ImportContext): MultiTrackTaskImage[] {
  const images: MultiTrackTaskImage[] = []
  raw.forEach((value, index) => {
    const reference = parseReference(value)
    if (!reference || reference.kind === 'track') {
      issue(context, 'error', 'invalid_reference', { field: 'images', index: index + 1 })
      return
    }
    warnIgnoredFlags(reference, new Set(['muted', 'shared']), 'images', context)
    // An existing image with the same source keeps its id and settings the plan does not cover.
    let image: MultiTrackTaskImage
    if (reference.kind === 'previous_frame') {
      image = old.find((item) => item.source_type === 'previous_frame') ?? { id: uuid(), source_type: 'previous_frame' }
    } else {
      const fields = sourceFields(reference, 'image', context)
      if (!fields) return
      const candidate = { id: uuid(), ...fields } as MultiTrackTaskImage
      image = old.find((item) => item.source_type !== 'previous_frame' && taskImageIdentity(item) === taskImageIdentity(candidate))
        ?? candidate
    }
    image = setSwitch(image, 'muted', reference.flags.muted === true)
    image = setSwitch(image, 'shared_reference', reference.kind !== 'previous_frame' && reference.flags.shared === true,
      'shared_reference_copy')
    images.push(image)
  })
  if (images.length > MAX_TASK_IMAGES) issue(context, 'error', 'too_many_images', { count: images.length, limit: MAX_TASK_IMAGES })
  return images
}

function buildMedia(
  raw: unknown[],
  old: MultiTrackTaskMedia[],
  mediaType: 'video' | 'audio',
  context: ImportContext,
): MultiTrackTaskMedia[] {
  const field = mediaType === 'video' ? 'videos' : 'audios'
  const items: MultiTrackTaskMedia[] = []
  raw.forEach((value, index) => {
    const reference = parseReference(value)
    if (!reference || reference.kind === 'previous_frame' || reference.kind === 'slot') {
      issue(context, 'error', 'invalid_reference', { field, index: index + 1 })
      return
    }
    const flagNames = new Set(['muted', mediaType === 'audio' ? 'fit' : 'soundtrack', ...(reference.kind === 'track' ? [] : ['shared'])])
    warnIgnoredFlags(reference, flagNames, field, context)
    let item: MultiTrackTaskMedia
    if (reference.kind === 'track') {
      // A placeholder only arranges a timeline track's references, so one whose track or clip
      // is missing (a plan from another workflow) has nothing to arrange and is skipped.
      const track = context.tracks.find((candidate) => candidate.id === reference.value)
      if (!track) {
        issue(context, 'warning', 'unknown_track', { track: reference.value })
        return
      }
      if (track.type !== mediaType) {
        issue(context, 'error', 'track_type', { track: reference.value, field })
        return
      }
      if (reference.clip && h3SharedReferenceClip(track)?.id !== reference.clip) {
        issue(context, 'warning', 'unknown_clip', { clip: reference.clip, track: reference.value })
        return
      }
      const placeholder: MultiTrackTaskMedia = {
        id: uuid(), source_type: H3_TRACK_SOURCE, track_id: reference.value,
        ...(reference.clip ? { segment_id: reference.clip } : {}),
      }
      const key = h3PlaceholderKey(placeholder)
      item = old.find((candidate) => candidate.source_type === H3_TRACK_SOURCE && h3PlaceholderKey(candidate) === key)
        ?? placeholder
      // A placeholder's muted overrides a default, so unset and false differ.
      item = setOptional(item, 'muted', typeof reference.flags.muted === 'boolean' ? reference.flags.muted : undefined)
    } else {
      const fields = sourceFields(reference, mediaType, context)
      if (!fields) return
      const candidate = { id: uuid(), ...fields } as MultiTrackTaskMedia
      item = old.find((entry) => entry.source_type !== H3_TRACK_SOURCE && taskMediaIdentity(entry) === taskMediaIdentity(candidate))
        ?? candidate
      item = setSwitch(item, 'muted', reference.flags.muted === true)
      item = setSwitch(item, 'shared_reference', reference.flags.shared === true, 'shared_reference_copy')
    }
    item = setOptional(item, 'fit', mediaType === 'audio' && typeof reference.flags.fit === 'boolean' ? reference.flags.fit : undefined)
    item = setOptional(item, 'soundtrack', mediaType === 'video' && typeof reference.flags.soundtrack === 'boolean'
      ? reference.flags.soundtrack : undefined)
    items.push(item)
  })
  const active = items.filter((item) => item.muted !== true).length
  if (active > H3_MEDIA_REFERENCE_LIMIT) issue(context, 'warning', 'over_limit', { field, count: active, limit: H3_MEDIA_REFERENCE_LIMIT })
  return items
}

function buildContent(
  old: MultiTrackSegmentContent | undefined,
  task: H3PlanTask,
  context: ImportContext,
): MultiTrackSegmentContent {
  let content: MultiTrackSegmentContent = old ? { ...old } : {
    media_type: 'none', task_mode: 'default', continuity_mode: 'shot', images: [], user_prompt: '',
  }
  if (task.mode !== undefined) {
    if ((MULTITRACK_TASK_MODES as readonly string[]).includes(task.mode)) content.task_mode = task.mode
    else issue(context, 'error', 'invalid_mode', { value: String(task.mode), allowed: MULTITRACK_TASK_MODES.join(', ') })
  }
  if (task.continuity !== undefined) {
    if ((MULTITRACK_CONTINUITY_MODES as readonly string[]).includes(task.continuity)) {
      if (task.continuity !== (content.continuity_mode ?? 'shot')) content = { ...content, ...continuityModeChangePatch(task.continuity) }
    } else {
      issue(context, 'error', 'invalid_continuity', { value: String(task.continuity), allowed: MULTITRACK_CONTINUITY_MODES.join(', ') })
    }
  }
  const drift = content.continuity_mode === DRIFT_TAPER_CONTINUITY_MODE
  if (task.seam !== undefined) {
    const seam = parseSeam(task.seam)
    if (!seam) issue(context, 'error', 'invalid_seam')
    else if (!drift) issue(context, 'warning', 'seam_ignored')
    else content = { ...content, ...seamPatch(seam) }
  } else if (drift && old?.continuity_mode !== DRIFT_TAPER_CONTINUITY_MODE) {
    // A task the plan makes Drift, new or not, starts from the plan's seam.
    content = { ...content, ...seamPatch(SEAM_PRESETS[PLAN_DEFAULT_SEAM]) }
  }
  if (task.ref_image_size !== undefined) {
    if ((MULTITRACK_REF_IMAGE_SIZES as readonly string[]).includes(task.ref_image_size)) content.ref_image_size = task.ref_image_size
    else issue(context, 'error', 'invalid_ref_image_size', { value: String(task.ref_image_size), allowed: MULTITRACK_REF_IMAGE_SIZES.join(', ') })
  }
  if (task.prompt !== undefined) {
    if (typeof task.prompt !== 'string') issue(context, 'error', 'invalid_prompt')
    else if (task.prompt !== getSelectedTaskUserPrompt(content)) content = { ...content, ...getSelectedTaskUserPromptPatch(content, task.prompt) }
  }
  for (const [field, mediaType] of REFERENCE_FIELDS) {
    const list = task[field]
    if (list === undefined) continue
    if (!Array.isArray(list)) {
      issue(context, 'error', 'invalid_list', { field })
      continue
    }
    if (mediaType === 'image') {
      content = { ...content, images: buildImages(list, old?.images ?? [], context) }
      continue
    }
    const items = buildMedia(list, old?.[field as 'videos' | 'audios'] ?? [], mediaType, context)
    // An empty list on a task that never had one stays absent, as the Editor stores it.
    if (items.length > 0 || content[field as 'videos' | 'audios'] !== undefined) content = { ...content, [field]: items }
  }
  return content
}

function referenceCounts(data: TrackData, segment: MultiTrackSegment): { images: number; videos: number; audios: number; labels: ReturnType<typeof h3ReferenceLabels>; sources: Record<string, string> } {
  const plan = planH3TaskReferences(data.tracks, segment, h3NextTaskStart(orderedTasks(data), segment), { frameRate: data.frame_rate })
  const labels = h3ReferenceLabels(plan)
  return {
    images: plan.images.length, videos: plan.videos.length, audios: plan.audios.length, labels,
    sources: labelSources(data, segment.content.task_mode ?? 'default', plan, labels),
  }
}

function imageSource(image: MultiTrackTaskImage): string {
  if (image.source_type === 'previous_frame') return "previous task's last frame"
  const name = image.file_name || image.file_path || image.local_path || image.url || image.slot_name || ''
  return image.shared_reference === true ? `${name} (shared)` : name
}

function mediaSource(data: TrackData, reference: H3MediaReference): string {
  if (reference.kind === 'upload') {
    const { file_name, file_path, local_path, url } = reference.source
    return file_name || file_path || local_path || url || ''
  }
  const end = reference.start_frame + reference.frames
  const clips = (data.tracks.find((track) => track.id === reference.track_id)?.segments ?? []).filter((clip) => (
    reference.kind === 'shared' ? clip.id === reference.segment_id : clip.end_frame > reference.start_frame && clip.start_frame < end
  ))
  const names = clips.map((clip) => clip.content.file_name || clip.content.file_path || '').filter(Boolean).join(', ')
  const role = reference.kind === 'shared' ? 'shared clip' : reference.kind === 'locked' ? 'locked track' : 'track window'
  return `${names} (${reference.media_type} track ${reference.track_id}, ${role})`
}

/**
 * What each prompt label stands for, for agents writing prompts. Default and L2V tasks take
 * only frames: the first and last image (L2V: the last), so other images and media get none.
 */
function labelSources(
  data: TrackData,
  mode: MultiTrackTaskMode,
  plan: H3TaskReferencePlan,
  labels: ReturnType<typeof h3ReferenceLabels>,
): Record<string, string> {
  if (mode === 'passthrough') return {}
  const images = plan.images.map(imageSource)
  if (mode !== 'ref' && mode !== 'edit') {
    const last = images.length - 1
    return Object.fromEntries(images.map((name, index) => {
      const role = index === last && (index > 0 || mode === 'l2v') ? 'last frame'
        : index === 0 && mode !== 'l2v' ? 'first frame' : `unused in ${mode}`
      return [`<${labels.images[index]}>`, `${role}: ${name}`]
    }))
  }
  const sources: Record<string, string> = {}
  images.forEach((name, index) => { sources[`<${labels.images[index]}>`] = name })
  plan.videos.forEach((video, index) => { sources[`<${labels.videos[index]}>`] = mediaSource(data, video) })
  labels.video_soundtracks.forEach((label, index) => {
    if (label) sources[`<${label}>`] = `soundtrack of <${labels.videos[index]}>`
  })
  plan.audios.forEach((audio, index) => { sources[`<${labels.audios[index]}>`] = mediaSource(data, audio) })
  return sources
}

function promptTagIssues(prompt: string, labels: ReturnType<typeof h3ReferenceLabels>, context: ImportContext) {
  const known = new Set([...labels.images, ...labels.videos, ...labels.audios,
    ...labels.video_soundtracks.filter((label): label is string => label !== null)])
  const missing = new Set<string>()
  for (const match of prompt.matchAll(PROMPT_TAG)) {
    const label = `${match[1]} ${match[2]}`
    if (!known.has(label)) missing.add(label)
  }
  for (const label of missing) issue(context, 'warning', 'prompt_reference', { tag: `<${label}>` })
}

function sameSegment(left: MultiTrackSegment, right: MultiTrackSegment): boolean {
  return left.start_frame === right.start_frame && left.end_frame === right.end_frame
    && JSON.stringify(left.content) === JSON.stringify(right.content)
}

// ---------------------------------------------------------------------------
// Timeline tracks
// ---------------------------------------------------------------------------

/** Resolve `at` (a task's start) or `until` (its end) to a frame: a task id, or a task's 1-based position in the file. */
type ClipAnchor = (at: string | number, edge?: 'end') => number | undefined

function readFlag(value: unknown, flag: string, context: ImportContext): boolean {
  if (value === undefined || typeof value === 'boolean') return value === true
  issue(context, 'error', 'invalid_flag', { flag })
  return false
}

function readVolume(value: unknown, context: ImportContext): number {
  if (value === undefined) return MULTITRACK_DEFAULT_VOLUME_DB
  if (typeof value === 'number' && Number.isFinite(value)) return value
  issue(context, 'error', 'invalid_volume')
  return MULTITRACK_DEFAULT_VOLUME_DB
}

/** "reference" on a video track; "output", or nothing, keeps a locked video's sound as the task's audio. */
function readSoundtrack(track: H3PlanTrack, context: ImportContext): 'reference' | undefined {
  const value: unknown = track.soundtrack
  if (value === undefined || value === 'output') return undefined
  if (value === 'reference' && track.type === 'video') return 'reference'
  issue(context, 'error', 'invalid_soundtrack')
  return undefined
}

function withoutSoundtrack({ soundtrack: _soundtrack, ...track }: MultiTrack): Omit<MultiTrack, 'soundtrack'> {
  return track
}

function readSeconds(value: unknown, field: string, context: ImportContext): number | undefined | null {
  if (value === undefined) return undefined
  if (typeof value === 'number' && Number.isFinite(value) && (field === 'from' || value >= 0)) return value
  issue(context, 'error', 'invalid_clip_time', { field })
  return null
}

function sourceIdentity(content: MultiTrackSegmentContent | Partial<MultiTrackTaskImage>): string {
  return taskMediaIdentity({ id: '', ...content } as MultiTrackTaskMedia)
}

function anchorFrame(value: string | number, anchor: ClipAnchor, context: ImportContext, edge?: 'end'): number | null {
  const valid = (typeof value === 'string' && value) || (typeof value === 'number' && Number.isInteger(value) && value > 0)
  const frame = valid ? anchor(value, edge) : undefined
  if (frame === undefined) issue(context, 'error', 'unknown_anchor', { at: String(value) })
  return frame ?? null
}

function clipStart(clip: H3PlanClip, anchor: ClipAnchor, fps: number, context: ImportContext): number | undefined | null {
  if (clip.at !== undefined) return anchorFrame(clip.at, anchor, context)
  const start = readSeconds(clip.start, 'start', context)
  return typeof start === 'number' ? Math.round(start * fps) : start
}

/** The end `until` sets, after checking that the clip's source reaches it. */
function clipUntil(
  clip: H3PlanClip, start: number, sourceEnd: number | undefined, anchor: ClipAnchor, fps: number, context: ImportContext,
): number | null {
  const end = anchorFrame(clip.until!, anchor, context, 'end')
  if (end === null) return null
  if (end <= start) {
    issue(context, 'error', 'clip_end')
    return null
  }
  if (sourceEnd !== undefined && end > sourceEnd) {
    issue(context, 'error', 'clip_until_short', { at: String(clip.until), end: round3(sourceEnd / fps) })
    return null
  }
  return end
}

/** One clip of `tracks`, or null after reporting why it cannot be placed. */
function buildClip(
  clip: H3PlanClip,
  track: MultiTrack & { type: 'video' | 'audio' },
  oldClips: ReadonlyMap<string, MultiTrackSegment>,
  anchor: ClipAnchor,
  fps: number,
  context: ImportContext,
): MultiTrackSegment | null {
  for (const key of Object.keys(clip)) if (!CLIP_KEYS.has(key)) issue(context, 'warning', 'unknown_field', { field: key })
  const source = parseClipSource(clip)
  const old = clip.id ? oldClips.get(clip.id) : undefined
  if (source === undefined || (source === null && !clip.id)) {
    issue(context, 'error', 'invalid_clip')
    return null
  }
  if ((clip.at !== undefined && (clip.start !== undefined || clip.end !== undefined))
    || (clip.until !== undefined && clip.end !== undefined)) {
    issue(context, 'error', 'clip_timing')
    return null
  }
  const start = clipStart(clip, anchor, fps, context)
  if (start === null) return null
  const volume = clip.volume === undefined ? undefined : readVolume(clip.volume, context)

  if (source === null) {
    // An existing clip, moved by `at` or `start`; settings it does not write are kept.
    if (!old) {
      issue(context, 'error', 'unknown_clip_id', { id: clip.id! })
      return null
    }
    if (old.content.media_type !== track.type || clip.end !== undefined || clip.from !== undefined) {
      issue(context, 'error', old.content.media_type !== track.type ? 'clip_type' : 'clip_timing')
      return null
    }
    const startFrame = start ?? old.start_frame
    const shift = startFrame - old.start_frame
    const origin = old.origin_start_frame === undefined ? undefined : old.origin_start_frame + shift
    let endFrame = old.end_frame + shift
    if (clip.until !== undefined) {
      const duration = old.content.duration
      const sourceEnd = typeof duration === 'number' && duration > 0 ? (origin ?? startFrame) + Math.round(duration * fps) : undefined
      const until = clipUntil(clip, startFrame, sourceEnd, anchor, fps, context)
      if (until === null) return null
      endFrame = until
    }
    return {
      ...old,
      start_frame: startFrame,
      end_frame: endFrame,
      ...(origin === undefined ? {} : { origin_start_frame: origin }),
      content: {
        ...old.content,
        ...(clip.muted !== undefined ? { muted: readFlag(clip.muted, 'muted', context) } : {}),
        ...(volume !== undefined ? { volume_db: volume } : {}),
        ...(clip.shared !== undefined ? { shared_reference: readFlag(clip.shared, 'shared', context) } : {}),
      },
    }
  }

  // A clip with a source is described in full: unwritten settings take their defaults.
  if (start === undefined) {
    issue(context, 'error', 'clip_timing')
    return null
  }
  const fields = sourceFields(source, track.type, context)
  if (!fields) return null
  const request = mediaRequest(source, track.type)
  const resolved = request ? context.media.get(h3PlanMediaKey(request)) : undefined
  const probed = resolved && !('error' in resolved) ? resolved : undefined
  const sameSource = old !== undefined && old.content.media_type === track.type && sourceIdentity(old.content) === sourceIdentity(fields)
  const duration = probed?.duration ?? (sameSource ? old!.content.duration : undefined)
  const from = readSeconds(clip.from, 'from', context)
  const end = readSeconds(clip.end, 'end', context)
  if (from === null || end === null) return null
  const fromFrames = Math.round((from ?? 0) * fps)
  let endFrame: number
  if (clip.until !== undefined) {
    const sourceEnd = typeof duration === 'number' && duration > 0 ? start + Math.round(duration * fps) - fromFrames : undefined
    const until = clipUntil(clip, start, sourceEnd, anchor, fps, context)
    if (until === null) return null
    endFrame = until
  } else if (end !== undefined) {
    endFrame = Math.round(end * fps)
  } else if (typeof duration === 'number' && duration > 0) {
    // Placed without an end, a clip takes the rest of its file.
    endFrame = start + Math.max(1, Math.round(duration * fps) - fromFrames)
  } else {
    issue(context, 'error', 'clip_duration', { source: source.value })
    return null
  }
  if (endFrame <= start) {
    issue(context, 'error', 'clip_end')
    return null
  }
  const base: MultiTrackSegmentContent = sameSource ? { ...old!.content } : {
    media_type: track.type,
    ...fields,
    ...(typeof duration === 'number' && duration > 0 ? { duration } : {}),
    ...(track.type === 'video' && typeof probed?.has_audio === 'boolean' ? { has_audio: probed.has_audio } : {}),
  }
  // Spreading the existing clip keeps its key order, which the Editor's lock check compares.
  const segment: MultiTrackSegment = {
    ...old,
    id: clip.id ?? uuid(),
    start_frame: start,
    end_frame: endFrame,
    color: old?.color ?? track.color,
    content: {
      ...base,
      muted: readFlag(clip.muted, 'muted', context),
      volume_db: volume ?? MULTITRACK_DEFAULT_VOLUME_DB,
      shared_reference: readFlag(clip.shared, 'shared', context),
    },
  }
  // The Editor leaves the source origin unset until a clip is trimmed.
  const originStart = start - fromFrames
  if (originStart !== start || old?.origin_start_frame !== undefined) segment.origin_start_frame = originStart
  else delete segment.origin_start_frame
  return segment
}

/** The video and audio tracks `tracks` describes, in its order, or null when the plan leaves them out. */
function buildMediaTracks(
  current: TrackData,
  plan: H3PlanFile,
  media: ReadonlyMap<string, H3PlanMediaResult>,
  anchor: ClipAnchor,
  issues: H3PlanIssue[],
): MultiTrack[] | null {
  if (plan.tracks === undefined) return null
  if (!Array.isArray(plan.tracks)) {
    issues.push({ level: 'error', code: 'invalid_list', params: { field: 'tracks' } })
    return null
  }
  const fps = current.frame_rate
  const oldTracks = current.tracks.filter(isMediaTrack)
  const oldClips = new Map(oldTracks.flatMap((track) => track.segments.map((segment) => [segment.id, segment] as const)))
  const trackIds = new Set<string>()
  const clipIds = new Set<string>()
  const tracks: MultiTrack[] = []
  plan.tracks.forEach((raw: unknown, trackIndex) => {
    const context: ImportContext = { at: { track: trackIndex + 1 }, tracks: current.tracks, media, issues }
    const planTrack = raw as H3PlanTrack
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || (planTrack.type !== 'video' && planTrack.type !== 'audio')) {
      issue(context, 'error', 'invalid_track')
      return
    }
    for (const key of Object.keys(planTrack)) if (!TRACK_KEYS.has(key)) issue(context, 'warning', 'unknown_field', { field: key })
    if (planTrack.id !== undefined && (typeof planTrack.id !== 'string' || !planTrack.id)) {
      issue(context, 'error', 'invalid_id')
      return
    }
    if (planTrack.id && trackIds.has(planTrack.id)) {
      issue(context, 'error', 'duplicate_track_id', { id: planTrack.id })
      return
    }
    if (planTrack.id) trackIds.add(planTrack.id)
    const old = planTrack.id ? current.tracks.find((track) => track.id === planTrack.id) : undefined
    if (old && old.type !== planTrack.type) {
      issue(context, 'error', 'track_type_changed', { id: old.id, type: planTrack.type })
      return
    }
    if (!Array.isArray(planTrack.clips)) {
      issue(context, 'error', 'invalid_list', { field: 'clips' })
      return
    }
    const base = old ? withoutSoundtrack(old) : {
      id: planTrack.id ?? uuid(), name: '', color: MULTITRACK_TRACK_COLORS[planTrack.type], solo: false, locked: false,
    }
    const soundtrack = readSoundtrack(planTrack, context)
    const track = {
      ...base,
      type: planTrack.type,
      // A track is described in full: unwritten settings take their defaults.
      audio_locked: readFlag(planTrack.audio_locked, 'audio_locked', context),
      ...(soundtrack ? { soundtrack } : {}),
      muted: readFlag(planTrack.muted, 'muted', context),
      volume_db: readVolume(planTrack.volume, context),
      segments: [] as MultiTrackSegment[],
    }
    const placed: { segment: MultiTrackSegment; number: number }[] = []
    planTrack.clips.forEach((rawClip: unknown, clipIndex) => {
      const clipContext: ImportContext = { ...context, at: { track: trackIndex + 1, clip: clipIndex + 1 } }
      const clip = rawClip as H3PlanClip
      if (!rawClip || typeof rawClip !== 'object' || Array.isArray(rawClip)) {
        issue(clipContext, 'error', 'invalid_clip')
        return
      }
      if (clip.id !== undefined && (typeof clip.id !== 'string' || !clip.id)) {
        issue(clipContext, 'error', 'invalid_id')
        return
      }
      if (clip.id && clipIds.has(clip.id)) {
        issue(clipContext, 'error', 'duplicate_clip_id', { id: clip.id })
        return
      }
      if (clip.id) clipIds.add(clip.id)
      const segment = buildClip(clip, track, oldClips, anchor, fps, clipContext)
      if (segment) placed.push({ segment, number: clipIndex + 1 })
    })
    placed.sort((a, b) => a.segment.start_frame - b.segment.start_frame)
    placed.forEach((entry, index) => {
      const next = placed[index + 1]
      if (next && next.segment.start_frame < entry.segment.end_frame) {
        issue(context, 'error', 'clip_overlap', { first: Math.min(entry.number, next.number), second: Math.max(entry.number, next.number) })
      }
    })
    const shared = placed.filter((entry) => entry.segment.content.shared_reference === true).length
    if (shared > 1) issue(context, 'error', 'shared_clips', { count: shared })
    tracks.push({ ...track, segments: placed.map((entry) => entry.segment) })
  })
  for (const type of ['video', 'audio'] as const) {
    const ofType = tracks.filter((track) => track.type === type)
    if (ofType.length > MULTITRACK_MEDIA_TRACK_LIMIT) {
      issues.push({ level: 'error', code: 'too_many_tracks', params: { type, count: ofType.length, limit: MULTITRACK_MEDIA_TRACK_LIMIT } })
    }
    const locked = ofType.filter((track) => track.audio_locked).length
    if (locked > 1) issues.push({ level: 'error', code: 'locked_tracks', params: { type, count: locked } })
  }
  // The Editor never deletes its first video track, so a plan without one empties it instead.
  const firstVideo = oldTracks.find((track) => track.type === 'video')
  if (firstVideo && !tracks.some((track) => track.type === 'video')) {
    tracks.unshift({ ...firstVideo, audio_locked: false, muted: false, volume_db: MULTITRACK_DEFAULT_VOLUME_DB, segments: [] })
  }
  return tracks
}

/** Put `media` where the timeline's video and audio tracks were, keeping every other track. */
function withMediaTracks(tracks: MultiTrack[], media: MultiTrack[] | null): MultiTrack[] {
  if (!media) return tracks
  const others = tracks.filter((track) => !isMediaTrack(track))
  const firstMedia = tracks.findIndex(isMediaTrack)
  const at = firstMedia < 0
    ? others.findIndex((track) => track.type === 'task') + 1
    : tracks.slice(0, firstMedia).filter((track) => !isMediaTrack(track)).length
  return [...others.slice(0, at), ...media, ...others.slice(at)]
}

function clipSummary(segment: MultiTrackSegment, fps: number): H3PlanClipSummary {
  const content = segment.content
  return {
    name: content.file_name ?? basename(content.file_path ?? content.local_path ?? content.url ?? content.slot_name ?? segment.id),
    start: round3(segment.start_frame / fps),
    end: round3(segment.end_frame / fps),
  }
}

function trackSignature(track: MultiTrack): string {
  return JSON.stringify([track.audio_locked === true, track.soundtrack ?? 'output', track.muted, track.volume_db,
    [...track.segments].sort((a, b) => a.start_frame - b.start_frame)
      .map((segment) => [segment.id, segment.start_frame, segment.end_frame, segment.origin_start_frame ?? null, segment.content])])
}

function trackRows(before: TrackData, after: TrackData): H3PlanTrackRow[] {
  const fps = after.frame_rate
  const oldTracks = before.tracks.filter(isMediaTrack)
  const newTracks = after.tracks.filter(isMediaTrack)
  const row = (track: MultiTrack & { type: 'video' | 'audio' }, index: number, change: H3PlanChange): H3PlanTrackRow => ({
    id: track.id, index, type: track.type, change, audioLocked: track.audio_locked === true,
    ...(track.soundtrack === 'reference' ? { soundtrack: 'reference' as const } : {}), muted: track.muted,
    clips: [...track.segments].sort((a, b) => a.start_frame - b.start_frame).map((segment) => clipSummary(segment, fps)),
  })
  return [
    ...newTracks.map((track, index) => {
      const old = oldTracks.find((candidate) => candidate.id === track.id)
      return row(track, index + 1, !old ? 'added' : trackSignature(old) === trackSignature(track) ? 'unchanged' : 'modified')
    }),
    ...oldTracks.flatMap((track, index) => newTracks.some((candidate) => candidate.id === track.id) ? [] : [row(track, index + 1, 'removed')]),
  ]
}

const LOCKED_VIDEO_MODES = new Set(['ref', 'edit', 'passthrough'])

/**
 * The Project's checks on a locked video, made before anything runs: a task the video overlaps
 * must use a reference mode, and the video must cover the task's whole generation window,
 * including the context frames a continuation regenerates before it.
 */
function lockedVideoIssues(data: TrackData, tasks: readonly MultiTrackSegment[], fps: number, issues: H3PlanIssue[]): void {
  const locked = data.tracks.filter((track) => track.type === 'video' && track.audio_locked === true)
  tasks.forEach((task, index) => {
    const track = locked.find((item) => item.segments.some((segment) => segment.content.media_type === 'video'
      && segment.start_frame < task.end_frame && segment.end_frame > task.start_frame))
    if (!track) return
    if (!LOCKED_VIDEO_MODES.has(task.content.task_mode ?? 'default')) {
      issues.push({ level: 'error', task: index + 1, code: 'locked_video_mode' })
      return
    }
    const start = index > 0 && isH3Continuation(task) ? task.start_frame - H3_NATIVE_CONTEXT_FRAMES : task.start_frame
    let covered = start
    for (const segment of [...track.segments].sort((a, b) => a.start_frame - b.start_frame)) {
      if (segment.start_frame <= covered) covered = Math.max(covered, segment.end_frame)
    }
    if (covered < task.end_frame) {
      issues.push({ level: 'error', task: index + 1, code: 'locked_video_range', params: {
        start: round3(start / fps), end: round3(task.end_frame / fps),
      } })
    }
  })
}

/**
 * Build the timeline a plan describes. `prepare` applies the Editor's own normalization and
 * native timing, exactly as a manual edit would; it may throw to reject the result.
 */
export function buildH3PlanImport(
  current: TrackData,
  plan: H3PlanFile,
  media: ReadonlyMap<string, H3PlanMediaResult>,
  prepare: (candidate: TrackData) => TrackData,
): H3PlanImport {
  const issues: H3PlanIssue[] = []
  const taskTrack = taskTrackOf(current)
  if (!taskTrack) return { data: null, rows: [], trackRows: [], issues: [{ level: 'error', code: 'no_task_track' }] }
  if (plan.frame_rate !== undefined && plan.frame_rate !== current.frame_rate) {
    issues.push({ level: 'warning', code: 'frame_rate', params: { file: plan.frame_rate, timeline: current.frame_rate } })
  }
  if ((current.task_markers ?? []).length > 0) issues.push({ level: 'warning', code: 'markers_kept' })
  const fps = current.frame_rate
  const before = orderedTasks(current)
  const existing = new Map(before.map((segment) => [segment.id, segment]))
  // When no id matches, the plan likely comes from another workflow: say so once instead of
  // once per task. An empty task track has nothing to match, so it says nothing.
  const planIds = plan.tasks.flatMap((task) => (
    task && typeof task === 'object' && typeof task.id === 'string' && task.id ? [task.id] : []
  ))
  const foreign = before.length > 0 && planIds.length > 0 && planIds.every((id) => !existing.has(id))
  if (foreign) issues.push({ level: 'warning', code: 'foreign_ids', params: { count: planIds.length } })
  const reportNewIds = before.length > 0 && !foreign
  // Task references check placeholders against the tracks the plan lists. Clips placed with
  // `at` need the tasks' final timing, so this first pass places them anywhere and its
  // problems are reported by the final pass below.
  const draftTracks = withMediaTracks(current.tracks, buildMediaTracks(current, plan, media, (_at, edge) => (edge === 'end' ? 1 : 0), []))
  const seen = new Set<string>()
  const requested = new Map<string, { start: number; end: number }>()
  const positions: (string | undefined)[] = []
  const color = before[0]?.color ?? taskTrack.color
  let cursor = 0
  const segments: MultiTrackSegment[] = []
  plan.tasks.forEach((task, index) => {
    const context: ImportContext = { at: { task: index + 1 }, tracks: draftTracks, media, issues }
    if (!task || typeof task !== 'object' || Array.isArray(task)) {
      issue(context, 'error', 'invalid_task')
      return
    }
    for (const key of Object.keys(task)) if (!TASK_KEYS.has(key)) issue(context, 'warning', 'unknown_field', { field: key })
    if (task.id !== undefined && (typeof task.id !== 'string' || !task.id)) {
      issue(context, 'error', 'invalid_id')
      return
    }
    if (task.id && seen.has(task.id)) {
      issue(context, 'error', 'duplicate_id', { id: task.id })
      return
    }
    if (task.id) seen.add(task.id)
    const old = task.id ? existing.get(task.id) : undefined
    if (task.id && !old && reportNewIds) issue(context, 'warning', 'new_id', { id: task.id })
    if (task.seconds !== undefined && !(typeof task.seconds === 'number' && Number.isFinite(task.seconds) && task.seconds > 0)) {
      issue(context, 'error', 'invalid_seconds')
      return
    }
    if (task.seconds === undefined && !old) {
      issue(context, 'error', 'missing_seconds')
      return
    }
    if (task.gap_before !== undefined && !(typeof task.gap_before === 'number' && Number.isFinite(task.gap_before) && task.gap_before >= 0)) {
      issue(context, 'error', 'invalid_gap')
      return
    }
    const frames = task.seconds !== undefined ? Math.max(1, Math.round(task.seconds * fps)) : old!.end_frame - old!.start_frame
    const start = cursor + Math.round((task.gap_before ?? 0) * fps)
    cursor = start + frames
    const id = task.id ?? uuid()
    requested.set(id, { start, end: cursor })
    positions[index] = id
    segments.push({ id, start_frame: start, end_frame: cursor, color: old?.color ?? color, content: buildContent(old?.content, task, context) })
  })
  if (segments[0] && segments[0].content.continuity_mode !== 'shot' && segments[0].content.task_mode !== 'passthrough') {
    issues.push({ level: 'warning', task: 1, code: 'first_continuity' })
  }
  if (segments.some((segment, index) => index === 0 && segment.content.images?.some((image) => (
    image.source_type === 'previous_frame' && image.muted !== true
  )))) {
    issues.push({ level: 'error', task: 1, code: 'previous_frame_first' })
  }

  const fileContext: ImportContext = { at: {}, tracks: current.tracks, media, issues }
  const master = {
    ...(plan.volume !== undefined ? { volume_db: readVolume(plan.volume, fileContext) } : {}),
    ...(plan.muted !== undefined ? { muted: readFlag(plan.muted, 'muted', fileContext) } : {}),
  }
  let data: TrackData | null = null
  try {
    // Native timing settles the tasks first; then clips placed at a task's start can be placed.
    const timed = prepare({
      ...current,
      ...master,
      tracks: draftTracks.map((track) => (track.id === taskTrack.id ? { ...track, segments } : track)),
    })
    const timedTasks = new Map(orderedTasks(timed).map((segment) => [segment.id, segment]))
    const anchor: ClipAnchor = (at, edge) => {
      const segment = timedTasks.get((typeof at === 'number' ? positions[at - 1] : at) ?? '')
      return edge === 'end' ? segment?.end_frame : segment?.start_frame
    }
    const mediaTracks = buildMediaTracks(current, plan, media, anchor, issues)
    data = mediaTracks ? prepare({ ...timed, tracks: withMediaTracks(timed.tracks, mediaTracks) }) : timed
  } catch (cause: unknown) {
    issues.push({ level: 'error', code: 'native', params: { message: cause instanceof Error ? cause.message : String(cause) } })
  }

  const rows: H3PlanRow[] = []
  if (data) {
    // Tasks past the locked audio's end have no locked sound to follow there. A locked video
    // that ends early is intended when it ends on a task boundary; otherwise the check below fails.
    const after = orderedTasks(data)
    const tasksEnd = after.at(-1)?.end_frame ?? 0
    const lockedAudio = data.tracks.find((track) => track.type === 'audio' && track.audio_locked === true && track.segments.length > 0)
    const lockedAudioEnd = lockedAudio ? Math.max(...lockedAudio.segments.map((segment) => segment.end_frame)) : Infinity
    if (tasksEnd > lockedAudioEnd) {
      issues.push({ level: 'warning', code: 'past_locked_audio', params: { tasks: round3(tasksEnd / fps), end: round3(lockedAudioEnd / fps) } })
    }
    lockedVideoIssues(data, after, fps, issues)
    after.forEach((segment, index) => {
      const context: ImportContext = { at: { task: index + 1 }, tracks: data!.tracks, media, issues }
      const wanted = requested.get(segment.id)
      if (wanted && (wanted.start !== segment.start_frame || wanted.end !== segment.end_frame)) {
        issue(context, 'warning', 'timing_adjusted', {
          start: round3(segment.start_frame / fps), end: round3(segment.end_frame / fps),
          seconds: round3((segment.end_frame - segment.start_frame) / fps),
        })
      }
      const counts = referenceCounts(data!, segment)
      promptTagIssues(getSelectedTaskUserPrompt(segment.content), counts.labels, context)
      const old = existing.get(segment.id)
      rows.push({
        id: segment.id,
        index: index + 1,
        change: !old ? 'added' : sameSegment(old, segment) ? 'unchanged' : 'modified',
        seconds: round3((segment.end_frame - segment.start_frame) / fps),
        ...(old && old.end_frame - old.start_frame !== segment.end_frame - segment.start_frame
          ? { beforeSeconds: round3((old.end_frame - old.start_frame) / fps) } : {}),
        mode: segment.content.task_mode ?? 'default',
        continuity: segment.content.continuity_mode ?? 'shot',
        images: counts.images,
        videos: counts.videos,
        audios: counts.audios,
        prompt: getSelectedTaskUserPrompt(segment.content),
        labels: counts.sources,
      })
    })
  }
  before.forEach((segment, index) => {
    if (seen.has(segment.id)) return
    rows.push({
      id: segment.id, index: index + 1, change: 'removed',
      seconds: round3((segment.end_frame - segment.start_frame) / fps),
      mode: segment.content.task_mode ?? 'default', continuity: segment.content.continuity_mode ?? 'shot',
      images: 0, videos: 0, audios: 0, prompt: getSelectedTaskUserPrompt(segment.content),
    })
  })
  const blocked = issues.some((item) => item.level === 'error')
  return { data: blocked ? null : data, rows, trackRows: data && plan.tracks !== undefined ? trackRows(current, data) : [], issues }
}

export interface H3PlanIssueGroup {
  level: H3PlanIssue['level']
  code: string
  params?: H3PlanIssue['params']
  /** 1-based task numbers in ascending order; empty for the whole file or a track. */
  tasks: number[]
  track?: number
  clip?: number
}

/** Merge issues that say the same thing about several tasks, in the order each first appears. */
export function groupH3PlanIssues(issues: readonly H3PlanIssue[]): H3PlanIssueGroup[] {
  const groups = new Map<string, H3PlanIssueGroup>()
  for (const item of issues) {
    const key = JSON.stringify([item.level, item.code, item.params ?? null, item.track ?? null, item.clip ?? null])
    const group = groups.get(key) ?? {
      level: item.level, code: item.code, ...(item.params ? { params: item.params } : {}), tasks: [],
      ...(item.track !== undefined ? { track: item.track } : {}), ...(item.clip !== undefined ? { clip: item.clip } : {}),
    }
    groups.set(key, group)
    if (item.task !== undefined && !group.tasks.includes(item.task)) group.tasks.push(item.task)
  }
  for (const group of groups.values()) group.tasks.sort((a, b) => a - b)
  return [...groups.values()]
}

/** Task numbers with runs collapsed: [1, 2, 3, 5] → "1–3, 5". */
export function formatH3PlanTaskNumbers(tasks: readonly number[]): string {
  const parts: string[] = []
  for (let index = 0; index < tasks.length;) {
    let end = index
    while (end + 1 < tasks.length && tasks[end + 1] === tasks[end] + 1) end += 1
    parts.push(end > index ? `${tasks[index]}–${tasks[end]}` : String(tasks[index]))
    index = end + 1
  }
  return parts.join(', ')
}
