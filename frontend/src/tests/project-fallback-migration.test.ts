import { describe, expect, it } from 'vitest'
import { installProjectFallbackMigration, migrateProjectFallbacks } from '@/lib/project-fallback-migration'

describe('Project fallback migration', () => {
  function setup(saved: unknown[], property = false) {
    const type = { prototype: {} }
    installProjectFallbackMigration(type, { name: 'easy multitrackProject' })
    const editor = { id: 1, widgets: [{ name: 'track_data', value: JSON.stringify({ h3_native: { version: 1, allow_vae_fallback: true } }) }] }
    const project = Object.assign(Object.create(type.prototype), { id: 2, type: 'easy multitrackProject', properties: {},
      widgets: [{ name: 'enabled_tiling', value: 'false' }, { name: 'allow_vae_fallback', value: false }],
      inputs: [{ name: 'tracks_info', link: 1 }] })
    const graph = { _nodes: [editor, project], links: { 1: { origin_id: 1 } }, getNodeById: () => editor }
    project.graph = graph
    project.onConfigure({ widgets_values: saved, properties: { easy_media_project_fallback_v1: property } })
    return { graph, project }
  }
  it('moves a legacy explicit true preference without changing the timing policy', () => {
    const { graph, project } = setup(['false'])
    migrateProjectFallbacks(graph)
    expect(project.widgets[1].value).toBe(true)
    expect(project.properties.easy_media_project_fallback_v1).toBe(true)
    project.widgets[1].value = false
    migrateProjectFallbacks(graph)
    expect(project.widgets[1].value).toBe(false)
  })
  it('preserves an explicit Project false over Editor true', () => {
    const { graph, project } = setup(['false', false])
    migrateProjectFallbacks(graph)
    expect(project.widgets[1].value).toBe(false)
  })
})
