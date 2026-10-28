import type { MultiTrack, MultiTrackSegment, MultiTrackSegmentContent, MultiTrackTaskImage } from '@/types/multitrack'

/**
 * Per-task H3 reference plan (mirrors utils/h3_references.py; tests/fixtures/h3_references.json
 * checks both). It decides which media a task references and the <Picture N> / <Video N> /
 * <Audio N> numbers the sampler receives.
 *
 * Videos and audios list shared clips first, in track order and once per source, then one
 * window per overlapping track. A window runs from the task start to the next task's start,
 * or to the task end for the last task. Muted and audio-locked audio tracks give no
 * reference; a task on an audio-locked video track references only that track. Each video
 * soundtrack takes an <Audio N> label before the standalone audio.
 */
export type H3ReferenceKind = 'shared' | 'track' | 'locked'

export interface H3MediaReference {
  key: string
  media_type: 'video' | 'audio'
  kind: H3ReferenceKind
  track_id: string
  segment_id: string
  start_frame: number
  frames: number
  soundtrack: boolean
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

const SLOT_PREFIX = '__slot__:'

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

function mediaSegments(track: MultiTrack): MultiTrackSegment[] {
  return track.segments.filter((segment) => segment.content?.media_type === track.type)
}

/** The track's shared clip; an audio-locked track has none. */
export function h3SharedReferenceClip(track: MultiTrack): MultiTrackSegment | undefined {
  if (track.audio_locked === true) return undefined
  return mediaSegments(track).find((segment) => segment.content.shared_reference === true)
}

function windowEnd(track: MultiTrack, start: number, end: number, skip?: MultiTrackSegment): number | null {
  const ends = mediaSegments(track)
    .filter((segment) => segment !== skip && segment.end_frame > start && segment.start_frame < end)
    .map((segment) => Math.min(segment.end_frame, end))
  return ends.length > 0 ? Math.max(...ends) : null
}

export function h3LockedVideoTrack(tracks: readonly MultiTrack[], start: number, end: number): MultiTrack | undefined {
  return tracks.find((track) => track.type === 'video' && track.audio_locked === true
    && windowEnd(track, start, end) !== null)
}

export function planH3TaskReferences(
  tracks: readonly MultiTrack[],
  task: TaskLike,
  nextStart: number | null = null,
  { audioOnly = false }: { audioOnly?: boolean } = {},
): H3TaskReferencePlan {
  const images = (task.content?.images ?? []).filter((image) => image.muted !== true)
  const start = task.start_frame
  const taskEnd = Math.max(start, task.end_frame)
  const window = nextStart !== null ? Math.max(start, nextStart) : taskEnd
  const shared: Record<'video' | 'audio', H3MediaReference[]> = { video: [], audio: [] }
  const windows: Record<'video' | 'audio', H3MediaReference[]> = { video: [], audio: [] }
  const seen: Record<'video' | 'audio', Set<string>> = { video: new Set(), audio: new Set() }
  for (const track of tracks) {
    if (track.type !== 'video' && track.type !== 'audio') continue
    const mediaType = track.type
    const muted = track.muted === true
    if (mediaType === 'audio' && (muted || track.audio_locked === true)) continue
    const clip = h3SharedReferenceClip(track)
    const identity = clip ? h3MediaIdentity(clip.content) : null
    if (clip && (identity === null || !seen[mediaType].has(identity))) {
      if (identity !== null) seen[mediaType].add(identity)
      shared[mediaType].push({
        key: `shared:${clip.id || track.id}`, media_type: mediaType, kind: 'shared', track_id: track.id,
        segment_id: clip.id, start_frame: 0, frames: taskEnd - start, soundtrack: mediaType === 'video' && !muted,
      })
    }
    const mediaEnd = windowEnd(track, start, window, clip)
    if (mediaEnd === null || mediaEnd <= start) continue
    windows[mediaType].push({
      key: `track:${track.id}`, media_type: mediaType, kind: 'track', track_id: track.id, segment_id: '',
      start_frame: start, frames: mediaEnd - start, soundtrack: mediaType === 'video' && !muted,
    })
  }
  let videos = [...shared.video, ...windows.video]
  const locked = audioOnly ? undefined : h3LockedVideoTrack(tracks, start, taskEnd)
  if (locked) {
    videos = [{
      key: `locked:${locked.id}`, media_type: 'video', kind: 'locked', track_id: locked.id, segment_id: '',
      start_frame: start, frames: taskEnd - start, soundtrack: locked.muted !== true,
    }]
  }
  return { images, videos, audios: [...shared.audio, ...windows.audio] }
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
