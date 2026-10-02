import { describe, expect, it } from 'vitest'
import fixtures from '../../../tests/fixtures/h3_native_timing.json'
import { h3NativeSplitFrame, reconcileH3NativeTimeline, snapH3NativeDuration, splitH3NativeTask } from '@/lib/h3-native-timing'
import type { MultiTrackSegment, TrackData } from '@/types/multitrack'

const segment = (id: string, start: number, end: number, continuation = false): MultiTrackSegment => ({
  id, start_frame: start, end_frame: end, color: 'var(--primary)',
  content: { media_type: 'none', continuity_mode: continuation ? 'context' : 'shot' },
})
const data = (segments: MultiTrackSegment[]): TrackData => ({
  frame_rate: 24, total_length: 1000, h3_native: { version: 1, allow_vae_fallback: false },
  tracks: [{ id: 'tasks', name: 'Task 0', type: 'task', color: 'var(--primary)', muted: false, locked: false, segments }],
})

describe('shared H3 native timing contract', () => {
  for (const test of fixtures.durations) it(`snaps ${test.frames}, continuation=${test.continuation}`, () => {
    expect(snapH3NativeDuration(test.frames, test.continuation)).toBe(test.expected)
  })
  for (const test of fixtures.splits) it(`splits at ${test.requested}, continuation=${test.continuation}`, () => {
    expect(h3NativeSplitFrame(test.start, test.end, test.requested, test.continuation)).toBe(test.expected)
  })
  it('uses identical geometry with fallback on or off', () => {
    const input = data([segment('a', 0, 240), segment('b', 240, 480, true)])
    const strict = reconcileH3NativeTimeline(input)
    expect(strict.tracks[0].segments.map((s) => [s.start_frame, s.end_frame])).toEqual([[0, 243], [243, 481]])
    input.h3_native!.allow_vae_fallback = true
    expect(reconcileH3NativeTimeline(input).tracks).toEqual(strict.tracks)
  })
  it('preserves total duration when splitting a Shot into Shot and continuation', () => {
    const parts = splitH3NativeTask(segment('a', 0, 243), 120, 'b')
    expect(parts.map((s) => [s.start_frame, s.end_frame, s.content.continuity_mode])).toEqual([
      [0, 124, 'shot'], [124, 243, 'context'],
    ])
  })
  it('promotes a remaining first continuation to Shot without changing source media', () => {
    const input = data([segment('b', 243, 481, true)])
    const media = { ...input.tracks[0], id: 'media', type: 'video' as const, segments: [segment('m', 0, 240)] }
    input.tracks.push(media)
    const result = reconcileH3NativeTimeline(input)
    expect(result.tracks[0].segments[0].content.continuity_mode).toBe('shot')
    expect(result.tracks[1]).toBe(media)
    expect(input.tracks[0].segments[0].content.continuity_mode).toBe('context')
  })
  it('leaves legacy workflows untouched and rejects edits of locked task tracks', () => {
    const input = data([segment('a', 0, 240)])
    input.tracks[0].locked = true
    expect(() => reconcileH3NativeTimeline(input)).toThrow('Unlock')
    delete input.h3_native
    expect(reconcileH3NativeTimeline(input)).toBe(input)
  })
  it('keeps geometry valid through repeated split and deletion transactions', () => {
    let state = data([segment('root', 0, 243)])
    for (let i = 0; i < 80; i++) {
      const tasks = state.tracks[0].segments
      const last = tasks.at(-1)!
      tasks.push(segment(`tail-${i}`, last.end_frame, last.end_frame + 233 + (i % 17), true))
      if (i % 3 === 0 && tasks.length > 2) tasks.splice(1, 1)
      state = reconcileH3NativeTimeline(state)
      expect(reconcileH3NativeTimeline(state)).toEqual(state)
      state.tracks[0].segments.forEach((s, index) => {
        expect((s.end_frame - s.start_frame) % 17).toBe(index === 0 ? 5 : 0)
        if (index) expect(s.start_frame).toBe(state.tracks[0].segments[index - 1].end_frame)
      })
    }
  })
})

it('aligns generation markers without moving source media or the playhead', () => {
  const input = data([segment('a', 0, 243)])
  input.task_markers = [{ id: 'cut', frame: 120 }]
  expect(reconcileH3NativeTimeline(input).task_markers).toEqual([{ id: 'cut', frame: 124 }])
})
