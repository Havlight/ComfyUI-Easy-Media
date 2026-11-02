import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useMultiTrackHistory } from '@/hooks/use-multitrack-history'
import type { TrackData } from '@/types/multitrack'

const data = (total: number) => ({ total_length: total, frame_rate: 24, tracks: [] }) as unknown as TrackData

describe('MultiTrack Editor history', () => {
  it("undoes and redoes another node's change together with the edit that made it", () => {
    const onChange = vi.fn()
    const linked = { apply: vi.fn(), undo: vi.fn() }
    const { result, rerender } = renderHook(({ value }) => useMultiTrackHistory(value, onChange), { initialProps: { value: data(1) } })
    act(() => result.current.commitChange(data(2), linked))
    rerender({ value: data(2) })
    // Applied once the edit is recorded.
    expect(linked.apply).toHaveBeenCalledTimes(1)
    act(() => result.current.commitChange(data(3)))
    rerender({ value: data(3) })

    // The later edit had no linked change; undoing it leaves the other node alone.
    act(() => result.current.undo())
    rerender({ value: data(2) })
    expect(linked.undo).not.toHaveBeenCalled()
    act(() => result.current.undo())
    rerender({ value: data(1) })
    expect(linked.undo).toHaveBeenCalled()
    act(() => result.current.redo())
    expect(linked.apply).toHaveBeenCalledTimes(2)
    expect(onChange).toHaveBeenLastCalledWith(data(2))
  })

  it('records an edit that only changes another node', () => {
    const onChange = vi.fn()
    const linked = { apply: vi.fn(), undo: vi.fn() }
    const { result } = renderHook(() => useMultiTrackHistory(data(1), onChange))
    act(() => result.current.commitChange(data(1)))
    expect(result.current.canUndo).toBe(false)
    act(() => result.current.commitChange(data(1), linked))
    expect(result.current.canUndo).toBe(true)
    expect(linked.apply).toHaveBeenCalledTimes(1)
    act(() => result.current.undo())
    expect(linked.undo).toHaveBeenCalledTimes(1)
    // The timeline did not change, so the Editor's value is not rewritten.
    expect(onChange).not.toHaveBeenCalled()
  })
})
