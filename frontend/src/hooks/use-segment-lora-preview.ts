import { useEffect, useState } from 'react'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import type { ConnectedH3Project } from '@/lib/h3-project-snapshot'
import type { LoraSummary } from '@/types/segment-loras'

export function useSegmentLoraPreview(app: ComfyApp, project: ConnectedH3Project | undefined, expanded: boolean) {
  const [summary, setSummary] = useState<LoraSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resolved, setResolved] = useState<LoraSummary | null>(null)
  const serialized = JSON.stringify(project)
  useEffect(() => { setResolved(null) }, [serialized])
  useEffect(() => {
    const receive = (event: CustomEvent<unknown>) => {
      const detail = event.detail as Partial<LoraSummary> & { project_node_id?: string } | null
      if (detail?.project_node_id === project?.id && Array.isArray(detail?.tasks)
          && Array.isArray(detail.rules) && Array.isArray(detail.conflicts)) setResolved(detail as LoraSummary)
    }
    app.api.addCustomEventListener('easy_media_segment_loras_resolved', receive)
    return () => app.api.removeCustomEventListener('easy_media_segment_loras_resolved', receive)
  }, [app, project?.id])
  useEffect(() => {
    setSummary(null); setError(null)
    if (!expanded || !project) return
    const controller = new AbortController()
    let cancelled = false
    const timer = setTimeout(() => {
      void app.api.fetchApi('/easy-media/project/segment-loras-preview', {
        method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: serialized,
      }).then(async (response) => {
        const value = await response.json()
        if (!response.ok) throw new Error(value.error ?? `HTTP ${response.status}`)
        if (!cancelled) setSummary(value as LoraSummary)
      }).catch((cause: unknown) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)) })
    }, 350)
    return () => { cancelled = true; clearTimeout(timer); controller.abort() }
  }, [app, serialized, expanded])
  return { summary: resolved ?? summary, resolved: resolved !== null, error }
}
