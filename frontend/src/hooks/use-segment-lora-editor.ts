import { useCallback, useEffect, useState } from 'react'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import type { SnapshotNode } from '@/lib/h3-project-snapshot'
import type { SegmentLoraPlan, SegmentLoraRule } from '@/types/segment-loras'

export function useSegmentLoraEditor(value: SegmentLoraPlan, onChange: (value: SegmentLoraPlan) => void, node: SnapshotNode, app: ComfyApp) {
  const [files, setFiles] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [revision, refresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    void app.api.fetchApi('/models/loras', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const list: unknown = await response.json()
        if (!Array.isArray(list) || !list.every((item) => typeof item === 'string')) throw new Error('Invalid LoRA model list')
        if (!cancelled) { setFiles(list.map((name: string) => name.replaceAll('\\', '/'))); setError(null) }
      }).catch((cause: unknown) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { cancelled = true; controller.abort() }
  }, [app, revision])
  const commit = useCallback((next: SegmentLoraRule[]) => {
    node.graph?.beforeChange?.(node)
    onChange({ version: 1, rules: next })
    node.graph?.afterChange?.(node)
  }, [node, onChange])
  return { files, error, refresh: () => refresh((n) => n + 1),
    add: () => commit([...value.rules, { id: crypto.randomUUID(), enabled: true, lora: '', start_segment: 1, segment_count: -1, strength: 1, stage: 'all' }]),
    remove: (id: string) => commit(value.rules.filter((rule) => rule.id !== id)),
    update: (id: string, changes: Partial<SegmentLoraRule>) => commit(value.rules.map((rule) => rule.id === id ? { ...rule, ...changes } : rule)),
  }
}
