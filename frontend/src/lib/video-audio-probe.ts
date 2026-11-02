import type { MultiTrackTaskMedia, TrackData } from '@/types/multitrack'

/**
 * Whether each video has an audio stream, stored as `has_audio` on video clips and task video
 * uploads. The H3 reference plan gives silent videos no soundtrack by default, so the Editor
 * probes each local file once and records the answer in the track data.
 */
type VideoSource = Pick<MultiTrackTaskMedia, 'source_type' | 'file_path' | 'local_path'>

const PROBED_SOURCES = new Set(['input', 'output', 'local'])
export const MAX_VIDEO_AUDIO_PROBES = 64

/** The key of a video the server can probe, or null for URLs, slots and presets. */
export function videoAudioProbeKey(source: VideoSource): string | null {
  const sourceType = source.source_type ?? 'input'
  if (!PROBED_SOURCES.has(sourceType)) return null
  const path = sourceType === 'local' ? source.local_path ?? source.file_path : source.file_path
  return path ? `${sourceType}\u0000${path}` : null
}

function unprobedVideos(data: TrackData): VideoSource[] {
  return data.tracks.flatMap((track): VideoSource[] => {
    if (track.type === 'video') {
      return track.segments.flatMap((segment) => (
        segment.content.media_type === 'video' && segment.content.has_audio === undefined ? [segment.content] : []
      ))
    }
    if (track.type !== 'task') return []
    return track.segments.flatMap((segment) => (segment.content.videos ?? []).filter((item) => (
      item.source_type !== 'track' && item.has_audio === undefined
    )))
  })
}

/** Videos whose audio is unknown, by probe key. */
export function videosMissingAudioInfo(data: TrackData): Map<string, VideoSource> {
  const missing = new Map<string, VideoSource>()
  for (const source of unprobedVideos(data)) {
    const key = videoAudioProbeKey(source)
    if (key !== null && !missing.has(key)) missing.set(key, source)
  }
  return missing
}

/** Record probe results on every matching video; null when nothing changes. */
export function applyVideoAudioInfo(data: TrackData, results: ReadonlyMap<string, boolean>): TrackData | null {
  let changed = false
  const known = <T extends VideoSource & { has_audio?: boolean }>(source: T): T => {
    if (source.has_audio !== undefined) return source
    const key = videoAudioProbeKey(source)
    const hasAudio = key === null ? undefined : results.get(key)
    if (hasAudio === undefined) return source
    changed = true
    return { ...source, has_audio: hasAudio }
  }
  const tracks = data.tracks.map((track) => {
    if (track.type === 'video') {
      return {
        ...track,
        segments: track.segments.map((segment) => (
          segment.content.media_type === 'video' ? { ...segment, content: known(segment.content) } : segment
        )),
      }
    }
    if (track.type !== 'task') return track
    return {
      ...track,
      segments: track.segments.map((segment) => segment.content.videos ? {
        ...segment,
        content: {
          ...segment.content,
          videos: segment.content.videos.map((item) => (item.source_type === 'track' ? item : known(item))),
        },
      } : segment),
    }
  })
  return changed ? { ...data, tracks } : null
}

/** Ask the server which videos have an audio stream; null marks one it could not read. */
export async function probeVideoAudio(sources: readonly VideoSource[]): Promise<Array<boolean | null>> {
  const response = await fetch('/easy-media/video/has-audio', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: sources.map(({ source_type, file_path, local_path }) => ({ source_type, file_path, local_path })) }),
  })
  if (!response.ok) throw new Error(`Video audio probe failed: ${response.status}`)
  const result = await response.json() as { has_audio?: Array<boolean | null> }
  return sources.map((_, index) => {
    const value = result.has_audio?.[index]
    return typeof value === 'boolean' ? value : null
  })
}
