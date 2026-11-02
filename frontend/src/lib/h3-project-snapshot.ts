import type { H3LoraNodeError, H3PlanLoraTarget } from '@/lib/h3-plan-loras'
import type { TrackData } from '@/types/multitrack'
import { EMPTY_LORA_PLAN, type LoraProjectSnapshot, type SegmentLoraPlan } from '@/types/segment-loras'

interface Link { origin_id?: string | number; target_id?: string | number }
export interface SnapshotNode {
  id: string | number
  type?: string
  comfyClass?: string
  mode?: number
  widgets?: { name: string; value?: unknown }[]
  inputs?: { name?: string; link?: number | null }[]
  outputs?: { links?: number[] | null }[]
  graph?: {
    links: Record<number, Link> | Map<number, Link>
    getNodeById: (id: string | number) => SnapshotNode | null
    beforeChange?: (node?: unknown) => void
    afterChange?: (node?: unknown) => void
  }
}
export interface ConnectedH3Project {
  id: string
  name: string
  tracks_info?: TrackData
  project_snapshot: LoraProjectSnapshot
}
function kind(node: SnapshotNode) { return node.comfyClass ?? node.type }
/** ComfyUI leaves a muted (2) or bypassed (4) node out of the run, so a LoRA node in either mode applies no rules. */
export function h3NodeInactive(node: SnapshotNode) { return node.mode === 2 || node.mode === 4 }
function linkAt(node: SnapshotNode, id: number): Link | undefined {
  const links = node.graph?.links
  return links instanceof Map ? links.get(id) : links?.[id]
}
function widget(node: SnapshotNode, name: string, fallback?: unknown): unknown {
  return node.widgets?.find((item) => item.name === name)?.value ?? fallback
}
function inputSource(node: SnapshotNode, name: string): SnapshotNode | null | undefined {
  const id = node.inputs?.find((input) => input.name === name)?.link
  if (id == null) return null
  const origin = linkAt(node, id)?.origin_id
  return origin == null ? undefined : node.graph?.getNodeById(origin) ?? undefined
}
function unwrapReroute(node: SnapshotNode | null | undefined): SnapshotNode | null | undefined {
  const seen = new Set<SnapshotNode>()
  while (node && kind(node) === 'Reroute') {
    if (seen.has(node)) return undefined
    seen.add(node)
    const id = node.inputs?.[0]?.link
    const origin: string | number | undefined = id == null ? undefined : linkAt(node, id)?.origin_id
    node = origin == null ? undefined : node.graph?.getNodeById(origin) ?? undefined
  }
  return node
}
function decode(value: unknown): unknown {
  if (typeof value !== 'string') return value
  try { return JSON.parse(value) }
  catch (error) { console.warn('[Easy Media] Invalid graph snapshot:', error); return undefined }
}
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }

/** Resolve only recognized, static inputs. Unknown upstream adapters stay pending. */
export function snapshotH3Project(project: SnapshotNode, editorOverride?: { node: SnapshotNode; data: TrackData }): ConnectedH3Project {
  const editor = unwrapReroute(inputSource(project, 'tracks_info'))
  const lora = unwrapReroute(inputSource(project, 'segment_loras'))
  let known = !!editor && kind(editor) === 'easy multiTrackEditor'
  const tracks = editor && editorOverride?.node.id === editor.id ? editorOverride.data : decode(editor ? widget(editor, 'track_data') : undefined)
  if (editor && (inputSource(editor, 'prompt_override') !== null || inputSource(editor, 'track_data') !== null
      || inputSource(editor, 'format') !== null || widget(editor, 'format', 'MiniMax') !== 'MiniMax')) known = false
  const rawPlan = lora && kind(lora) === 'easy h3SegmentLoras' && !h3NodeInactive(lora) ? decode(widget(lora, 'rules')) : EMPTY_LORA_PLAN
  if (lora === undefined || (lora && (kind(lora) !== 'easy h3SegmentLoras' || inputSource(lora, 'rules') !== null))) known = false
  if (!object(rawPlan) || rawPlan.version !== 1 || !Array.isArray(rawPlan.rules)) known = false
  for (const name of ['project_name', 'sampling_mode', 'segment_start_number', 'segment_count']) {
    if (inputSource(project, name) !== null) known = false
  }
  const mode = widget(project, 'sampling_mode', 'single')
  const modeValue = object(mode) ? mode.sampling_mode : mode
  const reuse = object(mode) ? mode.use_saved_1st_pass : widget(project, 'sampling_mode.use_saved_1st_pass')
  const recipe = { sampling_mode: typeof modeValue === 'string' ? modeValue : 'single',
    ...(modeValue === 'dual' && reuse === true ? { use_saved_1st_pass: true } : {}) }
  if (!['single', 'dual', 'selflift', 'passthrough'].includes(recipe.sampling_mode)) known = false
  if (!object(tracks) || !Array.isArray(tracks.tracks)) known = false
  return { id: String(project.id), name: String(widget(project, 'project_name', 'default')),
    tracks_info: object(tracks) ? tracks as unknown as TrackData : undefined,
    project_snapshot: { known, segment_loras: rawPlan as SegmentLoraPlan, recipe,
      start_segment: Number(widget(project, 'segment_start_number', 1)), segment_count: Number(widget(project, 'segment_count', -1)) } }
}

export type H3EditorLoraNode = { node: SnapshotNode; rules: SegmentLoraPlan; inactive?: true } | { error: H3LoraNodeError } | null

/**
 * The H3 Segment LoRA node an Editor's plan writes: the one feeding the Projects the Editor
 * feeds. Null when none is connected; an error when it is not one node whose rules the plan
 * alone decides (several nodes or some Projects without one, one shared with another Editor's
 * Project, rules from a link). `inactive` when the node is bypassed or muted: its rules can still
 * be written, but runs use none of them.
 */
export function h3EditorLoraNode(editor: SnapshotNode): H3EditorLoraNode {
  const projects = connectedH3ProjectNodes(editor)
  const sources = projects.map((project) => unwrapReroute(inputSource(project, 'segment_loras')))
  if (sources.some((source) => source === undefined || (source && kind(source) !== 'easy h3SegmentLoras'))) return { error: 'loras_linked' }
  const nodes = [...new Map(sources.filter((source): source is SnapshotNode => !!source).map((source) => [String(source.id), source])).values()]
  if (nodes.length === 0) return null
  // The plan's rules must reach every Project the Editor feeds.
  if (nodes.length > 1 || sources.includes(null)) return { error: 'loras_ambiguous' }
  const [node] = nodes
  if (inputSource(node, 'rules') !== null) return { error: 'loras_linked' }
  const fed = new Set(projects.map((project) => String(project.id)))
  if (connectedH3ProjectNodes(node).some((project) => !fed.has(String(project.id)))) return { error: 'loras_shared' }
  // A saved file without named widget values gives no rules to read, which is not the same as none.
  const value = widget(node, 'rules')
  const rules = value === undefined ? undefined : decode(value)
  if (!object(rules) || rules.version !== 1 || !Array.isArray(rules.rules)) return { error: 'loras_unreadable' }
  return { node, rules: rules as unknown as SegmentLoraPlan, ...(h3NodeInactive(node) ? { inactive: true as const } : {}) }
}

/** The LoRA node's rules widget, which a plan import or an agent apply writes. */
export function h3LoraRulesWidget(node: SnapshotNode): { name: string; value?: unknown } | undefined {
  return node.widgets?.find((item) => item.name === 'rules')
}

/** What a plan import writes for an Editor's LoRA node: its rules, why it cannot, or undefined when there is none. */
export function h3PlanLoraTarget(editor: SnapshotNode): H3PlanLoraTarget | undefined {
  const found = h3EditorLoraNode(editor)
  if (found === null) return undefined
  return 'error' in found ? { error: found.error } : { rules: found.rules, ...(found.inactive ? { inactive: true as const } : {}) }
}

/** A saved workflow's nodes in the shape the graph helpers read, for a tab that is not on the canvas. */
export function serializedGraphNodes(workflow: unknown): SnapshotNode[] {
  const data = (object(workflow) ? workflow : {}) as { nodes?: unknown; links?: unknown }
  const links = new Map<number, Link>()
  for (const link of Array.isArray(data.links) ? data.links : []) {
    // [id, origin, origin slot, target, target slot, type], or an object in newer files.
    if (Array.isArray(link)) links.set(Number(link[0]), { origin_id: link[1], target_id: link[3] })
    else if (object(link)) links.set(Number(link.id), { origin_id: link.origin_id as string | number, target_id: link.target_id as string | number })
  }
  const nodes = new Map<string, SnapshotNode>()
  const graph = { links, getNodeById: (id: string | number) => nodes.get(String(id)) ?? null }
  for (const raw of Array.isArray(data.nodes) ? data.nodes : []) {
    if (!object(raw)) continue
    const named = object(raw.widgets_values_named) ? raw.widgets_values_named : {}
    nodes.set(String(raw.id), {
      id: raw.id as string | number,
      type: raw.type as string | undefined,
      mode: typeof raw.mode === 'number' ? raw.mode : undefined,
      widgets: Object.entries(named).map(([name, value]) => ({ name, value })),
      inputs: raw.inputs as SnapshotNode['inputs'],
      outputs: raw.outputs as SnapshotNode['outputs'],
      graph,
    })
  }
  return [...nodes.values()]
}

export function connectedH3ProjectNodes(start: SnapshotNode | undefined): SnapshotNode[] {
  if (!start) return []
  const queue = [start], seen = new Set<SnapshotNode>(), projects: SnapshotNode[] = []
  while (queue.length) {
    const node = queue.shift()!
    if (seen.has(node)) continue
    seen.add(node)
    if (kind(node) === 'easy multitrackProject') { projects.push(node); continue }
    for (const output of node.outputs ?? []) for (const id of output.links ?? []) {
      const targetId = linkAt(node, id)?.target_id
      const target = targetId == null ? null : node.graph?.getNodeById(targetId)
      if (target) queue.push(target)
    }
  }
  return projects
}
