import { describe, expect, it } from 'vitest'
import { canEnableSharedTaskImage, synchronizeSharedTaskImages, taskImageReferenceIndex, togglePreviousFrame } from '@/lib/task-image-utils'
import { isProjectBridgeReady } from '@/lib/project-bridge'
import type { MultiTrack, MultiTrackTaskImage } from '@/types/multitrack'
import type { ProjectClip } from '@/types/project'

describe('previous frame reference', () => {
  it('retains its identity and slot when toggled and when shared references synchronize', () => {
    const clean: MultiTrackTaskImage = { id: 'clean', source_type: 'input', file_path: 'clean.png', shared_reference: true }
    const added = togglePreviousFrame([clean])
    const previous = added[1]
    const images = [previous, clean]
    const disabled = togglePreviousFrame(images)
    expect(disabled[0]).toEqual({ ...previous, muted: true })
    expect(togglePreviousFrame(disabled)[0]).toEqual({ ...previous, muted: false })
    expect(taskImageReferenceIndex(images, previous.id)).toBe(0)
    expect(canEnableSharedTaskImage([], previous)).toBe(false)
    const tracks = [{ id: 'tasks', type: 'task', segments: [
      { id: 'first', content: { images: [clean] } }, { id: 'second', content: { images } },
    ] }] as MultiTrack[]
    const result = synchronizeSharedTaskImages(tracks)
    expect(result[0].segments[1].content.images?.map((image) => image.id)).toEqual([previous.id, clean.id])
    expect(result[0].segments[0].content.images).toHaveLength(1)
  })

  it('invalidates a bridge when either selected version or boundary trim changes', () => {
    const left: ProjectClip = { id: '0', index: 0, file_path: 'video_0_1.mp4', file_name: 'video_0_1.mp4',
      media_revision: 'a', source_start_frame: 0, source_end_frame: 120, source_frame_count: 120,
      continuity_mode: 'shot', enabled: true }
    const right: ProjectClip = { ...left, id: '1', index: 1, file_name: 'video_1_1.mp4', media_revision: 'b',
      continuity_mode: 'restart_bridge', bridge: {
        left: { video: left.file_name, revision: 'a', segment_index: 0 },
        right: { video: 'video_1_1.mp4', revision: 'b', segment_index: 1 },
        before: 14, after: 15, left_frame_count: 120, right_frame_count: 120, file: 'bridge_1_1.mp4',
      } }
    expect(isProjectBridgeReady(right, [left, right])).toBe(true)
    expect(isProjectBridgeReady(right, [{ ...left, media_revision: 'new' }, right])).toBe(false)
    expect(isProjectBridgeReady(right, [{ ...left, source_end_frame: 119 }, right])).toBe(false)
    expect(isProjectBridgeReady(right, [right, left])).toBe(false)
  })
})
