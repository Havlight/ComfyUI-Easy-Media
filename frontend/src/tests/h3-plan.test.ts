import { describe, expect, it } from 'vitest'
import { prepareH3NativeEdit } from '@/hooks/use-h3-native-editing'
import {
  buildH3PlanImport,
  exportH3Plan,
  formatH3PlanTaskNumbers,
  groupH3PlanIssues,
  h3PlanMediaKey,
  h3PlanMediaRequests,
  parseH3Plan,
  type H3PlanFile,
  type H3PlanMediaRequest,
  type H3PlanMediaResult,
} from '@/lib/h3-plan'
import type { MultiTrackSegment, TrackData } from '@/types/multitrack'

const prepare = (current: TrackData) => (candidate: TrackData) => prepareH3NativeEdit(current, candidate, 'MiniMax')

function clip(id: string, start: number, end: number, content: Partial<MultiTrackSegment['content']>): MultiTrackSegment {
  return { id, start_frame: start, end_frame: end, color: 'var(--primary)', content: { media_type: 'none', ...content } }
}

function project(): TrackData {
  const raw: TrackData = {
    h3_native: { version: 2 },
    total_length: 260,
    frame_rate: 24,
    tracks: [
      { id: 'tasks', name: 'Task 0', type: 'task', color: 'var(--multitrack-task-bg)', muted: false, locked: false, segments: [
        clip('t1', 0, 124, {
          task_mode: 'ref', continuity_mode: 'shot', user_prompt: '<Picture 1> waves at <Video 1>',
          images: [{ id: 'img', source_type: 'input', file_path: 'refs/alice.png', file_name: 'alice.png', muted: false }],
          videos: [{ id: 'clip', source_type: 'input', file_path: 'refs/clip.mp4', has_audio: false }],
          audios: [{ id: 'bed-slot', source_type: 'track', track_id: 'song', muted: false, fit: false }],
        }),
        clip('t2', 124, 243, {
          task_mode: 'ref', continuity_mode: 'context_drift', user_prompt: 'Keep walking',
          images: [{ id: 'prev', source_type: 'previous_frame', muted: true }],
        }),
      ] },
      { id: 'song', name: 'Audio 0', type: 'audio', color: 'var(--primary)', muted: false, locked: false, audio_locked: true,
        segments: [clip('song-1', 0, 243, { media_type: 'audio', source_type: 'input', file_path: 'song.wav' })] },
    ],
  }
  return prepareH3NativeEdit(raw, raw, 'MiniMax')
}

/** The server's answers: input and output files exist; local files are copied into imports. */
function serve(requests: H3PlanMediaRequest[], missing: string[] = []): Map<string, H3PlanMediaResult> {
  return new Map(requests.map((request): [string, H3PlanMediaResult] => {
    const key = h3PlanMediaKey(request)
    const path = request.input ?? request.output ?? request.file ?? request.local ?? ''
    if (missing.includes(path)) return [key, { error: `${path} was not found` }]
    if (request.input) return [key, { source_type: 'input', file_path: request.input, file_name: path.split('/').pop()! }]
    if (request.output) return [key, { source_type: 'output', file_path: request.output, file_name: path.split('/').pop()! }]
    const name = path.split(/[\\/]/).pop()!
    return [key, { source_type: 'input', file_path: `easy_media/imports/${name}`, file_name: name }]
  }))
}

function importPlan(current: TrackData, plan: unknown, missing: string[] = []) {
  const parsed = parseH3Plan(JSON.stringify(plan))
  expect(parsed.issues).toEqual([])
  return buildH3PlanImport(current, parsed.plan!, serve(h3PlanMediaRequests(parsed.plan!), missing), prepare(current))
}

function codes(result: ReturnType<typeof buildH3PlanImport>) {
  return result.issues.map((item) => `${item.level}:${item.task ?? '-'}:${item.code}`)
}

describe('H3 plan files', () => {
  it('exports the task track so that importing it changes nothing', () => {
    const current = project()
    const plan = exportH3Plan(current)

    expect(plan.tasks[0]).toEqual({
      id: 't1', seconds: 5.167, mode: 'ref', continuity: 'shot', prompt: '<Picture 1> waves at <Video 1>',
      images: [{ input: 'refs/alice.png' }], videos: [{ input: 'refs/clip.mp4' }],
      audios: [{ track: 'song', muted: false, fit: false }],
    })
    expect(plan.tasks[1].images).toEqual([{ previous_frame: true, muted: true }])

    const result = importPlan(current, plan)
    expect(codes(result)).toEqual([])
    expect(result.data).toEqual(current)
    expect(result.rows.map((row) => row.change)).toEqual(['unchanged', 'unchanged'])
  })

  it('adds tasks from local files, snaps their timing and copies the files once', () => {
    const current = project()
    const result = importPlan(current, { version: 1, tasks: [
      ...exportH3Plan(current).tasks,
      { seconds: 5, mode: 'ref', continuity: 'context', prompt: '<Picture 2> joins <Picture 1>',
        images: ['previous_frame', 'C:/shots/bob.png'], audios: [{ file: 'D:/voice/line.wav', fit: true }] },
    ] })

    expect(codes(result)).toEqual(['warning:3:timing_adjusted'])
    const added = result.data!.tracks[0].segments[2]
    expect([added.start_frame, added.end_frame]).toEqual([243, 362])
    expect(added.content.images?.map((image) => image.file_path ?? image.source_type)).toEqual([
      'previous_frame', 'easy_media/imports/bob.png',
    ])
    expect(added.content.audios).toEqual([
      { id: expect.any(String), source_type: 'input', file_path: 'easy_media/imports/line.wav', file_name: 'line.wav', fit: true },
    ])
    expect(result.rows.at(-1)).toMatchObject({ change: 'added', seconds: 4.958, images: 2, audios: 1 })
  })

  it('updates listed tasks, keeps fields the file leaves out and removes tasks it omits', () => {
    const current = project()
    const result = importPlan(current, { version: 1, tasks: [{ id: 't1', prompt: 'A new line', mode: 'edit' }] })

    expect(codes(result)).toEqual([])
    const [task] = result.data!.tracks[0].segments
    expect(task.content).toMatchObject({ user_prompt: 'A new line', task_mode: 'edit' })
    expect(task.content.images).toEqual(current.tracks[0].segments[0].content.images)
    expect(result.rows.map((row) => [row.id, row.change])).toEqual([['t1', 'modified'], ['t2', 'removed']])
  })

  it('blocks the import when tasks or references cannot be used', () => {
    const current = project()
    const result = importPlan(current, { version: 1, tasks: [
      { id: 't1', mode: 'r2v' },
      { id: 't1', seconds: 5 },
      { seconds: 5, images: ['C:/missing.png'], audios: [{ track: 'nope' }], videos: [{ track: 'song' }] },
      { prompt: 'no length' },
    ] }, ['C:/missing.png'])

    expect(result.data).toBeNull()
    expect(codes(result)).toEqual(expect.arrayContaining([
      'error:1:invalid_mode', 'error:2:duplicate_id', 'error:3:file_failed', 'error:3:track_type',
      'error:4:missing_seconds',
    ]))
  })

  it('takes a plan from another workflow, skipping placeholders for tracks this timeline lacks', () => {
    const current = project()
    const result = importPlan(current, { version: 1, tasks: [
      { id: 'other-1', seconds: 5, mode: 'ref', prompt: '<Audio 1> plays, then <Audio 1> again',
        audios: [{ track: 'elsewhere' }, { track: 'song', clip: 'gone' }] },
      { id: 'other-2', seconds: 5, continuity: 'context' },
    ] })

    expect(codes(result)).toEqual([
      'warning:-:foreign_ids', 'warning:1:unknown_track', 'warning:1:unknown_clip',
      'warning:1:timing_adjusted', 'warning:1:prompt_reference', 'warning:2:timing_adjusted',
    ])
    expect(result.issues[0].params).toEqual({ count: 2 })
    expect(result.data!.tracks[0].segments[0].content.audios).toBeUndefined()
    expect(result.rows.map((row) => [row.id, row.change])).toEqual([
      ['other-1', 'added'], ['other-2', 'added'], ['t1', 'removed'], ['t2', 'removed'],
    ])

    // Once some ids match, each unknown one is named on its task.
    expect(codes(importPlan(current, { version: 1, tasks: [{ id: 't1' }, { id: 'stray', seconds: 5, continuity: 'context' }] })))
      .toContain('warning:2:new_id')
  })

  it('warns about prompt tags that name a missing reference and fields it does not know', () => {
    const current = project()
    const result = importPlan(current, { version: 1, tasks: [
      { id: 't1', prompt: '<Picture 1> sings with <Audio 3>, then <Audio 3> fades', lora: 'x' },
      { id: 't2' },
    ] })

    expect(codes(result)).toEqual(['warning:1:unknown_field', 'warning:1:prompt_reference'])
    expect(result.issues[1].params).toEqual({ tag: '<Audio 3>' })
  })

  it('merges issues that say the same thing about several tasks', () => {
    const tag = (task: number, name: string) => ({ level: 'warning' as const, task, code: 'prompt_reference', params: { tag: name } })
    const groups = groupH3PlanIssues([
      { level: 'warning', code: 'foreign_ids', params: { count: 4 } },
      tag(2, '<Video 1>'), tag(1, '<Video 1>'), tag(1, '<Video 2>'), tag(3, '<Video 1>'), tag(5, '<Video 1>'),
    ])

    expect(groups.map((group) => [group.code, group.params?.tag ?? null, formatH3PlanTaskNumbers(group.tasks)])).toEqual([
      ['foreign_ids', null, ''], ['prompt_reference', '<Video 1>', '1–3, 5'], ['prompt_reference', '<Video 2>', '1'],
    ])
  })

  it('rejects files that are not version 1 plans', () => {
    expect(parseH3Plan('{').issues[0].code).toBe('invalid_json')
    expect(parseH3Plan(JSON.stringify({ version: 2, tasks: [] })).issues[0].code).toBe('invalid_version')
    expect(parseH3Plan(JSON.stringify({ version: 1 } satisfies Partial<H3PlanFile>)).issues[0].code).toBe('invalid_format')
  })
})
