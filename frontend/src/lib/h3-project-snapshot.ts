import type { TrackData } from '@/types/multitrack'
import { EMPTY_LORA_PLAN, type LoraProjectSnapshot, type SegmentLoraPlan } from '@/types/segment-loras'

interface Link { origin_id?: string | number; target_id?: string | number }
export interface SnapshotNode {
  id: string | number
  type?: string
  comfyClass?: string
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
  const rawPlan = lora && kind(lora) === 'easy h3SegmentLoras' ? decode(widget(lora, 'rules')) : EMPTY_LORA_PLAN
  if (lora === undefined || (lora && (kind(lora) !== 'easy h3SegmentLoras' || inputSource(lora, 'rules') !== null))) known = false
  if (!object(rawPlan) || rawPlan.version !== 1 || !Array.isArray(rawPlan.rules)) known = false
  for (const name of ['project_name', 'sampling_mode', '1st_pass_only', 'segment_start_number', 'segment_count']) {
    if (inputSource(project, name) !== null) known = false
  }
  const mode = widget(project, 'sampling_mode', 'single')
  const modeValue = object(mode) ? mode.sampling_mode : mode
  const recipe = { sampling_mode: typeof modeValue === 'string' ? modeValue : 'single',
    first_pass_only: Boolean(object(mode) ? mode['1st_pass_only'] ?? widget(project, '1st_pass_only', false) : widget(project, '1st_pass_only', false)) }
  if (!['single', 'dual', 'selflift', 'passthrough'].includes(recipe.sampling_mode)) known = false
  if (recipe.sampling_mode === 'selflift') recipe.first_pass_only = false
  if (!object(tracks) || !Array.isArray(tracks.tracks)) known = false
  return { id: String(project.id), name: String(widget(project, 'project_name', 'default')),
    tracks_info: object(tracks) ? tracks as unknown as TrackData : undefined,
    project_snapshot: { known, segment_loras: rawPlan as SegmentLoraPlan, recipe,
      start_segment: Number(widget(project, 'segment_start_number', 1)), segment_count: Number(widget(project, 'segment_count', -1)) } }
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
