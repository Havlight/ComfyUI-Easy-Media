import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { describe, expect, it, vi } from 'vitest'
import { applyAgentTab, readAgentTab } from '@/lib/agent-tabs'
import type { TrackData } from '@/types/multitrack'

const NEXT = 'Workspace.NextOpenedWorkflow'

function trackData(prompt?: string): TrackData {
  return {
    total_length: 248, frame_rate: 24, h3_native: { version: 2 },
    tracks: [{ id: 'tasks', name: 'Task 0', type: 'task', color: '', muted: false, locked: false, segments: prompt ? [
      { id: 's01', start_frame: 0, end_frame: 124, color: '', content: { media_type: 'none', task_mode: 'default', continuity_mode: 'shot', user_prompt: prompt, images: [] } },
    ] : [] }],
  } as unknown as TrackData
}

function setup() {
  const editor = { id: 14, type: 'easy multiTrackEditor', widgets: [
    { name: 'format', value: 'MiniMax' }, { name: 'track_data', value: JSON.stringify(trackData()) }] }
  const project = { id: 15, type: 'easy multitrackProject', widgets: [
    { name: 'project_name', value: '' }, { name: 'project_save', value: 'override' },
    { name: 'segment_start_number', value: 1 }, { name: 'segment_count', value: -1 }] }
  const graphA = () => ({ id: 'graph-a', links: [], version: 0.4, nodes: [editor, project].map((node) => ({
    id: node.id, type: node.type, widgets_values: node.widgets.map((widget) => widget.value) })) })
  const tracker = { captureCanvasState: vi.fn() }
  // A hidden tab is read from the state ComfyUI kept when it was left, a reactive proxy.
  const tabA = { path: 'workflows/mv.json', filename: 'mv', isModified: false, isTemporary: false, activeState: new Proxy(graphA(), {}),
    load: vi.fn(), changeTracker: tracker }
  const tabB = { path: 'workflows/other.json', filename: 'other', isModified: true, isTemporary: true,
    activeState: { id: 'graph-b-kept', links: [], version: 0.4, nodes: [] }, load: vi.fn(), changeTracker: null }
  // The canvas shows the tab in view.
  const serialize = vi.fn(() => active === tabA ? graphA() : { id: 'graph-b', links: [], version: 0.4, nodes: [] })
  const open = [tabA, tabB]
  let active: typeof tabA | typeof tabB = tabB
  const store = {
    get openWorkflows() { return open },
    get activeWorkflow() { return active },
    isBusy: false,
    isActive: (workflow: unknown) => workflow === active,
    isOpen: (workflow: unknown) => open.includes(workflow as typeof tabA),
  }
  const execute = vi.fn(async (command: string) => {
    active = open[(open.indexOf(active) + (command === NEXT ? 1 : open.length - 1)) % open.length]
  })
  const graph = {
    serialize,
    getNodeById: (id: number | string) => [editor, project].find((node) => node.id === Number(id)) ?? null,
    beforeChange: vi.fn(),
    afterChange: vi.fn(),
  }
  const app = {
    extensionManager: { workflow: store, command: { execute } },
    rootGraph: graph,
    canvas: { setDirty: vi.fn() },
    api: { queuePrompt: vi.fn().mockResolvedValue({ prompt_id: 'prompt-1' }) },
    queuePrompt: vi.fn(async () => {
      await app.api.queuePrompt(0, { workflow: serialize(), output: {} })
      return true
    }),
  }
  return { app, nativeApp: app as unknown as ComfyApp, editor, project, tracker, execute, graph, isActive: (tab: unknown) => tab === active, tabA, tabB }
}

describe('agent access to open workflow tabs', () => {
  it('lists the tabs and reads one with its unsaved edits', async () => {
    const { nativeApp } = setup()
    const listed = await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't' })
    expect(listed).toEqual({ status: 'read', tabs: [
      { key: 'workflows/mv.json', name: 'mv', active: false, modified: false, temporary: false },
      { key: 'workflows/other.json', name: 'other', active: true, modified: true, temporary: true },
    ] })
    // The tab in view is read from the canvas; another tab from the state ComfyUI kept for it.
    const visible = await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't', tab: 'workflows/other.json' })
    expect(visible).toMatchObject({ tab: { key: 'workflows/other.json', workflow: { id: 'graph-b' }, plans: {} } })
    const hidden = await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't', tab: 'workflows/mv.json' })
    expect(hidden.status).toBe('read')
    const tab = hidden.status === 'read' ? hidden.tab! : undefined
    expect(tab?.workflow).toMatchObject({ id: 'graph-a' })
    expect(tab?.plans['14']).toMatchObject({ format: 'easy-media-h3-plan', tasks: [] })
    expect(await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't', tab: 'workflows/gone.json' }))
      .toMatchObject({ status: 'failed', error: expect.stringContaining('open tabs: workflows/mv.json, workflows/other.json') })
  })

  it('finds a tab by its name when no other open tab has it', async () => {
    const { app, nativeApp } = setup()
    for (const name of ['mv', 'mv.json']) {
      expect(await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't', tab: name }))
        .toMatchObject({ status: 'read', tab: { key: 'workflows/mv.json' } })
    }
    app.extensionManager.workflow.openWorkflows.push({ ...app.extensionManager.workflow.openWorkflows[0], path: 'workflows/old/mv.json' })
    expect(await readAgentTab(nativeApp, { kind: 'tabs', request_id: 'r', claim_token: 't', tab: 'mv' })).toEqual({
      status: 'failed', error: 'Several open tabs are named mv; use one of their keys: workflows/mv.json, workflows/old/mv.json' })
  })

  it('sets the Editor and Project of another tab as one undoable edit, then returns to the tab in view', async () => {
    const { app, nativeApp, editor, project, tracker, execute, graph, isActive, tabB } = setup()
    const result = await applyAgentTab(nativeApp, { kind: 'apply', request_id: 'r', claim_token: 't', tab: 'workflows/mv.json',
      queue: false, changes: { editor_id: 14, project_id: 15, track_data: trackData('A dancer'),
        project: { project_name: 'mv', segment_start_number: 2, segment_count: -1 } } })
    expect(result).toEqual({ status: 'applied' })
    expect(JSON.parse(editor.widgets[1].value as string).tracks[0].segments[0].id).toBe('s01')
    expect(project.widgets.map((widget) => widget.value)).toEqual(['mv', 'override', 2, -1])
    expect(graph.beforeChange).toHaveBeenCalledTimes(1)
    expect(graph.afterChange).toHaveBeenCalledTimes(1)
    expect(tracker.captureCanvasState).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledTimes(2)
    expect(isActive(tabB)).toBe(true)
    expect(app.queuePrompt).not.toHaveBeenCalled()
  })

  it('changes nothing when the named Editor is not one, and still returns to the tab in view', async () => {
    const { app, nativeApp, editor, isActive, tabB } = setup()
    const before = editor.widgets[1].value
    const result = await applyAgentTab(nativeApp, { kind: 'apply', request_id: 'r', claim_token: 't', tab: 'workflows/mv.json',
      queue: true, changes: { editor_id: 15, track_data: trackData('Late') } })
    expect(result).toMatchObject({ status: 'failed', error: expect.stringContaining('is not a MultiTrack Editor') })
    expect(editor.widgets[1].value).toBe(before)
    expect(app.queuePrompt).not.toHaveBeenCalled()
    expect(isActive(tabB)).toBe(true)
  })

  it('runs a tab with the native Run button', async () => {
    const { app, nativeApp } = setup()
    const result = await applyAgentTab(nativeApp, { kind: 'apply', request_id: 'r', claim_token: 't', tab: 'workflows/mv.json', queue: true })
    expect(result).toEqual({ status: 'queued', prompt_id: 'prompt-1' })
    expect(app.queuePrompt).toHaveBeenCalledExactlyOnceWith(0, 1)
  })
})
