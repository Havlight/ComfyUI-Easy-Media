import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { H3SegmentLorasWidget } from '@/components/widgets/H3SegmentLorasWidget'
import type { ReactWidgetProps } from '@/lib/create-react-widget'
import type { SegmentLoraPlan } from '@/types/segment-loras'
import { translate } from '@/lib/i18n'

afterEach(cleanup)
function setup() {
  const history: SegmentLoraPlan[] = []
  const node = { id: 1, graph: { beforeChange: vi.fn(), afterChange: vi.fn(), links: {}, getNodeById: () => null } }
  const api = { fetchApi: vi.fn().mockResolvedValue({ ok: true, json: async () => ['folder/a.safetensors', 'b.safetensors'] }),
    addCustomEventListener: vi.fn(), removeCustomEventListener: vi.fn() }
  const app = { api } as unknown as ComfyApp
  let restore: (value: SegmentLoraPlan) => void = () => undefined
  function Fixture() {
    const [value, setValue] = useState<SegmentLoraPlan>({ version: 1, rules: [] })
    restore = setValue
    return <H3SegmentLorasWidget value={value} onChange={(next) => { history.push(next); setValue(next) }} node={node} app={app}
      inputName="rules" widget={{} as ReactWidgetProps<SegmentLoraPlan>['widget']} />
  }
  render(<Fixture />)
  return { node, history, api, restore: (value: SegmentLoraPlan) => act(() => restore(value)) }
}

describe('segment LoRA controls', () => {
  it('adds, edits, disables, deletes and restores serializable rules with graph history boundaries', async () => {
    const { history, node, restore } = setup()
    await waitFor(() => expect(screen.getByText('No extra LoRAs. The Loader settings are used.')).toBeTruthy())
    fireEvent.click(screen.getByText('Add LoRA'))
    expect(history.at(-1)?.rules[0]).toMatchObject({ lora: '', start_segment: 1, segment_count: -1, strength: 1, stage: 'all', enabled: true })
    expect((screen.getByLabelText('Segments') as HTMLInputElement).value).toBe('Until end')
    const start = screen.getByLabelText('Start segment')
    fireEvent.change(start, { target: { value: '3' } }); fireEvent.blur(start)
    expect(history.at(-1)?.rules[0].start_segment).toBe(3)
    fireEvent.click(screen.getByLabelText('Enable row 1'))
    expect(history.at(-1)?.rules[0].enabled).toBe(false)
    const saved = JSON.parse(JSON.stringify(history.at(-1))) as SegmentLoraPlan
    fireEvent.click(screen.getByLabelText('Delete row 1'))
    expect(history.at(-1)?.rules).toHaveLength(0)
    restore(saved)
    expect((screen.getByLabelText('Start segment') as HTMLInputElement).value).toBe('3')
    expect(node.graph.beforeChange).toHaveBeenCalledTimes(4)
    expect(node.graph.afterChange).toHaveBeenCalledTimes(4)
  })
  it('commits count -1 as Until end and supports keyboard counts without a zero state', async () => {
    const { history } = setup()
    fireEvent.click(screen.getByText('Add LoRA'))
    const count = screen.getByLabelText('Segments')
    fireEvent.focus(count)
    fireEvent.keyDown(count, { key: 'ArrowUp' })
    expect(history.at(-1)?.rules[0].segment_count).toBe(1)
    fireEvent.keyDown(count, { key: 'ArrowDown' })
    expect(history.at(-1)?.rules[0].segment_count).toBe(-1)
    fireEvent.change(count, { target: { value: '5' } }); fireEvent.blur(count)
    expect(history.at(-1)?.rules[0].segment_count).toBe(5)
    await waitFor(() => expect(screen.getByLabelText('Segments')).toBeTruthy())
  })
  it('searches registered file names and refreshes the model list', async () => {
    const { history, api } = setup()
    await waitFor(() => expect(api.fetchApi).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByText('Add LoRA'))
    fireEvent.click(screen.getByLabelText('LoRA'))
    fireEvent.change(screen.getByLabelText('Search LoRAs'), { target: { value: 'folder' } })
    expect(screen.queryByRole('option', { name: 'b.safetensors' })).toBeNull()
    fireEvent.click(screen.getByRole('option', { name: 'folder/a.safetensors' }))
    expect(history.at(-1)?.rules[0].lora).toBe('folder/a.safetensors')
    fireEvent.click(screen.getByLabelText('Refresh LoRA files'))
    await waitFor(() => expect(api.fetchApi).toHaveBeenCalledTimes(2))
  })
  it('keeps stage controls advanced and explains disconnected preview', async () => {
    setup()
    fireEvent.click(screen.getByText('Add LoRA'))
    expect(screen.queryByLabelText('Apply to')).toBeNull()
    fireEvent.click(screen.getByLabelText('Advanced settings'))
    expect(screen.getByLabelText('Apply to')).toBeTruthy()
    fireEvent.click(screen.getByText('Effective plan'))
    expect(screen.getByText('Connect this node to Project’s segment_loras input.')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('All stages')).toBeTruthy())
  })
  it('says when the node is bypassed or muted, and summarizes what its rules would do', async () => {
    const rules: SegmentLoraPlan = { version: 1, rules: [
      { id: 'r', enabled: true, lora: 'b.safetensors', start_segment: 1, segment_count: -1, strength: 0.5, stage: 'all' }] }
    const nodes: Record<string, object> = {}
    const graph = { beforeChange: vi.fn(), afterChange: vi.fn(), getNodeById: (id: string | number) => nodes[String(id)] ?? null,
      links: { 1: { origin_id: 3, target_id: 2 }, 2: { origin_id: 1, target_id: 2 } } }
    const node = { id: 1, type: 'easy h3SegmentLoras', mode: 4, widgets: [{ name: 'rules', value: JSON.stringify(rules) }], outputs: [{ links: [2] }], graph }
    nodes[1] = node
    nodes[2] = { id: 2, type: 'easy multitrackProject', widgets: [{ name: 'project_name', value: 'p' }],
      inputs: [{ name: 'tracks_info', link: 1 }, { name: 'segment_loras', link: 2 }], graph }
    nodes[3] = { id: 3, type: 'easy multiTrackEditor', outputs: [{ links: [1] }], graph,
      widgets: [{ name: 'format', value: 'MiniMax' }, { name: 'track_data', value: JSON.stringify({ tracks: [] }) }] }
    const fetchApi = vi.fn(async (path: string, _init?: RequestInit) => ({ ok: true,
      json: async () => path === '/models/loras' ? ['b.safetensors'] : { pending: false, tasks: [], rules: [], conflicts: [] } }))
    const app = { api: { fetchApi, addCustomEventListener: vi.fn(), removeCustomEventListener: vi.fn() } } as unknown as ComfyApp
    render(<H3SegmentLorasWidget value={rules} onChange={vi.fn()} node={node} app={app}
      inputName="rules" widget={{} as ReactWidgetProps<SegmentLoraPlan>['widget']} />)
    expect(screen.getByText(translate('en', 'segmentLoras.inactive'))).toBeTruthy()
    fireEvent.click(screen.getByText('Effective plan'))
    // The Project's snapshot has no rules for a node runs leave out; the summary previews the node's own.
    await waitFor(() => expect(fetchApi.mock.calls.some(([path]) => path === '/easy-media/project/segment-loras-preview')).toBe(true))
    const [, init] = fetchApi.mock.calls.find(([path]) => path === '/easy-media/project/segment-loras-preview')!
    expect(JSON.parse(String(init?.body)).project_snapshot.segment_loras).toEqual(rules)
  })
  it('provides localized labels for both supported message catalogs', () => {
    for (const locale of ['en', 'zh']) for (const key of ['all', 'first', 'second', 'pending', 'upstream', 'upstreamDuplicate', 'countHelp', 'inactive']) {
      expect(translate(locale, `segmentLoras.${key}`)).not.toBe(`segmentLoras.${key}`)
    }
  })
})
