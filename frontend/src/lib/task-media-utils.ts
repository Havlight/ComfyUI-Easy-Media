import { H3_TRACK_SOURCE, type H3ReferenceMediaType, type H3ReferenceSlot } from '@/lib/h3-references'
import { uploadInputMediaFile } from '@/lib/media-upload'
import { createTaskImage } from '@/lib/task-image-utils'
import { uuid } from '@/lib/uuid'
import type { MultiTrack, MultiTrackSegment, MultiTrackSourceType, MultiTrackTaskMedia } from '@/types/multitrack'

/**
 * A task's reference videos and audios (`content.videos` / `content.audios`): uploads plus
 * placeholders for timeline references. Shared uploads are copied into every task like shared
 * images, but never move, so the numbers already written in prompts stay put.
 */
export type TaskMediaField = 'videos' | 'audios'

export const TASK_MEDIA_FIELDS: readonly TaskMediaField[] = ['videos', 'audios']

export function taskMediaField(mediaType: H3ReferenceMediaType): TaskMediaField {
  return mediaType === 'video' ? 'videos' : 'audios'
}

export function taskMediaFromContent(value: MultiTrackTaskMedia[] | undefined): MultiTrackTaskMedia[] {
  return Array.isArray(value) ? value : []
}

export function isTaskMediaUpload(item: MultiTrackTaskMedia): boolean {
  return item.source_type !== H3_TRACK_SOURCE
}

export function taskMediaIdentity(item: MultiTrackTaskMedia): string {
  const sourceType = item.source_type ?? 'input'
  const path = sourceType === 'slot' ? item.slot_name : item.file_path ?? item.local_path ?? item.url ?? item.file_name
  return `${sourceType}:${path ?? item.id}`
}

export function createTaskMedia(filePath: string, source: MultiTrackSourceType): MultiTrackTaskMedia {
  return createTaskImage(filePath, source)
}

export async function uploadTaskMediaFile(file: File): Promise<MultiTrackTaskMedia> {
  return { ...createTaskMedia(await uploadInputMediaFile(file), 'input'), file_name: file.name }
}

/** The stored list for a task's slots, with a placeholder for each timeline slot that has none. */
export function taskMediaItemsFromSlots(slots: readonly H3ReferenceSlot[]): MultiTrackTaskMedia[] {
  return slots.filter((slot) => slot.kind !== 'locked').map((slot) => slot.item ?? {
    id: uuid(),
    source_type: H3_TRACK_SOURCE,
    track_id: slot.track_id,
    ...(slot.kind === 'shared' ? { segment_id: slot.segment_id } : {}),
  })
}

/** Any upload can be shared; a task that is already full shows the copy as over the limit. */
export function canShareTaskMedia(item: MultiTrackTaskMedia): boolean {
  return isTaskMediaUpload(item)
}

/**
 * Share or unshare an upload without moving anything, so numbers in prompts stay put: it keeps
 * its place where it already is, and other tasks get a copy at the end. Unsharing removes the
 * copies and keeps the original where it is.
 */
export function sharedTaskMediaUpdates(
  segments: MultiTrackSegment[],
  field: TaskMediaField,
  item: MultiTrackTaskMedia,
  enabled: boolean,
): Array<{ segmentId: string; items: MultiTrackTaskMedia[] }> {
  const identity = taskMediaIdentity(item)
  return segments.map((segment) => {
    const items = taskMediaFromContent(segment.content[field])
    if (enabled) {
      const matching = items.some((candidate) => taskMediaIdentity(candidate) === identity)
      return {
        segmentId: segment.id,
        items: matching
          ? items.map((candidate) => (
              taskMediaIdentity(candidate) === identity ? { ...candidate, shared_reference: true } : candidate
            ))
          : [...items, { ...item, id: uuid(), shared_reference: true, shared_reference_copy: true }],
      }
    }
    return {
      segmentId: segment.id,
      items: items.flatMap((candidate) => {
        if (taskMediaIdentity(candidate) !== identity) return [candidate]
        if (candidate.shared_reference_copy === true) return []
        const local = { ...candidate, shared_reference: false }
        delete local.shared_reference_copy
        return [local]
      }),
    }
  })
}

/** Copy every shared upload into every task that lacks it, at the end, without moving anything. */
export function synchronizeSharedTaskMedia(tracks: MultiTrack[]): MultiTrack[] {
  const taskSegments = tracks.filter((track) => track.type === 'task').flatMap((track) => track.segments)
  let result = tracks
  for (const field of TASK_MEDIA_FIELDS) {
    const shared = new Map<string, MultiTrackTaskMedia>()
    for (const segment of taskSegments) {
      for (const item of taskMediaFromContent(segment.content[field])) {
        if (item.shared_reference !== true || !isTaskMediaUpload(item)) continue
        const identity = taskMediaIdentity(item)
        if (!shared.has(identity)) shared.set(identity, item)
      }
    }
    if (shared.size === 0) continue
    result = result.map((track) => track.type !== 'task' ? track : {
      ...track,
      segments: track.segments.map((segment) => {
        const items = taskMediaFromContent(segment.content[field])
        const present = new Set(items.map(taskMediaIdentity))
        const missing = Array.from(shared.entries())
          .filter(([identity]) => !present.has(identity))
          .map(([, sharedItem]) => ({ ...sharedItem, id: uuid(), shared_reference: true, shared_reference_copy: true }))
        const next = items.map((candidate) => (
          shared.has(taskMediaIdentity(candidate)) && candidate.shared_reference !== true
            ? { ...candidate, shared_reference: true }
            : candidate
        ))
        if (missing.length === 0 && next.every((candidate, index) => candidate === items[index])) return segment
        return { ...segment, content: { ...segment.content, [field]: [...next, ...missing] } }
      }),
    })
  }
  return result
}
