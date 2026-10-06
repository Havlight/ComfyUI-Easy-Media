import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { useSegmentLoraPreview } from '@/hooks/use-segment-lora-preview'
import type { ConnectedH3Project } from '@/lib/h3-project-snapshot'

afterEach(() => { cleanup(); vi.useRealTimers() })
const project: ConnectedH3Project = { id: '1', name: 'demo', tracks_info: { tracks: [], frame_rate: 24, total_length: 90 },
  project_snapshot: { known: true, segment_loras: { version: 1, rules: [] }, recipe: { sampling_mode: 'single', first_pass_only: false }, start_segment: 1, segment_count: -1 } }
function api() {
  const api = { addCustomEventListener: vi.fn(), removeCustomEventListener: vi.fn(), fetchApi: vi.fn() }
  api.fetchApi.mockResolvedValue({ ok: true, json: async () => ({ tasks: [], rules: [], conflicts: [] }) })
  return { api, app: { api } as unknown as ComfyApp }
}
describe('effective LoRA plan preview', () => {
  it('debounces edits, aborts stale requests and preserves explicit unknown status', async () => {
    vi.useFakeTimers()
    const { app, api: client } = api()
    const { rerender, unmount } = renderHook(({ value }) => useSegmentLoraPreview(app, value, true), { initialProps: { value: project } })
    await act(async () => { await vi.advanceTimersByTimeAsync(350) })
    const signal = client.fetchApi.mock.calls[0][1].signal as AbortSignal
    const changed = { ...project, project_snapshot: { ...project.project_snapshot, known: false } }
    rerender({ value: changed })
    expect(signal.aborted).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(350) })
    expect(JSON.parse(client.fetchApi.mock.calls[1][1].body).project_snapshot.known).toBe(false)
    unmount()
    expect(client.removeCustomEventListener).toHaveBeenCalled()
  })
  it('uses only the matching Project runtime summary and clears it after edits', () => {
    const { app, api: client } = api()
    const { result, rerender } = renderHook(({ value }) => useSegmentLoraPreview(app, value, false), { initialProps: { value: project } })
    const handler = client.addCustomEventListener.mock.calls[0][1]
    act(() => handler(new CustomEvent('test', { detail: { project_node_id: 'other', tasks: [], rules: [], conflicts: [] } })))
    expect(result.current.resolved).toBe(false)
    act(() => handler(new CustomEvent('test', { detail: { project_node_id: '1', tasks: [], rules: [], conflicts: [] } })))
    expect(result.current.resolved).toBe(true)
    rerender({ value: { ...project, project_snapshot: { ...project.project_snapshot, start_segment: 2 } } })
    expect(result.current.resolved).toBe(false)
    expect(client.fetchApi).not.toHaveBeenCalled()
  })
  it('displays request errors without replacing saved rules', async () => {
    vi.useFakeTimers()
    const { app, api: client } = api()
    client.fetchApi.mockResolvedValue({ ok: false, status: 400, json: async () => ({ error: 'invalid range' }) })
    const { result } = renderHook(() => useSegmentLoraPreview(app, project, true))
    await act(async () => { await vi.advanceTimersByTimeAsync(350) })
    expect(result.current.error).toBe('invalid range')
    expect(result.current.summary).toBeNull()
  })
})
