import type { MultiTrackSegment, TrackData } from '@/types/multitrack'

export const H3_NATIVE_CONTEXT_FRAMES = 39
export const H3_NATIVE_MAX_RAW_FRAMES = 3592

export class H3NativeTimingError extends Error {
  constructor(public readonly code: string, message: string, public readonly segmentId = '') {
    super(message)
    this.name = 'H3NativeTimingError'
  }
}

export function snapH3NativeDuration(frames: number, continuation: boolean): number {
  if (!Number.isFinite(frames)) throw new H3NativeTimingError('DURATION', 'Duration must be finite.')
  const remainder = continuation ? 0 : 5
  const minimum = continuation ? 17 : H3_NATIVE_CONTEXT_FRAMES
  const maximum = H3_NATIVE_MAX_RAW_FRAMES - (continuation ? H3_NATIVE_CONTEXT_FRAMES : 0)
  const lower = Math.floor((frames - remainder) / 17) * 17 + remainder
  return Math.max(minimum, Math.min(maximum, frames - lower <= 8.5 ? lower : lower + 17))
}

export function h3NativeSplitFrame(start: number, end: number, requested: number, continuation: boolean): number {
  const minimum = continuation ? 17 : H3_NATIVE_CONTEXT_FRAMES
  const left = Math.min(snapH3NativeDuration(requested - start, continuation), end - start - 17)
  if (left < minimum || left !== snapH3NativeDuration(left, continuation)) {
    throw new H3NativeTimingError('SPLIT_SHORT', 'Both split tasks need a legal generation window.')
  }
  return start + left
}

export function isH3Continuation(segment: MultiTrackSegment): boolean {
  return segment.content.task_mode !== 'passthrough' && !!segment.content.continuity_mode
    && segment.content.continuity_mode !== 'shot'
}

/** Reconcile a complete editing transaction before it enters undo history.
 * Media positions and contents are never snapped or moved by this function.
 */
export function reconcileH3NativeTimeline(candidate: TrackData): TrackData {
  if (!candidate.h3_native) return candidate
  if (candidate.h3_native.version !== 1 || typeof candidate.h3_native.allow_vae_fallback !== 'boolean') {
    throw new H3NativeTimingError('POLICY_VERSION', 'Unsupported native timing policy.')
  }
  if (candidate.frame_rate !== 24) throw new H3NativeTimingError('FORMAT', 'Native H3 requires 24 fps.')
  const ordered = candidate.tracks.flatMap((track) => track.type === 'task' ? track.segments : [])
    .sort((a, b) => a.start_frame - b.start_frame || a.id.localeCompare(b.id))
  const replacements = new Map<string, MultiTrackSegment>()
  let previous: MultiTrackSegment | undefined
  let shift = 0
  for (const segment of ordered) {
    if (!segment.id || replacements.has(segment.id)) {
      throw new H3NativeTimingError('SEGMENT_ID', 'Each task needs a unique ID.', segment.id)
    }
    const requested = segment.end_frame - segment.start_frame
    const passthrough = segment.content.task_mode === 'passthrough'
    const continuation = !!previous && isH3Continuation(segment)
    const duration = passthrough ? Math.max(1, Math.round(requested)) : snapH3NativeDuration(requested, continuation)
    if (continuation && previous && previous.end_frame - previous.start_frame < H3_NATIVE_CONTEXT_FRAMES
      && !isH3Continuation(previous)) {
      throw new H3NativeTimingError('CONTEXT_SHORT', 'The predecessor cannot supply 39 context frames.', segment.id)
    }
    const start = continuation && previous ? previous.end_frame
      : Math.max(previous?.end_frame ?? 0, Math.round(segment.start_frame + shift), 0)
    const next: MultiTrackSegment = {
      ...segment,
      start_frame: start,
      end_frame: start + duration,
      content: { ...segment.content, continuity_mode: continuation
        ? segment.content.continuity_mode === 'context_swap' ? 'context_drift' : segment.content.continuity_mode
        : 'shot' },
    }
    const owner = candidate.tracks.find((track) => track.segments.some((item) => item.id === segment.id))
    if (owner?.locked && (next.start_frame !== segment.start_frame || next.end_frame !== segment.end_frame
      || next.content.continuity_mode !== segment.content.continuity_mode)) {
      throw new H3NativeTimingError('TRACK_LOCKED', 'Unlock the task track before changing its timing.', segment.id)
    }
    replacements.set(segment.id, next)
    previous = next
    shift = next.end_frame - segment.end_frame
  }
  const tracks = candidate.tracks.map((track) => track.type === 'task'
    ? { ...track, segments: track.segments.map((segment) => replacements.get(segment.id) ?? segment) }
    : track)
  const lastFrame = Math.max(0, ...tracks.flatMap((track) => track.segments.map((segment) => segment.end_frame)))
  return { ...candidate, tracks, total_length: Math.max(candidate.total_length, lastFrame) }
}

export function splitH3NativeTask(segment: MultiTrackSegment, requested: number, rightId: string): MultiTrackSegment[] {
  const cut = h3NativeSplitFrame(segment.start_frame, segment.end_frame, requested, isH3Continuation(segment))
  return [
    { ...segment, end_frame: cut },
    { ...segment, id: rightId, start_frame: cut,
      content: { ...segment.content, continuity_mode: isH3Continuation(segment) ? segment.content.continuity_mode : 'context' } },
  ]
}
