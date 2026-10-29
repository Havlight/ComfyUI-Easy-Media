import { describe, expect, it } from 'vitest'
import { h3TaskReferenceSlots } from '@/lib/h3-references'
import {
  canShareTaskMedia,
  sharedTaskMediaUpdates,
  synchronizeSharedTaskMedia,
  taskMediaItemsFromSlots,
} from '@/lib/task-media-utils'
import type { MultiTrack, MultiTrackSegment, MultiTrackTaskMedia } from '@/types/multitrack'

function task(id: string, audios: MultiTrackTaskMedia[]): MultiTrackSegment {
  return { id, start_frame: 0, end_frame: 48, color: '', content: { media_type: 'none', task_mode: 'ref', audios } }
}

const line: MultiTrackTaskMedia = { id: 'line', source_type: 'input', file_path: 'line.wav' }
const own: MultiTrackTaskMedia = { id: 'own', source_type: 'input', file_path: 'own.wav' }

describe('task media utils', () => {
  it('stores a placeholder for each timeline slot and keeps uploads as they are', () => {
    const tracks: MultiTrack[] = [{
      id: 'bed', name: 'Bed', type: 'audio', color: '', muted: false, locked: false,
      segments: [{ id: 'bed-1', start_frame: 0, end_frame: 100, color: '', content: { media_type: 'audio', file_path: 'bed.wav' } }],
    }]
    const slots = h3TaskReferenceSlots(tracks, task('t', [line]), null)
    expect(taskMediaItemsFromSlots(slots.audio)).toEqual([
      { id: expect.any(String), source_type: 'track', track_id: 'bed' },
      line,
    ])
  })

  it('shares an upload without moving anything and removes the copies when unshared', () => {
    const segments = [task('a', [own, line]), task('b', [own])]

    // The shared file keeps its place here and comes last elsewhere, so written numbers stay put.
    const shared = sharedTaskMediaUpdates(segments, 'audios', line, true)
    expect(shared).toEqual([
      { segmentId: 'a', items: [own, { ...line, shared_reference: true }] },
      { segmentId: 'b', items: [own, { ...line, id: expect.any(String), shared_reference: true, shared_reference_copy: true }] },
    ])

    const sharedSegments = shared.map((update) => task(update.segmentId, update.items))
    expect(sharedTaskMediaUpdates(sharedSegments, 'audios', line, false)).toEqual([
      { segmentId: 'a', items: [own, { ...line, shared_reference: false }] },
      { segmentId: 'b', items: [own] },
    ])
  })

  it('shares only uploads; timeline placeholders stay per task', () => {
    expect(canShareTaskMedia(line)).toBe(true)
    expect(canShareTaskMedia({ id: 'p', source_type: 'track', track_id: 'bed' })).toBe(false)
  })

  it('copies shared uploads to the end of tasks that lack them', () => {
    const tracks: MultiTrack[] = [{
      id: 'tasks', name: 'Tasks', type: 'task', color: '', muted: false, locked: false,
      segments: [task('a', [own, { ...line, shared_reference: true }]), task('b', [own])],
    }]
    const [synced] = synchronizeSharedTaskMedia(tracks)
    expect(synced.segments[0]).toBe(tracks[0].segments[0])
    expect(synced.segments[1].content.audios).toEqual([
      own, { ...line, id: expect.any(String), shared_reference: true, shared_reference_copy: true },
    ])
    expect(synchronizeSharedTaskMedia([{ ...tracks[0], segments: [task('a', [line])] }])[0].segments[0].content.audios).toEqual([line])
  })
})
