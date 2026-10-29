import { describe, expect, it } from 'vitest'
import { h3TaskReferenceSlots } from '@/lib/h3-references'
import {
  canEnableSharedTaskMedia,
  sharedTaskMediaUpdates,
  synchronizeSharedTaskMedia,
  taskMediaItemsFromSlots,
} from '@/lib/task-media-utils'
import type { MultiTrack, MultiTrackSegment, MultiTrackTaskMedia } from '@/types/multitrack'

function task(id: string, audios: MultiTrackTaskMedia[]): MultiTrackSegment {
  return { id, start_frame: 0, end_frame: 48, color: '', content: { media_type: 'none', task_mode: 'ref', audios } }
}

const line: MultiTrackTaskMedia = { id: 'line', source_type: 'input', file_path: 'line.wav' }

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

  it('shares an upload before every task\'s own files and removes the copies when unshared', () => {
    const own: MultiTrackTaskMedia = { id: 'own', source_type: 'input', file_path: 'own.wav' }
    const segments = [task('a', [line]), task('b', [own])]
    expect(canEnableSharedTaskMedia(segments, 'audios', line)).toBe(true)

    const shared = sharedTaskMediaUpdates(segments, 'audios', line, true)
    expect(shared).toEqual([
      { segmentId: 'a', items: [{ ...line, shared_reference: true }] },
      { segmentId: 'b', items: [{ ...line, id: expect.any(String), shared_reference: true, shared_reference_copy: true }, own] },
    ])

    const sharedSegments = shared.map((update) => task(update.segmentId, update.items))
    expect(sharedTaskMediaUpdates(sharedSegments, 'audios', line, false)).toEqual([
      { segmentId: 'a', items: [{ ...line, shared_reference: false }] },
      { segmentId: 'b', items: [own] },
    ])
  })

  it('does not share into a task that already has three of its own files', () => {
    const full = ['x', 'y', 'z'].map((id): MultiTrackTaskMedia => ({ id, source_type: 'input', file_path: `${id}.wav` }))
    expect(canEnableSharedTaskMedia([task('a', [line]), task('b', full)], 'audios', line)).toBe(false)
  })

  it('copies shared uploads into tasks that lack them', () => {
    const tracks: MultiTrack[] = [{
      id: 'tasks', name: 'Tasks', type: 'task', color: '', muted: false, locked: false,
      segments: [task('a', [{ ...line, shared_reference: true }]), task('b', [])],
    }]
    const [synced] = synchronizeSharedTaskMedia(tracks)
    expect(synced.segments[1].content.audios).toEqual([
      { ...line, id: expect.any(String), shared_reference: true, shared_reference_copy: true },
    ])
    expect(synchronizeSharedTaskMedia([{ ...tracks[0], segments: [task('a', [line])] }])[0].segments[0].content.audios).toEqual([line])
  })
})
