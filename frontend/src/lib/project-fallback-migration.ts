/** One-time migration of the old Editor execution preference into Project. */
interface PolicyWidget { name: string; value?: unknown; options?: { serialize?: boolean } }
interface PolicyNode {
  id: string | number
  type?: string
  comfyClass?: string
  properties?: Record<string, unknown>
  widgets?: PolicyWidget[]
  inputs?: { name?: string; link?: number | null }[]
  graph?: PolicyGraph | null
  onConfigure?: (info: { widgets_values?: unknown[]; properties?: Record<string, unknown> }) => void
  easyMediaFallbackMissing?: boolean
}
interface PolicyGraph {
  _nodes?: PolicyNode[]
  links?: Record<number, { origin_id: string | number }>
  getNodeById?: (id: string | number) => PolicyNode | null
}
const PROPERTY = 'easy_media_project_fallback_v1'
const PROJECT = 'easy multitrackProject'

export function installProjectFallbackMigration(nodeType: { prototype: object }, nodeData: { name: string }): void {
  if (nodeData.name !== PROJECT) return
  const prototype = nodeType.prototype as PolicyNode
  const original = prototype.onConfigure
  prototype.onConfigure = function (info) {
    original?.call(this, info)
    const index = this.widgets?.findIndex((widget) => widget.name === 'allow_vae_fallback') ?? -1
    this.easyMediaFallbackMissing = !info.properties?.[PROPERTY]
      && (index < 0 || typeof info.widgets_values?.[index] !== 'boolean')
  }
}

function sourcePreference(node: PolicyNode, seen = new Set<PolicyNode>()): boolean | undefined {
  if (seen.has(node)) return undefined
  seen.add(node)
  for (const widget of node.widgets ?? []) {
    if (widget.name !== 'track_data') continue
    let value: unknown = widget.value
    if (typeof value === 'string') {
      try { value = JSON.parse(value) }
      catch (error) { console.error('[Easy Media] Cannot migrate invalid Editor data:', error); return undefined }
    }
    if (value && typeof value === 'object' && 'h3_native' in value) {
      const policy = value.h3_native
      if (policy && typeof policy === 'object' && 'allow_vae_fallback' in policy
          && typeof policy.allow_vae_fallback === 'boolean') return policy.allow_vae_fallback
    }
  }
  // Follow TRACKS_INFO adapters and reroutes without depending on node IDs.
  const input = node.inputs?.find((item) => item.name === 'tracks_info')
    ?? (node.type === 'Reroute' ? node.inputs?.[0] : undefined)
  if (input?.link != null) {
    const link = node.graph?.links?.[input.link]
    const source = link && node.graph?.getNodeById?.(link.origin_id)
    if (source) return sourcePreference(source, seen)
  }
  return undefined
}

export function migrateProjectFallbacks(graphInput: object): void {
  const graph = graphInput as PolicyGraph
  for (const node of graph._nodes ?? []) {
    if ((node.comfyClass ?? node.type) !== PROJECT) continue
    const widget = node.widgets?.find((item) => item.name === 'allow_vae_fallback')
    if (!widget) continue
    if (node.easyMediaFallbackMissing) {
      const preference = sourcePreference(node)
      if (preference !== undefined) widget.value = preference
      node.easyMediaFallbackMissing = false
    }
    node.properties ??= {}
    node.properties[PROPERTY] = true
  }
}
