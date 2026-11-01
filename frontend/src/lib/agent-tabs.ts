import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { exportH3Plan, type H3PlanFile } from '@/lib/h3-plan'
import { normalizeTrackData } from '@/lib/multitrack-utils'
import { errorMessage, nativeQueueBusy, queueCurrentWorkflow, type QueueResult } from '@/lib/native-queue'
import type { TrackData } from '@/types/multitrack'

const EDITOR_TYPE = 'easy multiTrackEditor'
const PROJECT_TYPE = 'easy multitrackProject'
const NEXT_TAB = 'Workspace.NextOpenedWorkflow'
const PREVIOUS_TAB = 'Workspace.PreviousOpenedWorkflow'

type Workflow = ReturnType<ComfyApp['rootGraph']['serialize']>

/** The parts of ComfyUI's workflow store this module uses: open tabs and the saved workflows. */
interface StoreWorkflow {
  path: string
  filename: string
  isModified: boolean
  isTemporary: boolean
  activeState: unknown
  load: () => Promise<{ activeState: unknown }>
  changeTracker?: { captureCanvasState?: () => void } | null
}
interface WorkflowTabs {
  openWorkflows: StoreWorkflow[]
  activeWorkflow: StoreWorkflow | null
  isBusy: boolean
  isActive: (workflow: StoreWorkflow) => boolean
  isOpen: (workflow: StoreWorkflow) => boolean
  /** Saved workflows, open or not; filled by loadWorkflows like the Workflows sidebar. */
  persistedWorkflows?: StoreWorkflow[]
  loadWorkflows?: () => Promise<void>
}

function tabsOf(app: ComfyApp): WorkflowTabs {
  return app.extensionManager.workflow as unknown as WorkflowTabs
}

export interface AgentTab {
  key: string
  name: string
  active: boolean
  modified: boolean
  temporary: boolean
}

/** A saved workflow that is not open as a tab. */
export interface SavedWorkflow {
  key: string
  name: string
}

export interface AgentTabContent {
  key: string
  name: string
  /** False for a saved workflow read from its file without opening it. */
  open: boolean
  workflow: Workflow
  /** The H3 plan each Editor exports, by node id. */
  plans: Record<string, H3PlanFile>
}

export interface TabReadJob {
  kind: 'tabs'
  request_id: string
  claim_token: string
  tab?: string | null
}

export interface TabChanges {
  editor_id: number | string
  track_data: TrackData
  project_id?: number | string
  project?: { project_name?: string; segment_start_number?: number; segment_count?: number }
}

export interface TabApplyJob {
  kind: 'apply'
  request_id: string
  claim_token: string
  tab: string
  queue: boolean
  changes?: TabChanges | null
}

export type TabReadResult =
  | { status: 'read'; tabs: AgentTab[]; saved: SavedWorkflow[]; tab?: AgentTabContent }
  | { status: 'failed'; error: string }
/** `opened` when a saved workflow was opened as a tab for this job. */
export type TabApplyResult = ({ status: 'applied' } | QueueResult) & { opened?: boolean }

interface SerializedNode { id: number | string; type?: string; widgets_values?: unknown }

function nodesOf(workflow: Workflow, type: string): SerializedNode[] {
  const nodes = (workflow as { nodes?: unknown }).nodes
  return (Array.isArray(nodes) ? nodes as SerializedNode[] : []).filter((node) => node.type === type)
}

function trackDataValue(node: SerializedNode): string | undefined {
  const values = Array.isArray(node.widgets_values) ? node.widgets_values : []
  return values.find((value): value is string => {
    if (typeof value !== 'string') return false
    try {
      const parsed: unknown = JSON.parse(value)
      return !!parsed && typeof parsed === 'object' && ['tracks', 'total_length', 'frame_rate'].every((key) => key in parsed)
    } catch {
      return false
    }
  })
}

function editorPlans(workflow: Workflow): Record<string, H3PlanFile> {
  const plans: Record<string, H3PlanFile> = {}
  for (const node of nodesOf(workflow, EDITOR_TYPE)) {
    const value = trackDataValue(node)
    if (value) plans[String(node.id)] = exportH3Plan(normalizeTrackData(JSON.parse(value) as TrackData))
  }
  return plans
}

function listTabs(app: ComfyApp): AgentTab[] {
  const store = tabsOf(app)
  return store.openWorkflows.map((workflow) => ({
    key: workflow.path,
    name: workflow.filename,
    active: store.isActive(workflow),
    modified: workflow.isModified,
    temporary: workflow.isTemporary,
  }))
}

/**
 * Saved workflows that are not open, once the store has listed them. The store lists every file
 * in the workflows folder (notes, images, node packs), so only .json files count.
 */
async function savedWorkflows(store: WorkflowTabs): Promise<StoreWorkflow[]> {
  await store.loadWorkflows?.()
  return (store.persistedWorkflows ?? []).filter(
    (workflow) => /\.json$/i.test(workflow.path) && !store.isOpen(workflow),
  )
}

const withoutJson = (name: string) => name.replace(/\.json$/i, '')

/**
 * A tab or saved workflow by its key, or by a name only one has (".json" optional). Open tabs
 * are matched by name before saved workflows.
 */
async function findWorkflow(app: ComfyApp, key: string): Promise<StoreWorkflow> {
  const store = tabsOf(app)
  const open = store.openWorkflows
  const saved = await savedWorkflows(store)
  const byKey = [...open, ...saved].find((workflow) => workflow.path === key)
  if (byKey) return byKey
  for (const [group, label] of [[open, 'open tabs'], [saved, 'saved workflows']] as const) {
    const named = group.filter((workflow) => withoutJson(workflow.filename) === withoutJson(key))
    if (named.length === 1) return named[0]
    if (named.length) throw new Error(`Several ${label} are named ${key}; use one of their keys: ${named.map((workflow) => workflow.path).join(', ')}`)
  }
  throw new Error(`No open tab or saved workflow has the key or name ${key}; open tabs: ${open.map((workflow) => workflow.path).join(', ')}. The tabs command with --saved finds saved workflows.`)
}

/** Open a saved workflow as its own tab the way the Workflows sidebar does, so saving writes back to its file. */
async function openSaved(app: ComfyApp, workflow: StoreWorkflow): Promise<void> {
  const store = tabsOf(app)
  const state = workflow.activeState ?? (await workflow.load()).activeState
  const loaded: unknown = await app.loadGraphData(
    JSON.parse(JSON.stringify(state)) as Workflow, true, true, workflow as unknown as string,
  )
  for (let wait = 0; store.isBusy && wait < 100; wait += 1) await new Promise((resolve) => setTimeout(resolve, 50))
  if (loaded === false || !store.isActive(workflow)) throw new Error(`Could not open the saved workflow ${workflow.path}`)
}

/** The workflow's content, unsaved edits included: the canvas for the visible tab, its kept or saved state otherwise. */
async function tabWorkflow(app: ComfyApp, tab: StoreWorkflow): Promise<Workflow> {
  if (tabsOf(app).isActive(tab)) return app.rootGraph.serialize()
  const state = tab.activeState ?? (await tab.load()).activeState
  if (!state) throw new Error(`The tab ${tab.path} has no content to read`)
  // ComfyUI keeps the state as a reactive proxy, which structuredClone rejects.
  return JSON.parse(JSON.stringify(state)) as Workflow
}

/** Switch tabs with the same commands as the keyboard shortcuts, so ComfyUI keeps each tab's own state. */
async function activateTab(app: ComfyApp, tab: StoreWorkflow): Promise<void> {
  const store = tabsOf(app)
  for (let step = 0; step < store.openWorkflows.length && !store.isActive(tab); step += 1) {
    const open = store.openWorkflows
    const from = open.findIndex((workflow) => store.isActive(workflow))
    const to = open.indexOf(tab)
    if (from < 0 || to < 0) break
    const forward = (to - from + open.length) % open.length
    await Promise.resolve(app.extensionManager.command.execute(forward <= open.length - forward ? NEXT_TAB : PREVIOUS_TAB))
    for (let wait = 0; store.isBusy && wait < 100; wait += 1) await new Promise((resolve) => setTimeout(resolve, 50))
  }
  if (!store.isActive(tab)) throw new Error(`Could not switch to the tab ${tab.path}`)
}

export async function readAgentTab(app: ComfyApp, job: TabReadJob): Promise<TabReadResult> {
  try {
    const tabs = listTabs(app)
    const saved = (await savedWorkflows(tabsOf(app))).map((workflow) => ({ key: workflow.path, name: workflow.filename }))
    if (!job.tab) return { status: 'read', tabs, saved }
    // A saved workflow is read from its file; only applying opens it.
    const tab = await findWorkflow(app, job.tab)
    const workflow = await tabWorkflow(app, tab)
    return { status: 'read', tabs, saved, tab: {
      key: tab.path, name: tab.filename, open: tabsOf(app).isOpen(tab), workflow, plans: editorPlans(workflow) } }
  } catch (error) {
    return { status: 'failed', error: errorMessage(error) }
  }
}

function setWidget(node: { widgets?: { name: string; value?: unknown }[] }, name: string, value: unknown) {
  const widget = node.widgets?.find((item) => item.name === name)
  if (!widget) throw new Error(`The node has no ${name} widget`)
  widget.value = value
}

/** Set the Editor and Project the way a person would (one undo step), and optionally press Run. */
export async function applyAgentTab(app: ComfyApp, job: TabApplyJob): Promise<TabApplyResult> {
  const store = tabsOf(app)
  const previous = store.activeWorkflow
  let switched = false
  let opened = false
  try {
    if (nativeQueueBusy(app)) throw new Error('ComfyUI is already submitting a workflow; wait for it to finish')
    const tab = await findWorkflow(app, job.tab)
    if (!store.isOpen(tab)) {
      switched = opened = true
      await openSaved(app, tab)
    } else if (!store.isActive(tab)) {
      switched = true
      await activateTab(app, tab)
    }
    const graph = app.rootGraph
    if (job.changes) {
      const { editor_id: editorId, project_id: projectId, project = {}, track_data: trackData } = job.changes
      const editor = graph.getNodeById(editorId)
      const projectNode = projectId == null ? null : graph.getNodeById(projectId)
      if (editor?.type !== EDITOR_TYPE) throw new Error(`Node ${editorId} is not a MultiTrack Editor`)
      if (Object.keys(project).length && projectNode?.type !== PROJECT_TYPE) throw new Error(`Node ${projectId} is not a MultiTrack Project`)
      graph.beforeChange()
      try {
        setWidget(editor, 'track_data', JSON.stringify(trackData))
        for (const [name, value] of Object.entries(project)) setWidget(projectNode!, name, value)
      } finally {
        graph.afterChange()
      }
      // Let the Editor settle on the new data first, so the recorded edit is what it shows.
      await new Promise((resolve) => setTimeout(resolve, 50))
      // Record the edit like a person's, for undo and the unsaved marker.
      store.activeWorkflow?.changeTracker?.captureCanvasState?.()
      app.canvas.setDirty(true, true)
    }
    const result = job.queue ? await queueCurrentWorkflow(app, graph.serialize().id) : { status: 'applied' as const }
    return opened ? { ...result, opened } : result
  } catch (error) {
    return { status: 'failed', error: errorMessage(error), ...(opened ? { opened } : {}) }
  } finally {
    // Leave the person on the tab they were looking at.
    if (switched && previous && store.isOpen(previous) && !store.isActive(previous)) {
      await activateTab(app, previous).catch((error: unknown) => console.warn('[Easy Media] Could not return to the previous tab:', error))
    }
  }
}
