import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { linkedH3Projects, useH3TimelineStatus } from '@/hooks/use-h3-timeline-status'
import type { TrackData } from '@/types/multitrack'

const data: TrackData = { frame_rate: 24, total_length: 90, tracks: [] }
function setup() {
  const project = { id: 2, type: 'easy multitrackProject', widgets: [{ name: 'project_name', value: 'test' }] }
  const editor = { id: 1, outputs: [{ links: [1] }], graph: {
    links: { 1: { target_id: 2 } }, getNodeById: () => project,
  } }
  const api = { addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addCustomEventListener: vi.fn(), removeCustomEventListener: vi.fn(),
    fetchApi: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ project_name: 'test', tasks: [
      { segment_id: 'a', index: 0, status: 'edited' },
      { segment_id: 'a:marker:cut', index: 1, status: 'parent_changed' },
      { segment_id: 'b', index: 2, status: 'saved' },
    ] }) }),
  }
  return { editor, api, app: { api } as unknown as ComfyApp }
}

afterEach(() => vi.useRealTimers())
describe('native timeline result status', () => {
  it('finds connected projects', () => {
    expect(linkedH3Projects(setup().editor)).toEqual(['test'])
    expect(linkedH3Projects(undefined)).toEqual([])
  })
  it('debounces edits and selects the original task for virtual marker dependencies', async () => {
    vi.useFakeTimers()
    const { editor, api, app } = setup()
    const { result, rerender, unmount } = renderHook(({ value }) => useH3TimelineStatus(app, editor, value, true),
      { initialProps: { value: data } })
    rerender({ value: { ...data, total_length: 124 } })
    await act(async () => { await vi.advanceTimersByTimeAsync(400) })
    expect(api.fetchApi).toHaveBeenCalledTimes(1)
    expect(result.current.affected).toEqual(['a'])
    unmount()
    expect(api.removeEventListener).toHaveBeenCalledWith('execution_success', expect.any(Function))
  })
  it('reports failed status queries without blocking editing', async () => {
    vi.useFakeTimers()
    const { editor, api, app } = setup()
    api.fetchApi.mockRejectedValueOnce(new Error('offline'))
    const { result } = renderHook(() => useH3TimelineStatus(app, editor, data, true))
    await act(async () => { await vi.advanceTimersByTimeAsync(400) })
    expect(result.current.error).toBe('offline')
    expect(result.current.affected).toEqual([])
  })
  it('does not fetch for non-H3 timelines or disconnected editors', async () => {
    vi.useFakeTimers()
    const { editor, api, app } = setup()
    renderHook(() => useH3TimelineStatus(app, editor, data, false))
    renderHook(() => useH3TimelineStatus(app, {}, data, true))
    await act(async () => { await vi.advanceTimersByTimeAsync(400) })
    expect(api.fetchApi).not.toHaveBeenCalled()
  })
})
