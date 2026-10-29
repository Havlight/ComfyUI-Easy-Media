import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useVideoAudioProbe } from '@/hooks/use-video-audio-probe'
import { applyVideoAudioInfo, videosMissingAudioInfo } from '@/lib/video-audio-probe'
import type { MultiTrackSegment, TrackData } from '@/types/multitrack'

function clip(id: string, content: Partial<MultiTrackSegment['content']>): MultiTrackSegment {
  return { id, start_frame: 0, end_frame: 10, color: '', content: { media_type: 'video', ...content } }
}

function trackData(): TrackData {
  return {
    total_length: 10,
    frame_rate: 24,
    tracks: [
      { id: 'cam', name: 'Video 0', type: 'video', color: '', muted: false, locked: false, segments: [
        clip('a', { source_type: 'input', file_path: 'a.mp4' }),
        clip('web', { source_type: 'url', url: 'https://example.com/w.mp4' }),
        clip('b', { source_type: 'input', file_path: 'b.mp4', has_audio: true }),
      ] },
      { id: 'tasks', name: 'Task 0', type: 'task', color: '', muted: false, locked: false, segments: [{
        id: 't1', start_frame: 0, end_frame: 10, color: '', content: { media_type: 'none', videos: [
          { id: 'u', source_type: 'local', local_path: 'C:/clips/c.mp4' },
          { id: 'p', source_type: 'track', track_id: 'cam' },
        ] },
      }] },
    ],
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('video audio probe', () => {
  it('finds local videos and uploads whose audio is unknown', () => {
    expect([...videosMissingAudioInfo(trackData()).keys()]).toEqual(['input\u0000a.mp4', 'local\u0000C:/clips/c.mp4'])
  })

  it('records results on matching videos and reports when nothing changes', () => {
    const results = new Map([['input\u0000a.mp4', false], ['local\u0000C:/clips/c.mp4', true]])
    const next = applyVideoAudioInfo(trackData(), results)!

    expect(next.tracks[0].segments.map((segment) => segment.content.has_audio)).toEqual([false, undefined, true])
    expect(next.tracks[1].segments[0].content.videos).toEqual([
      { id: 'u', source_type: 'local', local_path: 'C:/clips/c.mp4', has_audio: true },
      { id: 'p', source_type: 'track', track_id: 'cam' },
    ])
    expect(applyVideoAudioInfo(next, results)).toBeNull()
  })

  it('probes each unknown file once and amends the committed data', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ has_audio: [false, null] }))))
    vi.stubGlobal('fetch', fetchMock)
    let data = trackData()
    const amend = vi.fn((update: (current: TrackData) => TrackData | null) => {
      data = update(data) ?? data
    })

    renderHook(() => useVideoAudioProbe(data, amend))

    await waitFor(() => expect(amend).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown[] as [string, RequestInit])[1].body))).toEqual({ items: [
      { source_type: 'input', file_path: 'a.mp4' },
      { source_type: 'local', local_path: 'C:/clips/c.mp4' },
    ] })
    // An unreadable file stays unknown and is not asked about again.
    expect(data.tracks[0].segments[0].content.has_audio).toBe(false)
    expect(data.tracks[1].segments[0].content.videos?.[0].has_audio).toBeUndefined()
  })
})
