import { H3_MEDIA_REFERENCE_LIMIT, H3_TRACK_SOURCE, type H3ReferenceMediaType, type H3ReferenceSlot } from '@/lib/h3-references'
import { uploadInputMediaFile } from '@/lib/media-upload'
import { createTaskImage } from '@/lib/task-image-utils'
import { uuid } from '@/lib/uuid'
import type { MultiTrack, MultiTrackSegment, MultiTrackSourceType, MultiTrackTaskMedia } from '@/types/multitrack'

/**
 * A task's reference videos and audios (`content.videos` / `content.audios`): uploads plus
 * placeholders for timeline references. Shared uploads are copied into every task like shared
 * images, before the task's own items.
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

function activeUploads(items: MultiTrackTaskMedia[]): number {
  return items.filter((item) => isTaskMediaUpload(item) && item.muted !== true).length
}

export function canEnableSharedTaskMedia(
  segments: MultiTrackSegment[],
  field: TaskMediaField,
  item: MultiTrackTaskMedia,
): boolean {
  if (!isTaskMediaUpload(item)) return false
  const identity = taskMediaIdentity(item)
  return segments.every((segment) => {
    const items = taskMediaFromContent(segment.content[field])
    return items.some((candidate) => taskMediaIdentity(candidate) === identity)
      || activeUploads(items) < H3_MEDIA_REFERENCE_LIMIT
  })
}

export function sharedTaskMediaUpdates(
  segments: MultiTrackSegment[],
  field: TaskMediaField,
  item: MultiTrackTaskMedia,
  enabled: boolean,
): Array<{ segmentId: string; items: MultiTrackTaskMedia[] }> {
  const identity = taskMediaIdentity(item)
  return segments.map((segment) => {
    const items = taskMediaFromContent(segment.content[field])
    const matching = items.find((candidate) => taskMediaIdentity(candidate) === identity)
    if (enabled) {
      const shared = matching
        ? { ...matching, shared_reference: true }
        : { ...item, id: uuid(), shared_reference: true, shared_reference_copy: true }
      return {
        segmentId: segment.id,
        items: [
          ...items.filter((candidate) => candidate.shared_reference === true && taskMediaIdentity(candidate) !== identity),
          shared,
          ...items.filter((candidate) => candidate.shared_reference !== true && taskMediaIdentity(candidate) !== identity),
        ],
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

/** Copy every shared upload into every task, before the task's own items. */
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
        const common = Array.from(shared.entries()).map(([identity, sharedItem]) => {
          const existing = items.find((candidate) => taskMediaIdentity(candidate) === identity)
          return existing
            ? { ...existing, shared_reference: true }
            : { ...sharedItem, id: uuid(), shared_reference: true, shared_reference_copy: true }
        })
        const local = items.filter((candidate) => !shared.has(taskMediaIdentity(candidate)))
        return { ...segment, content: { ...segment.content, [field]: [...common, ...local] } }
      }),
    })
  }
  return result
}
