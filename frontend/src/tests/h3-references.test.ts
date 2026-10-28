import { describe, expect, it } from 'vitest'
import fixtures from '../../../tests/fixtures/h3_references.json'
import { h3NextTaskStart, h3ReferenceLabels, planH3TaskReferences } from '@/lib/h3-references'
import type { MultiTrack, MultiTrackSegment } from '@/types/multitrack'

interface FixtureCase {
  name: string
  audio_only?: boolean
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

const FIELDS = ['key', 'kind', 'start_frame', 'frames', 'soundtrack'] as const

function pick(reference: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(FIELDS.map((field) => [field, reference[field]]))
}

describe('shared H3 reference plan contract', () => {
  for (const test of fixtures.cases as unknown as FixtureCase[]) {
    it(test.name, () => {
      const plan = planH3TaskReferences(test.tracks, test.task, test.next_start, { audioOnly: test.audio_only === true })
      expect(plan.images.map((image) => image.id)).toEqual(test.expected.images)
      expect(plan.videos.map((video) => pick({ ...video }))).toEqual(test.expected.videos)
      expect(plan.audios.map((audio) => pick({ ...audio }))).toEqual(test.expected.audios)
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
