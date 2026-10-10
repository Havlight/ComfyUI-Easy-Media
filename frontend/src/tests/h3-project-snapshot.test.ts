import { describe, expect, it } from 'vitest'
import { connectedH3ProjectNodes, snapshotH3Project, type SnapshotNode } from '@/lib/h3-project-snapshot'

function setup() {
  const editor: SnapshotNode = { id: 1, type: 'easy multiTrackEditor', widgets: [
    { name: 'format', value: 'MiniMax' }, { name: 'track_data', value: JSON.stringify({ tracks: [] }) }], outputs: [{ links: [1] }] }
  const lora: SnapshotNode = { id: 2, type: 'easy h3SegmentLoras', widgets: [{ name: 'rules', value: JSON.stringify({ version: 1, rules: [] }) }], outputs: [{ links: [2] }] }
  const project: SnapshotNode = { id: 3, type: 'easy multitrackProject', widgets: [
    { name: 'project_name', value: 'test' }, { name: 'sampling_mode', value: 'dual' }],
    inputs: [{ name: 'tracks_info', link: 1 }, { name: 'segment_loras', link: 2 }] }
  const nodes = [editor, lora, project]
  const graph: NonNullable<SnapshotNode['graph']> = { links: { 1: { origin_id: 1, target_id: 3 }, 2: { origin_id: 2, target_id: 3 } },
    getNodeById: (id) => nodes.find((node) => node.id === id) ?? null }
  nodes.forEach((node) => { node.graph = graph })
  return { nodes, graph, editor, lora, project }
}

describe('shared H3 Project graph snapshots', () => {
  it('resolves static rules, selection and Editor data without evaluating the graph', () => {
    const { project } = setup()
    const snapshot = snapshotH3Project(project)
    expect(snapshot).toMatchObject({ id: '3', name: 'test', tracks_info: { tracks: [] },
      project_snapshot: { known: true, start_segment: 1, segment_count: -1,
        recipe: { sampling_mode: 'dual', first_pass_only: false }, segment_loras: { version: 1, rules: [] } } })
  })
  it('treats disconnected LoRA as known empty, unknown adapters as pending', () => {
    const { project, lora } = setup()
    lora.type = 'CustomDynamicPlan'
    expect(snapshotH3Project(project).project_snapshot.known).toBe(false)
    project.inputs![1].link = null
    expect(snapshotH3Project(project).project_snapshot.known).toBe(true)
    expect(snapshotH3Project(project).project_snapshot.segment_loras.rules).toEqual([])
  })
  it('marks connected prompt overrides and dynamic mode inputs pending', () => {
    const { project, editor } = setup()
    editor.inputs = [{ name: 'prompt_override', link: 77 }]
    expect(snapshotH3Project(project).project_snapshot.known).toBe(false)
    editor.inputs = []
    project.inputs!.push({ name: 'sampling_mode', link: 77 })
    expect(snapshotH3Project(project).project_snapshot.known).toBe(false)
  })
  it('follows reroutes and multiple consumers without conflating project names', () => {
    const { graph, nodes, lora, project } = setup()
    const reroute: SnapshotNode = { id: 4, type: 'Reroute', inputs: [{ link: 2 }], outputs: [{ links: [3, 4] }], graph }
    const other: SnapshotNode = { ...project, id: 5, widgets: [{ name: 'project_name', value: 'other' }], inputs: [{ name: 'tracks_info', link: 1 }, { name: 'segment_loras', link: 4 }] }
    nodes.push(reroute, other)
    graph.links = new Map([[1, { origin_id: 1, target_id: 3 }], [2, { origin_id: 2, target_id: 4 }],
      [3, { origin_id: 4, target_id: 3 }], [4, { origin_id: 4, target_id: 5 }]])
    project.inputs![1].link = 3
    expect(connectedH3ProjectNodes(lora).map((node) => node.id)).toEqual([3, 5])
    expect(snapshotH3Project(project).project_snapshot.known).toBe(true)
    expect(snapshotH3Project(other).name).toBe('other')
  })
  it('uses the live Editor value and ignores first-pass-only in SelfLift', () => {
    const { project, editor } = setup()
    project.widgets!.push({ name: '1st_pass_only', value: true })
    project.widgets![1].value = 'selflift'
    const data = { tracks: [], frame_rate: 24, total_length: 90 }
    const result = snapshotH3Project(project, { node: editor, data })
    expect(result.tracks_info).toBe(data)
    expect(result.project_snapshot.recipe.first_pass_only).toBe(false)
  })
})
