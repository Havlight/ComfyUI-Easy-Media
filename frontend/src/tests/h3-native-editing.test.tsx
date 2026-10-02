import { useState } from 'react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useH3NativeEditing } from '@/hooks/use-h3-native-editing'
import { useMultiTrackHistory } from '@/hooks/use-multitrack-history'
import { splitTrackSegmentAtFrame, applySmartSplitToMatchingTasks } from '@/lib/smart-split'
import type { TrackData } from '@/types/multitrack'

function initialData(native = true): TrackData {
  return { frame_rate: 24, total_length: 243,
    ...(native ? { h3_native: { version: 1 as const, allow_vae_fallback: false } } : {}),
    tracks: [{ id: 'task', name: 'Task', type: 'task', color: 'var(--primary)', muted: false, locked: false,
      segments: [{ id: 'a', start_frame: 0, end_frame: 243, color: 'var(--primary)', content: { media_type: 'none', continuity_mode: 'shot' } }] }] }
}

function useEditor(initial: TrackData) {
  const [data, setData] = useState(initial)
  const history = useMultiTrackHistory(data, setData)
  const editing = useH3NativeEditing(data, 'MiniMax', history.commitChange)
  return { data, ...history, ...editing }
}

describe('native editor transactions', () => {
  it('commits legal split geometry and restores the complete policy and dependency roles on undo/redo', () => {
    const { result } = renderHook(() => useEditor(initialData()))
    act(() => result.current.commitEdit(() => splitTrackSegmentAtFrame(result.current.data, 'a', 120)))
    expect(result.current.data.tracks[0].segments.map((s) => [s.start_frame, s.end_frame, s.content.continuity_mode]))
      .toEqual([[0, 124, 'shot'], [124, 243, 'context']])
    act(() => result.current.undo())
    expect(result.current.data.tracks[0].segments).toHaveLength(1)
    act(() => result.current.redo())
    expect(result.current.data.tracks[0].segments).toHaveLength(2)
    expect(result.current.data.h3_native?.allow_vae_fallback).toBe(false)
  })

  it('keeps legacy geometry until migration is reviewed, and can undo the upgrade', () => {
    const initial = initialData(false)
    initial.tracks[0].segments[0].end_frame = 240
    const { result } = renderHook(() => useEditor(initial))
    act(() => result.current.previewMigration())
    expect(result.current.data.tracks[0].segments[0].end_frame).toBe(240)
    expect(result.current.migration?.tracks[0].segments[0].end_frame).toBe(243)
    act(() => result.current.applyMigration())
    expect(result.current.data.h3_native?.allow_vae_fallback).toBe(false)
    act(() => result.current.undo())
    expect(result.current.data.h3_native).toBeUndefined()
    expect(result.current.data.tracks[0].segments[0].end_frame).toBe(240)
  })

  it('does not put a rejected short split into undo history', () => {
    const initial = initialData()
    initial.tracks[0].segments[0].end_frame = 39
    const { result } = renderHook(() => useEditor(initial))
    act(() => result.current.commitEdit(() => splitTrackSegmentAtFrame(result.current.data, 'a', 20)))
    expect(result.current.error?.code).toBe('SPLIT_SHORT')
    expect(result.current.data).toEqual(initial)
    expect(result.current.canUndo).toBe(false)
  })

  it('keeps source positions and native rules when VAE fallback is enabled', () => {
    const initial = initialData()
    initial.tracks.push({ ...initial.tracks[0], id: 'video', type: 'video', locked: true,
      segments: [{ ...initial.tracks[0].segments[0], id: 'media', end_frame: 243, content: { media_type: 'video', file_path: 'source.mp4' } }] })
    const { result } = renderHook(() => useEditor(initial))
    act(() => result.current.commitEdit({ ...result.current.data, h3_native: { version: 1, allow_vae_fallback: true } }))
    act(() => result.current.commitEdit(() => applySmartSplitToMatchingTasks(result.current.data, 'media', { ranges: [[0, 120], [120, 243]] })))
    expect(result.current.data.tracks[0].segments.map((s) => s.end_frame)).toEqual([124, 243])
    expect(result.current.data.tracks[1].segments[0].end_frame).toBe(243)
    expect(result.current.data.h3_native?.allow_vae_fallback).toBe(true)
  })

  it('enables strict native timing for the first edit of an empty H3 timeline', () => {
    const initial = initialData(false)
    initial.tracks[0].segments = []
    const { result } = renderHook(() => useEditor(initial))
    act(() => result.current.commitEdit(initialData(false)))
    expect(result.current.data.h3_native).toEqual({ version: 1, allow_vae_fallback: false })
  })
})

it('keeps native policy inactive for other formats and reviews timing when switching back', () => {
  const { result, rerender } = renderHook(({ format }) => {
    const [data, setData] = useState(initialData())
    return { data, ...useH3NativeEditing(data, format, setData) }
  }, { initialProps: { format: 'Wan' } })
  const edited = initialData()
  edited.tracks[0].segments[0].end_frame = 240
  delete edited.h3_native
  act(() => result.current.commitEdit(edited))
  expect(result.current.data.tracks[0].segments[0].end_frame).toBe(240)
  expect(result.current.data.h3_native?.allow_vae_fallback).toBe(false)
  rerender({ format: 'MiniMax' })
  expect(result.current.data.tracks[0].segments[0].end_frame).toBe(240)
  expect(result.current.migration?.tracks[0].segments[0].end_frame).toBe(243)
})
