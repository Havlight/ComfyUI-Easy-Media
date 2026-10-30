import { describe, expect, it, vi } from 'vitest'
import { convertH3Plan } from '@/lib/h3-plan-convert'
import { translate } from '@/lib/i18n'
import type { TrackData } from '@/types/multitrack'

const t = (path: string, params?: Record<string, string | number>) => translate('en', path, params)

/** A template's empty timeline: a task track and the first video track. */
function template(): TrackData {
  return {
    h3_native: { version: 2 },
    total_length: 120,
    frame_rate: 24,
    tracks: [
      { id: 'tasks', name: 'Task 0', type: 'task', color: '', muted: false, locked: false, segments: [] },
      { id: 'video', name: 'Video 0', type: 'video', color: '', muted: false, locked: false, segments: [] },
    ],
  }
}

function server(results: unknown[]) {
  return vi.fn(async () => new Response(JSON.stringify({ results })))
}

describe('H3 plan conversion for the skill', () => {
  it('turns a plan into track data with the same importer as the dialog', async () => {
    const fetcher = server([{ source_type: 'input', file_path: 'easy_media/imports/abc/song.wav', file_name: 'song.wav', duration: 9 }])
    const conversion = await convertH3Plan(template(), {
      version: 1,
      tasks: [{ id: 's01', seconds: 5, mode: 'ref', prompt: 'Dance' }],
      tracks: [{ type: 'video', clips: [] }, { type: 'audio', audio_locked: true, clips: [{ input: 'easy_media/imports/abc/song.wav', at: 1 }] }],
    }, 'MiniMax', t, fetcher)

    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual({
      items: [{ media_type: 'audio', input: 'easy_media/imports/abc/song.wav', probe: true }],
    })
    expect(conversion.errors).toEqual([])
    expect(conversion.warnings).toEqual(['Task 1: Timing adjusted to 0s–5.167s (5.167s) to fit H3\'s frame grid.'])
    expect(conversion.tasks.map((row) => [row.id, row.change, row.seconds])).toEqual([['s01', 'added', 5.167]])
    // Tracks without an id are new, so the template's own video track is replaced.
    expect(conversion.tracks.map((row) => [row.type, row.change, row.audioLocked, row.clips])).toEqual([
      ['video', 'added', false, []], ['audio', 'added', true, [{ name: 'song.wav', start: 0, end: 9 }]], ['video', 'removed', false, []],
    ])
    const audio = conversion.track_data!.tracks.find((track) => track.type === 'audio')!
    expect(audio.segments.map((segment) => [segment.start_frame, segment.end_frame])).toEqual([[0, 216]])
  })

  it('says what each prompt label of a task stands for', async () => {
    // The server finds every input file; probed videos are 10 s long with sound.
    const fetcher = vi.fn(async (_path: string, init: RequestInit) => {
      const { items } = JSON.parse(String(init.body)) as { items: Array<{ input: string; probe?: boolean }> }
      return new Response(JSON.stringify({
        results: items.map((item) => ({
          source_type: 'input', file_path: item.input, file_name: item.input.split('/').at(-1),
          ...(item.probe ? { duration: 10, has_audio: true } : {}),
        })),
      }))
    })
    const conversion = await convertH3Plan(template(), {
      version: 1,
      tasks: [
        { id: 's01', seconds: 5, images: [{ input: 'refs/a.png' }, { input: 'refs/b.png' }, { input: 'refs/c.png' }] },
        {
          id: 's02', seconds: 5, mode: 'ref', continuity: 'context_drift',
          images: ['previous_frame', { input: 'refs/alice.png' }], audios: [{ input: 'voice/line.wav' }],
        },
      ],
      tracks: [{ id: 'v1', type: 'video', clips: [{ input: 'clips/dance.mp4', at: 's02' }] }],
    }, 'MiniMax', t, fetcher)

    expect(conversion.errors).toEqual([])
    // Default tasks take only the first and last image as frames.
    expect(conversion.tasks.map((row) => row.labels)).toEqual([
      { '<Picture 1>': 'first frame: a.png', '<Picture 2>': 'unused in default: b.png', '<Picture 3>': 'last frame: c.png' },
      {
        '<Picture 1>': "previous task's last frame",
        '<Picture 2>': 'alice.png',
        '<Video 1>': 'dance.mp4 (video track v1, track window)',
        '<Audio 1>': 'soundtrack of <Video 1>',
        '<Audio 2>': 'line.wav',
      },
    ])
  })

  it('returns readable errors and no track data when the plan cannot be used', async () => {
    const conversion = await convertH3Plan(template(), { version: 1, tasks: [{ seconds: 5, mode: 'r2v' }] }, 'MiniMax', t, server([]))

    expect(conversion.track_data).toBeNull()
    expect(conversion.errors).toEqual(['Task 1: Mode "r2v" is not one of default, ref, edit, l2v, passthrough.'])
    expect((await convertH3Plan(template(), { tasks: [] }, 'MiniMax', t, server([]))).errors)
      .toEqual(['Plan version undefined is not supported; use version 1.'])
  })
})
