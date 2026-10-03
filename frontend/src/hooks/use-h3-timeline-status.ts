import { useEffect, useState } from 'react'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import type { TrackData } from '@/types/multitrack'

interface StatusNode {
  id: string | number
  type?: string
  comfyClass?: string
  widgets?: { name: string; value?: unknown }[]
  outputs?: { links?: number[] | null }[]
  graph?: { links: Record<number, { target_id: string | number }>; getNodeById: (id: string | number) => StatusNode | null }
}
interface TimelineTaskStatus { segment_id: string; index: number; status: string }
interface TimelineStatus { project_name: string; tasks: TimelineTaskStatus[] }

export function linkedH3Projects(nodeInput: object | undefined): string[] {
  const start = nodeInput as StatusNode | undefined
  if (!start) return []
  const queue = [start]
  const seen = new Set<StatusNode>()
  const names = new Set<string>()
  while (queue.length) {
    const node = queue.shift()!
    if (seen.has(node)) continue
    seen.add(node)
    if ((node.comfyClass ?? node.type) === 'easy multitrackProject') {
      names.add(String(node.widgets?.find((widget) => widget.name === 'project_name')?.value || 'default'))
      continue
    }
    for (const output of node.outputs ?? []) for (const id of output.links ?? []) {
      const target = node.graph?.getNodeById(node.graph.links[id]?.target_id)
      if (target) queue.push(target)
    }
  }
  return [...names].sort()
}

/** Saved-result status is advisory; execution still performs full preflight. */
export function useH3TimelineStatus(app: ComfyApp | undefined, node: object | undefined, data: TrackData, enabled: boolean) {
  const [results, setResults] = useState<TimelineStatus[]>([])
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const serialized = JSON.stringify(data)
  const names = JSON.stringify(enabled ? linkedH3Projects(node) : [])
  useEffect(() => {
    if (!app?.api || !enabled) return
    const refresh = () => setRevision((value) => value + 1)
    app.api.addEventListener('execution_success', refresh)
    app.api.addCustomEventListener('easy_multitrack_project_refresh', refresh)
    return () => {
      app.api.removeEventListener('execution_success', refresh)
      app.api.removeCustomEventListener('easy_multitrack_project_refresh', refresh)
    }
  }, [app, enabled])
  useEffect(() => {
    const projects = JSON.parse(names) as string[]
    if (!app?.api || !projects.length) { setResults([]); setError(null); return }
    let cancelled = false
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void Promise.all(projects.map(async (project_name) => {
        const response = await app.api.fetchApi('/easy-media/project/timeline-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ project_name, tracks_info: JSON.parse(serialized) }),
        })
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
        return payload as TimelineStatus
      })).then((value) => { if (!cancelled) { setResults(value); setError(null) } })
        .catch((cause: unknown) => { if (!cancelled) { setResults([]); setError(cause instanceof Error ? cause.message : String(cause)) } })
    }, 400)
    return () => { cancelled = true; clearTimeout(timer); controller.abort() }
  }, [app, names, serialized, revision])
  const affected = [...new Set(results.flatMap((result) => result.tasks
    .filter((task) => ['edited', 'parent_changed', 'legacy', 'retired'].includes(task.status))
    .map((task) => task.segment_id.split(':marker:')[0])))]
  return { affected, error }
}
