import { describe, expect, it } from 'vitest'
import fixtures from '../../../tests/fixtures/h3_references.json'
import { h3NextTaskStart, h3ReferenceLabels, h3TaskReferenceSlots, planH3TaskReferences } from '@/lib/h3-references'
import type { MultiTrack, MultiTrackSegment } from '@/types/multitrack'

interface FixtureCase {
  name: string
  audio_only?: boolean
  frame_rate?: number
  task: MultiTrackSegment
  next_start: number | null
  tracks: MultiTrack[]
  expected: {
    images: string[]
    videos: Array<Record<string, unknown>>
    audios: Array<Record<string, unknown>>
    labels: ReturnType<typeof h3ReferenceLabels>
  }
}

const FIELDS = ['key', 'kind', 'start_frame', 'frames', 'soundtrack', 'fit'] as const

function pick(reference: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(FIELDS.map((field) => [field, reference[field]]))
}

describe('shared H3 reference plan contract', () => {
  for (const test of fixtures.cases as unknown as FixtureCase[]) {
    it(test.name, () => {
      const plan = planH3TaskReferences(test.tracks, test.task, test.next_start, {
        audioOnly: test.audio_only === true, frameRate: test.frame_rate,
      })
      // Cases list "fit" only when a reference is not fitted.
      const fitted = (references: Array<Record<string, unknown>>) => references.map((reference) => ({ fit: true, ...reference }))
      expect(plan.images.map((image) => image.id)).toEqual(test.expected.images)
      expect(plan.videos.map((video) => pick({ ...video }))).toEqual(fitted(test.expected.videos))
      expect(plan.audios.map((audio) => pick({ ...audio }))).toEqual(fitted(test.expected.audios))
      expect(h3ReferenceLabels(plan)).toEqual(test.expected.labels)
    })
  }

  it('finds the next task start regardless of list order', () => {
    const segment = (id: string, start: number, end: number): MultiTrackSegment => ({
      id, start_frame: start, end_frame: end, color: 'var(--primary)', content: { media_type: 'none' },
    })
    const tasks = [segment('c', 200, 300), segment('a', 0, 100), segment('b', 100, 200)]
    expect(h3NextTaskStart(tasks, tasks[1])).toBe(100)
    expect(h3NextTaskStart(tasks, tasks[0])).toBeNull()
  })
})

describe('H3 reference slots', () => {
  const clip = (id: string, start: number, end: number, mediaType: 'video' | 'audio'): MultiTrackSegment => ({
    id, start_frame: start, end_frame: end, color: '', content: { media_type: mediaType, file_path: `${id}.media` },
  })
  const track = (id: string, type: 'video' | 'audio', segments: MultiTrackSegment[], extra: Partial<MultiTrack> = {}): MultiTrack => ({
    id, name: id, type, color: '', muted: false, locked: false, segments, ...extra,
  })
  const task = (content: Partial<MultiTrackSegment['content']>) => ({
    start_frame: 0, end_frame: 48, content: { media_type: 'none' as const, task_mode: 'ref' as const, ...content },
  })
  const statuses = (slots: ReturnType<typeof h3TaskReferenceSlots>['audio']) => slots.map((slot) => [slot.key, slot.status])

  it('shows why each slot is or is not sent and drops placeholders of deleted tracks', () => {
    const tracks = [
      track('far', 'audio', [clip('far-1', 200, 300, 'audio')]),
      track('quiet', 'audio', [clip('quiet-1', 0, 100, 'audio')], { muted: true }),
      track('a1', 'audio', [clip('a1-1', 0, 100, 'audio')]),
      track('a2', 'audio', [clip('a2-1', 0, 100, 'audio')]),
    ]
    const slots = h3TaskReferenceSlots(tracks, task({ audios: [
      { id: 'p-far', source_type: 'track', track_id: 'far' },
      { id: 'p-quiet', source_type: 'track', track_id: 'quiet' },
      { id: 'p-gone', source_type: 'track', track_id: 'deleted' },
      { id: 'u1', source_type: 'input', file_path: 'one.wav', muted: true },
      { id: 'u2', source_type: 'input', file_path: 'two.wav' },
      { id: 'u3', source_type: 'input', file_path: 'three.wav' },
    ] }), null)

    expect(slots.customizable).toBe(true)
    expect(statuses(slots.audio)).toEqual([
      ['track:a1', 'active'], ['track:a2', 'active'], ['track:far', 'uncovered'], ['track:quiet', 'excluded'],
      ['upload:u1', 'muted'], ['upload:u2', 'active'], ['upload:u3', 'over_limit'],
    ])
  })

  it('puts a locked video first and lets other videos follow up to three', () => {
    const tracks = [
      track('lv', 'video', [clip('lv-1', 0, 100, 'video')], { audio_locked: true }),
      track('cam', 'video', [clip('cam-1', 0, 100, 'video')]),
    ]
    const slots = h3TaskReferenceSlots(tracks, task({ videos: [
      { id: 'v', source_type: 'input', file_path: 'clip.mp4' },
      { id: 'w', source_type: 'input', file_path: 'more.mp4' },
    ] }), null)
    expect(statuses(slots.video)).toEqual([
      ['locked:lv', 'active'], ['track:cam', 'active'], ['upload:v', 'active'], ['upload:w', 'over_limit'],
    ])
  })

  it('lists excluded timeline audio last with its reason and keeps the reason once included', () => {
    const tracks = [
      track('song', 'audio', [clip('song-1', 0, 100, 'audio')], { audio_locked: true }),
      track('quiet', 'audio', [clip('quiet-1', 0, 100, 'audio')], { muted: true }),
      track('bed', 'audio', [clip('bed-1', 0, 100, 'audio')]),
    ]
    const rows = (slots: ReturnType<typeof h3TaskReferenceSlots>['audio']) => slots.map((slot) => [slot.key, slot.status, slot.reason])

    expect(rows(h3TaskReferenceSlots(tracks, task({}), null).audio)).toEqual([
      ['track:bed', 'active', undefined], ['track:song', 'excluded', 'audio_locked'], ['track:quiet', 'excluded', 'track_muted'],
    ])
    const included = h3TaskReferenceSlots(tracks, task({
      audios: [{ id: 'p', source_type: 'track', track_id: 'song', muted: false }],
    }), null)
    expect(rows(included.audio)).toEqual([
      ['track:bed', 'active', undefined], ['track:song', 'active', 'audio_locked'], ['track:quiet', 'excluded', 'track_muted'],
    ])
  })

  it('reads the task lists only in ref and edit tasks', () => {
    const slots = h3TaskReferenceSlots([], task({
      task_mode: 'default', audios: [{ id: 'u', source_type: 'input', file_path: 'x.wav' }],
    }), null)
    expect(slots.customizable).toBe(false)
    expect(slots.audio).toEqual([])
  })
})
