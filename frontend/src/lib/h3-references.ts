import type {
  MultiTrack,
  MultiTrackSegment,
  MultiTrackSegmentContent,
  MultiTrackTaskImage,
  MultiTrackTaskMedia,
} from '@/types/multitrack'

/**
 * Per-task H3 reference plan (mirrors utils/h3_references.py; tests/fixtures/h3_references.json
 * checks both). It decides which media a task references and the <Picture N> / <Video N> /
 * <Audio N> numbers the sampler receives.
 *
 * Videos and audios come from the timeline and, in ref and edit tasks, from the task's own
 * `videos` / `audios` lists. Timeline references are shared clips (once per source, in track
 * order) and then one window per overlapping track. A window runs from the task start to the
 * next task's start, or to the task end for the last task. Muted and audio-locked audio tracks
 * give no reference unless the task includes them.
 *
 * A task list holds uploads and placeholders ({source_type: 'track', track_id}, plus segment_id
 * for a shared clip) that place or adjust a timeline reference; a placeholder's `muted` overrides
 * the default (true excludes, false includes a track that gives none by default). Timeline
 * references without a placeholder come first, then the list in its order; at most three videos
 * and three audios are used. Fitted audio covers the task window (timeline) or the task length
 * (shared clips and uploads); unfitted audio takes up to 15 s of its source. An audio-locked
 * video track that reaches the task comes first and drives the video timing; other videos
 * follow. A video has a soundtrack when its track is not muted and its source is not known to be
 * silent (`has_audio: false`), unless the task says otherwise. Each video soundtrack takes an
 * <Audio N> label before the standalone audio.
 */
export type H3ReferenceKind = 'shared' | 'track' | 'locked' | 'upload'

export type H3ReferenceMediaType = 'video' | 'audio'

export interface H3MediaReference {
  key: string
  media_type: H3ReferenceMediaType
  kind: H3ReferenceKind
  track_id: string
  segment_id: string
  start_frame: number
  frames: number
  soundtrack: boolean
  /** Fitted references cover exactly `frames`; others take at most `frames`. */
  fit: boolean
  /** An upload's source fields. */
  source: Partial<Record<(typeof SOURCE_FIELDS)[number], string>>
  /** A video whose source is known to have no audio. */
  silent: boolean
}

export interface H3ReferenceLabels {
  images: string[]
  videos: string[]
  video_soundtracks: Array<string | null>
  audios: string[]
}

export interface H3TaskReferencePlan {
  images: MultiTrackTaskImage[]
  videos: H3MediaReference[]
  audios: H3MediaReference[]
}

/**
 * Why a slot is or is not sent: muted by the task, its track no longer reaches the task,
 * excluded by default (see H3ExclusionReason), or past the three-item limit.
 */
export type H3ReferenceSlotStatus = 'active' | 'muted' | 'uncovered' | 'excluded' | 'over_limit'

/** A muted or audio-locked audio track, which the task can include; a repeated shared source cannot be. */
export type H3ExclusionReason = 'track_muted' | 'audio_locked' | 'duplicate'

export interface H3ReferenceSlot {
  key: string
  media_type: H3ReferenceMediaType
  kind: H3ReferenceKind
  track_id: string
  segment_id: string
  status: H3ReferenceSlotStatus
  /** Why the reference is excluded by default; kept when the task includes it anyway. */
  reason?: H3ExclusionReason
  /** The stored upload or placeholder; timeline references without a placeholder have none. */
  item?: MultiTrackTaskMedia
  reference?: H3MediaReference
}

export interface H3TaskReferenceSlots {
  /** Ref and edit tasks read their own lists; other modes use the timeline alone. */
  customizable: boolean
  video: H3ReferenceSlot[]
  audio: H3ReferenceSlot[]
}

export interface H3ReferencePlanOptions {
  audioOnly?: boolean
  frameRate?: number
}

const SLOT_PREFIX = '__slot__:'
const SOURCE_FIELDS = ['source_type', 'file_path', 'local_path', 'url', 'slot_name', 'file_name'] as const
export const H3_TRACK_SOURCE = 'track'
export const H3_MEDIA_REFERENCE_LIMIT = 3
export const H3_MAX_REFERENCE_SECONDS = 15
const CUSTOM_REFERENCE_MODES = new Set(['ref', 'edit'])

type TaskLike = Pick<MultiTrackSegment, 'start_frame' | 'end_frame'> & { content?: Partial<MultiTrackSegmentContent> }

/** Source identity that deduplicates shared clips (mirrors multitrack_media_identity). */
export function h3MediaIdentity(content: Partial<MultiTrackSegmentContent>): string | null {
  let slot = typeof content.slot_name === 'string' && content.slot_name ? content.slot_name : null
  for (const value of [content.file_path, content.local_path, content.url, content.file_name]) {
    if (!slot && typeof value === 'string' && value.startsWith(SLOT_PREFIX)) slot = value.slice(SLOT_PREFIX.length)
  }
  if (!slot && content.source_type === 'slot' && typeof content.file_name === 'string' && content.file_name) {
    slot = content.file_name
  }
  if (slot) return `slot\u0000${slot.startsWith(SLOT_PREFIX) ? slot.slice(SLOT_PREFIX.length) : slot}`
  const path = content.file_path || content.local_path || content.url || content.file_name
  return path ? `${content.source_type ?? 'input'}\u0000${path}` : null
}

/** The timeline reference key a task-list placeholder stands for. */
export function h3PlaceholderKey(item: MultiTrackTaskMedia): string | null {
  if (item.source_type !== H3_TRACK_SOURCE) return null
  if (typeof item.segment_id === 'string' && item.segment_id) return `shared:${item.segment_id}`
  return typeof item.track_id === 'string' && item.track_id ? `track:${item.track_id}` : null
}

export function h3HasUploadSource(item: MultiTrackTaskMedia): boolean {
  return [item.file_path, item.local_path, item.url, item.slot_name].some((value) => typeof value === 'string' && value)
}

export function h3ReferenceCapFrames(frameRate: number): number {
  return Math.max(1, Math.round(H3_MAX_REFERENCE_SECONDS * frameRate))
}

function mediaSegments(track: MultiTrack): MultiTrackSegment[] {
  return track.segments.filter((segment) => segment.content?.media_type === track.type)
}

/** The track's shared clip; an audio-locked track has none. */
export function h3SharedReferenceClip(track: MultiTrack): MultiTrackSegment | undefined {
  if (track.audio_locked === true) return undefined
  return mediaSegments(track).find((segment) => segment.content.shared_reference === true)
}

function windowClips(track: MultiTrack, start: number, end: number, skip?: MultiTrackSegment): MultiTrackSegment[] {
  return mediaSegments(track).filter((segment) => segment !== skip && segment.end_frame > start && segment.start_frame < end)
}

function windowEnd(track: MultiTrack, start: number, end: number, skip?: MultiTrackSegment): number | null {
  const ends = windowClips(track, start, end, skip).map((segment) => Math.min(segment.end_frame, end))
  return ends.length > 0 ? Math.max(...ends) : null
}

/** Whether a source may carry audio; only `has_audio: false` marks it silent. */
export function h3HasAudio(content: { has_audio?: boolean }): boolean {
  return content.has_audio !== false
}

function windowHasAudio(track: MultiTrack, start: number, end: number, skip?: MultiTrackSegment): boolean {
  return windowClips(track, start, end, skip).some((segment) => h3HasAudio(segment.content))
}

function exclusionReason(track: MultiTrack): H3ExclusionReason | undefined {
  if (track.type !== 'audio') return undefined
  if (track.audio_locked === true) return 'audio_locked'
  return track.muted === true ? 'track_muted' : undefined
}

export function h3LockedVideoTrack(tracks: readonly MultiTrack[], start: number, end: number): MultiTrack | undefined {
  return tracks.find((track) => track.type === 'video' && track.audio_locked === true
    && windowEnd(track, start, end) !== null)
}

function uploadSource(item: MultiTrackTaskMedia): H3MediaReference['source'] {
  return Object.fromEntries(SOURCE_FIELDS.flatMap((field) => {
    const value = item[field]
    return typeof value === 'string' && value ? [[field, value]] : []
  }))
}

function slotFor(reference: H3MediaReference, status: H3ReferenceSlotStatus, item?: MultiTrackTaskMedia): H3ReferenceSlot {
  return {
    key: reference.key, media_type: reference.media_type, kind: reference.kind, track_id: reference.track_id,
    segment_id: reference.segment_id, status, item, reference,
  }
}

/** Status of a placeholder whose timeline reference is not sent; null drops it. */
function unresolvedSlot(
  tracks: readonly MultiTrack[],
  item: MultiTrackTaskMedia,
  key: string,
  mediaType: H3ReferenceMediaType,
): H3ReferenceSlot | null {
  const track = tracks.find((candidate) => candidate.id === item.track_id && candidate.type === mediaType)
  if (!track) return null
  const reason = exclusionReason(track)
  const slot = {
    key, media_type: mediaType, kind: item.segment_id ? 'shared' as const : 'track' as const, track_id: track.id,
    segment_id: item.segment_id ?? '', item,
  }
  if (item.segment_id) {
    const clip = h3SharedReferenceClip(track)
    if (clip?.id !== item.segment_id) return null
    return { ...slot, status: 'excluded', reason: reason && item.muted !== false ? reason : 'duplicate' }
  }
  return reason && item.muted !== false ? { ...slot, status: 'excluded', reason } : { ...slot, status: 'uncovered' }
}

/** Every reference slot the Editor shows for a task, in plan order, with why it is or is not sent. */
export function h3TaskReferenceSlots(
  tracks: readonly MultiTrack[],
  task: TaskLike,
  nextStart: number | null = null,
  { audioOnly = false, frameRate = 24 }: H3ReferencePlanOptions = {},
): H3TaskReferenceSlots {
  const content = task.content ?? {}
  const start = task.start_frame
  const taskEnd = Math.max(start, task.end_frame)
  const taskFrames = taskEnd - start
  const window = nextStart !== null ? Math.max(start, nextStart) : taskEnd
  const cap = h3ReferenceCapFrames(frameRate)
  const customizable = CUSTOM_REFERENCE_MODES.has(content.task_mode ?? 'default')
  const items: Record<H3ReferenceMediaType, MultiTrackTaskMedia[]> = {
    video: customizable && Array.isArray(content.videos) ? content.videos : [],
    audio: customizable && Array.isArray(content.audios) ? content.audios : [],
  }
  const placed: Record<H3ReferenceMediaType, Map<string, MultiTrackTaskMedia>> = { video: new Map(), audio: new Map() }
  for (const mediaType of ['video', 'audio'] as const) {
    for (const item of items[mediaType]) {
      const key = h3PlaceholderKey(item)
      if (key !== null && !placed[mediaType].has(key)) placed[mediaType].set(key, item)
    }
  }
  const fits = (mediaType: H3ReferenceMediaType, key: string) => mediaType === 'video' || placed[mediaType].get(key)?.fit !== false
  const included = (mediaType: H3ReferenceMediaType, key: string) => placed[mediaType].get(key)?.muted === false

  const shared: Record<H3ReferenceMediaType, H3MediaReference[]> = { video: [], audio: [] }
  const windows: Record<H3ReferenceMediaType, H3MediaReference[]> = { video: [], audio: [] }
  const seen: Record<H3ReferenceMediaType, Set<string>> = { video: new Set(), audio: new Set() }
  // Display only: why a reference is excluded by default, and the excluded ones without a placeholder.
  const reasons = new Map<string, H3ExclusionReason>()
  const dormant: Record<H3ReferenceMediaType, H3ReferenceSlot[]> = { video: [], audio: [] }
  const rest = (mediaType: H3ReferenceMediaType, key: string, kind: 'shared' | 'track', track: MultiTrack,
    segmentId: string, reason: H3ExclusionReason) => {
    if (!placed[mediaType].has(key)) {
      dormant[mediaType].push({
        key, media_type: mediaType, kind, track_id: track.id, segment_id: segmentId, status: 'excluded', reason,
      })
    }
  }
  for (const track of tracks) {
    if (track.type !== 'video' && track.type !== 'audio') continue
    const mediaType = track.type
    const muted = track.muted === true
    // Muted and audio-locked audio tracks reference only when the task includes them.
    const reason = exclusionReason(track)
    const clip = h3SharedReferenceClip(track)
    if (clip) {
      const identity = h3MediaIdentity(clip.content)
      const key = `shared:${clip.id || track.id}`
      if (reason && !included(mediaType, key)) {
        rest(mediaType, key, 'shared', track, clip.id, reason)
      } else if (identity !== null && seen[mediaType].has(identity)) {
        rest(mediaType, key, 'shared', track, clip.id, 'duplicate')
      } else {
        if (identity !== null) seen[mediaType].add(identity)
        if (reason) reasons.set(key, reason)
        const fit = fits(mediaType, key)
        const silent = mediaType === 'video' && !h3HasAudio(clip.content)
        shared[mediaType].push({
          key, media_type: mediaType, kind: 'shared', track_id: track.id, segment_id: clip.id, start_frame: 0,
          frames: fit ? taskFrames : cap, soundtrack: mediaType === 'video' && !muted && !silent, fit, source: {}, silent,
        })
      }
    }
    const key = `track:${track.id}`
    const fit = fits(mediaType, key)
    const limit = fit ? window : Math.max(window, start + cap)
    const mediaEnd = windowEnd(track, start, limit, clip)
    if (mediaEnd === null || mediaEnd <= start) continue
    if (reason && !included(mediaType, key)) {
      rest(mediaType, key, 'track', track, '', reason)
      continue
    }
    if (reason) reasons.set(key, reason)
    const silent = mediaType === 'video' && !windowHasAudio(track, start, limit, clip)
    windows[mediaType].push({
      key, media_type: mediaType, kind: 'track', track_id: track.id, segment_id: '', start_frame: start,
      frames: mediaEnd - start, soundtrack: mediaType === 'video' && !muted && !silent, fit, source: {}, silent,
    })
  }

  const ordered = (mediaType: H3ReferenceMediaType, skip = '', limit = H3_MEDIA_REFERENCE_LIMIT): H3ReferenceSlot[] => {
    const timeline = [...shared[mediaType], ...windows[mediaType]].filter((reference) => reference.key !== skip)
    const byKey = new Map(timeline.map((reference) => [reference.key, reference]))
    const slots = timeline.filter((reference) => !placed[mediaType].has(reference.key))
      .map((reference) => slotFor(reference, 'active'))
    const used = new Set<string>()
    for (const item of items[mediaType]) {
      if (item.source_type === H3_TRACK_SOURCE) {
        // The first placeholder of a reference counts; later copies are ignored.
        const key = h3PlaceholderKey(item)
        if (key === null || used.has(key)) continue
        used.add(key)
        if (key === skip) continue
        const timelineReference = byKey.get(key)
        if (!timelineReference) {
          const slot = unresolvedSlot(tracks, item, key, mediaType)
          if (slot) slots.push(slot)
          continue
        }
        const reference = mediaType === 'video' && typeof item.soundtrack === 'boolean'
          ? { ...timelineReference, soundtrack: item.soundtrack }
          : timelineReference
        slots.push(slotFor(reference, item.muted === true ? 'muted' : 'active', item))
        continue
      }
      if (!h3HasUploadSource(item)) continue
      const fit = mediaType === 'video' || item.fit === true
      const silent = mediaType === 'video' && !h3HasAudio(item)
      const soundtrack = typeof item.soundtrack === 'boolean' ? item.soundtrack : !silent
      slots.push(slotFor({
        key: `upload:${item.id}`, media_type: mediaType, kind: 'upload', track_id: '', segment_id: '', start_frame: 0,
        frames: fit ? taskFrames : cap, soundtrack: mediaType === 'video' && soundtrack, fit,
        source: uploadSource(item), silent,
      }, item.muted === true ? 'muted' : 'active', item))
    }
    slots.push(...dormant[mediaType].filter((slot) => slot.key !== skip))
    let active = 0
    return slots.map((slot) => {
      const reason = slot.reason ?? reasons.get(slot.key)
      const status = slot.status === 'active' && ++active > limit ? 'over_limit' : slot.status
      return { ...slot, status, ...(reason ? { reason } : {}) }
    })
  }

  const locked = audioOnly ? undefined : h3LockedVideoTrack(tracks, start, taskEnd)
  if (!locked) return { customizable, video: ordered('video'), audio: ordered('audio') }
  // The locked window drives the video timing as <Video 1>; other videos follow.
  const silent = !windowHasAudio(locked, start, taskEnd)
  const lockedReference: H3MediaReference = {
    key: `locked:${locked.id}`, media_type: 'video', kind: 'locked', track_id: locked.id, segment_id: '',
    start_frame: start, frames: taskFrames, soundtrack: locked.muted !== true && !silent, fit: true, source: {}, silent,
  }
  return {
    customizable,
    video: [slotFor(lockedReference, 'active'), ...ordered('video', `track:${locked.id}`, H3_MEDIA_REFERENCE_LIMIT - 1)],
    audio: ordered('audio'),
  }
}

/** The plan behind a task's slots: its unmuted images and its active slots. */
export function h3PlanFromSlots(task: TaskLike, slots: H3TaskReferenceSlots): H3TaskReferencePlan {
  const active = (list: H3ReferenceSlot[]) => list.flatMap((slot) => (
    slot.status === 'active' && slot.reference ? [slot.reference] : []
  ))
  return {
    images: (task.content?.images ?? []).filter((image) => image.muted !== true),
    videos: active(slots.video),
    audios: active(slots.audio),
  }
}

export function planH3TaskReferences(
  tracks: readonly MultiTrack[],
  task: TaskLike,
  nextStart: number | null = null,
  options: H3ReferencePlanOptions = {},
): H3TaskReferencePlan {
  return h3PlanFromSlots(task, h3TaskReferenceSlots(tracks, task, nextStart, options))
}

/** Prompt labels in the order core assigns them. */
export function h3ReferenceLabels(plan: H3TaskReferencePlan): H3ReferenceLabels {
  let audioNumber = 0
  const soundtracks = plan.videos.map((video) => (video.soundtrack ? `Audio ${++audioNumber}` : null))
  return {
    images: plan.images.map((_, index) => `Picture ${index + 1}`),
    videos: plan.videos.map((_, index) => `Video ${index + 1}`),
    video_soundtracks: soundtracks,
    audios: plan.audios.map((_, index) => `Audio ${audioNumber + index + 1}`),
  }
}

/** The start of the task that follows `segment` on the task track, if any. */
export function h3NextTaskStart(taskSegments: readonly MultiTrackSegment[], segment: MultiTrackSegment): number | null {
  const later = taskSegments.filter((item) => item.id !== segment.id && item.start_frame > segment.start_frame)
  return later.length > 0 ? Math.min(...later.map((item) => item.start_frame)) : null
}
