import { useEffect, useState } from 'react'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import type { TrackData } from '@/types/multitrack'
import { connectedH3ProjectNodes, type ConnectedH3Project, type SnapshotNode } from '@/lib/h3-project-snapshot'
import { useH3ProjectSnapshots } from '@/hooks/use-h3-project-snapshots'

export interface TimelineTaskStatus { segment_id: string; index: number; status: string }
export interface TimelineStatus { project_name: string; tasks: TimelineTaskStatus[] }

/** Statuses that mean a saved result no longer matches its task. */
export const H3_STALE_STATUSES = new Set(['edited', 'parent_changed', 'legacy'])

export function linkedH3Projects(nodeInput: object | undefined): string[] {
  return [...new Set(connectedH3ProjectNodes(nodeInput as SnapshotNode | undefined)
    .map((node) => String(node.widgets?.find((widget) => widget.name === 'project_name')?.value || 'default')))].sort()
}

/** Ask each linked Project how its saved results compare with `data`. */
export function fetchH3TimelineStatuses(
  app: ComfyApp,
  projects: ConnectedH3Project[],
  data: TrackData,
  signal?: AbortSignal,
): Promise<TimelineStatus[]> {
  return Promise.all(projects.map(async (project) => {
    const response = await app.api.fetchApi('/easy-media/project/timeline-status', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ project_name: project.name, tracks_info: data, project_snapshot: project.project_snapshot }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
    return payload as TimelineStatus
  }))
}

/** Task ids whose status matches, across every linked Project; marker splits map to their task. */
export function h3TasksWithStatus(results: TimelineStatus[], statuses: ReadonlySet<string>): string[] {
  return [...new Set(results.flatMap((result) => result.tasks
    .filter((task) => statuses.has(task.status))
    .map((task) => task.segment_id.split(':marker:')[0])))]
}

/** Saved-result status is advisory; execution still performs full preflight. */
export function useH3TimelineStatus(app: ComfyApp | undefined, node: object | undefined, data: TrackData, enabled: boolean) {
  const [results, setResults] = useState<TimelineStatus[]>([])
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const serialized = JSON.stringify(data)
  const snapshots = useH3ProjectSnapshots(node, enabled, data)
  const serializedProjects = JSON.stringify(snapshots)
  useEffect(() => {
    if (!app?.api || !enabled) return
    const refresh = () => setRevision((value) => value + 1)
    app.api.addEventListener('execution_success', refresh)
    app.api.addCustomEventListener('easy_multitrack_project_refresh', refresh)
    app.api.addCustomEventListener('easy_media_segment_loras_resolved', refresh)
    return () => {
      app.api.removeEventListener('execution_success', refresh)
      app.api.removeCustomEventListener('easy_multitrack_project_refresh', refresh)
      app.api.removeCustomEventListener('easy_media_segment_loras_resolved', refresh)
    }
  }, [app, enabled])
  useEffect(() => {
    const projects = JSON.parse(serializedProjects) as typeof snapshots
    if (!app?.api || !projects.length) { setResults([]); setError(null); return }
    let cancelled = false
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void fetchH3TimelineStatuses(app, projects, JSON.parse(serialized) as TrackData, controller.signal)
        .then((value) => { if (!cancelled) { setResults(value); setError(null) } })
        .catch((cause: unknown) => { if (!cancelled) { setResults([]); setError(cause instanceof Error ? cause.message : String(cause)) } })
    }, 400)
    return () => { cancelled = true; clearTimeout(timer); controller.abort() }
  }, [app, serializedProjects, serialized, revision])
  const affected = h3TasksWithStatus(results, H3_STALE_STATUSES)
  return { affected, error, results, projects: snapshots }
}
