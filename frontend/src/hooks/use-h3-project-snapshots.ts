import { useEffect, useState } from 'react'
import { connectedH3ProjectNodes, snapshotH3Project, type ConnectedH3Project, type SnapshotNode } from '@/lib/h3-project-snapshot'
import type { TrackData } from '@/types/multitrack'

/** Graph controls and reroutes do not all emit the same event across Comfy versions. */
export function useH3ProjectSnapshots(node: object | undefined, enabled = true, editorData?: TrackData) {
  const serializedEditor = JSON.stringify(editorData)
  const read = () => JSON.stringify(enabled ? connectedH3ProjectNodes(node as SnapshotNode | undefined)
    .map((project) => snapshotH3Project(project, node && editorData ? { node: node as SnapshotNode, data: editorData } : undefined)) : [])
  const [snapshot, setSnapshot] = useState(read)
  useEffect(() => {
    const refresh = () => setSnapshot(read())
    refresh()
    // JSON equality makes unchanged polls a no-op; no requests or graph writes.
    const timer = setInterval(refresh, 500)
    return () => clearInterval(timer)
  }, [node, enabled, serializedEditor])
  return JSON.parse(snapshot) as ConnectedH3Project[]
}
