import { continuityModeChangePatch } from '@/lib/h3-drift-taper'
import {
  H3_MEDIA_REFERENCE_LIMIT,
  H3_TRACK_SOURCE,
  h3NextTaskStart,
  h3PlaceholderKey,
  h3ReferenceLabels,
  h3SharedReferenceClip,
  planH3TaskReferences,
} from '@/lib/h3-references'
import {
  getSelectedTaskUserPrompt,
  getSelectedTaskUserPromptPatch,
  MULTITRACK_CONTINUITY_MODES,
  MULTITRACK_TASK_MODES,
} from '@/lib/multitrack-utils'
import { MAX_TASK_IMAGES, taskImageIdentity } from '@/lib/task-image-utils'
import { taskMediaIdentity } from '@/lib/task-media-utils'
import { uuid } from '@/lib/uuid'
import type {
  MultiTrack,
  MultiTrackContinuityMode,
  MultiTrackSegment,
  MultiTrackSegmentContent,
  MultiTrackTaskImage,
  MultiTrackTaskMedia,
  MultiTrackTaskMode,
  TrackData,
} from '@/types/multitrack'

/**
 * H3 plan files: the task track as JSON that a person or an agent can edit and import.
 *
 * The file lists every task in order. A task with an `id` updates that task and keeps its
 * saved results where nothing relevant changed; a task without one is new; tasks missing
 * from the file are removed. Fields left out of an existing task keep their value. Timeline
 * media tracks, Project settings and segment LoRAs are not part of the plan.
 *
 * References are strings or objects. A string is a local path or URL to copy into ComfyUI's
 * input folder (`file`), or "previous_frame". Objects name the source with one of `file`,
 * `local` (kept in place), `url` (kept as a URL), `input`, `output`, `slot`, `track` (a
 * timeline placeholder, with `clip` for a shared clip) or `previous_frame: true`, plus
 * `muted`, `shared`, `fit` (audio) and `soundtrack` (video).
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

export interface H3PlanTask {
  id?: string
  seconds?: number
  gap_before?: number
  mode?: MultiTrackTaskMode
  continuity?: MultiTrackContinuityMode
  prompt?: string
  images?: H3PlanReference[]
  videos?: H3PlanReference[]
  audios?: H3PlanReference[]
}

export interface H3PlanFile {
  format: typeof H3_PLAN_FORMAT
  version: typeof H3_PLAN_VERSION
  frame_rate?: number
  tasks: H3PlanTask[]
}

export type H3PlanMediaType = 'image' | 'video' | 'audio'

/** A file the server copies or checks: exactly one of file, local, input or output. */
export interface H3PlanMediaRequest {
  media_type: H3PlanMediaType
  file?: string
  local?: string
  input?: string
  output?: string
}

export type H3PlanMediaResult =
  | { source_type: 'input' | 'output' | 'local'; file_path?: string; local_path?: string; file_name: string }
  | { error: string }

export interface H3PlanIssue {
  level: 'error' | 'warning'
  /** 1-based task number in the file; absent for the whole file. */
  task?: number
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
}

export interface H3PlanImport {
  data: TrackData | null
  rows: H3PlanRow[]
  issues: H3PlanIssue[]
}

type ParsedReference =
  | { kind: 'previous_frame'; flags: H3PlanReferenceObject }
  | { kind: 'file' | 'local' | 'url' | 'input' | 'output' | 'slot'; value: string; flags: H3PlanReferenceObject }
  | { kind: 'track'; value: string; clip?: string; flags: H3PlanReferenceObject }

const SOURCE_KEYS = ['file', 'local', 'url', 'input', 'output', 'slot', 'track', 'previous_frame'] as const
const TASK_KEYS = new Set(['id', 'seconds', 'gap_before', 'mode', 'continuity', 'prompt', 'images', 'videos', 'audios'])
const REFERENCE_FIELDS = [['images', 'image'], ['videos', 'video'], ['audios', 'audio']] as const
const PROMPT_TAG = /<(Picture|Video|Audio) (\d+)>/g

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

/** The task track as a plan file; importing it unchanged changes nothing. */
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
      prompt: getSelectedTaskUserPrompt(content),
      images: (content.images ?? []).map(exportImage),
      videos: (content.videos ?? []).map((item) => exportMedia(item, 'video')),
      audios: (content.audios ?? []).map((item) => exportMedia(item, 'audio')),
    }
  })
  return { format: H3_PLAN_FORMAT, version: H3_PLAN_VERSION, frame_rate: fps, tasks }
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
  return { plan: { format: H3_PLAN_FORMAT, version: H3_PLAN_VERSION, frame_rate: file.frame_rate, tasks: file.tasks }, issues: [] }
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
  return [...requests.values()]
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

interface ImportContext {
  taskNumber: number
  tracks: MultiTrack[]
  media: ReadonlyMap<string, H3PlanMediaResult>
  issues: H3PlanIssue[]
}

function issue(context: ImportContext, level: H3PlanIssue['level'], code: string, params?: H3PlanIssue['params']) {
  context.issues.push({ level, task: context.taskNumber, code, ...(params ? { params } : {}) })
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

function referenceCounts(data: TrackData, segment: MultiTrackSegment): { images: number; videos: number; audios: number; labels: ReturnType<typeof h3ReferenceLabels> } {
  const plan = planH3TaskReferences(data.tracks, segment, h3NextTaskStart(orderedTasks(data), segment), { frameRate: data.frame_rate })
  return { images: plan.images.length, videos: plan.videos.length, audios: plan.audios.length, labels: h3ReferenceLabels(plan) }
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
  if (!taskTrack) return { data: null, rows: [], issues: [{ level: 'error', code: 'no_task_track' }] }
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
  const seen = new Set<string>()
  const requested = new Map<string, { start: number; end: number }>()
  const color = before[0]?.color ?? taskTrack.color
  let cursor = 0
  const segments: MultiTrackSegment[] = []
  plan.tasks.forEach((task, index) => {
    const context: ImportContext = { taskNumber: index + 1, tracks: current.tracks, media, issues }
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

  let data: TrackData | null = null
  try {
    data = prepare({
      ...current,
      tracks: current.tracks.map((track) => (track === taskTrack ? { ...track, segments } : track)),
    })
  } catch (cause: unknown) {
    issues.push({ level: 'error', code: 'native', params: { message: cause instanceof Error ? cause.message : String(cause) } })
  }

  const rows: H3PlanRow[] = []
  if (data) {
    const after = orderedTasks(data)
    after.forEach((segment, index) => {
      const context: ImportContext = { taskNumber: index + 1, tracks: data!.tracks, media, issues }
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
  return { data: blocked ? null : data, rows, issues }
}

export interface H3PlanIssueGroup {
  level: H3PlanIssue['level']
  code: string
  params?: H3PlanIssue['params']
  /** 1-based task numbers in ascending order; empty for the whole file. */
  tasks: number[]
}

/** Merge issues that say the same thing about several tasks, in the order each first appears. */
export function groupH3PlanIssues(issues: readonly H3PlanIssue[]): H3PlanIssueGroup[] {
  const groups = new Map<string, H3PlanIssueGroup>()
  for (const item of issues) {
    const key = JSON.stringify([item.level, item.code, item.params ?? null])
    const group = groups.get(key) ?? { level: item.level, code: item.code, ...(item.params ? { params: item.params } : {}), tasks: [] }
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
