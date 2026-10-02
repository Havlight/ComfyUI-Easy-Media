import { useEffect, useRef, useState } from 'react'
import { H3NativeTimingError, reconcileH3NativeTimeline } from '@/lib/h3-native-timing'
import { normalizeTrackData } from '@/lib/multitrack-utils'
import type { TrackData } from '@/types/multitrack'

/** Validate the entire edit before undo history receives it. */
export function useH3NativeEditing(
  current: TrackData,
  format: string | undefined,
  commit: (next: TrackData) => void,
) {
  const [error, setError] = useState<{ code: string; message: string; segmentId?: string } | null>(null)
  const [migration, setMigration] = useState<TrackData | null>(null)
  const previousFormat = useRef(format)
  const isNewH3 = format === 'MiniMax' && !current.tracks.some((track) => track.type === 'task' && track.segments.length)

  function prepare(candidate: TrackData): TrackData {
    const next = normalizeTrackData(candidate)
    if (format === 'MiniMax' && current.h3_native) {
      for (const track of normalizeTrackData(current).tracks.filter((item) => item.locked)) {
        const updated = next.tracks.find((item) => item.id === track.id)
        if ((!updated || updated.locked) && JSON.stringify(track.segments) !== JSON.stringify(updated?.segments)) {
          throw new H3NativeTimingError('TRACK_LOCKED', 'Unlock the track before editing its contents.')
        }
      }
    }
    if (isNewH3 && !next.h3_native) next.h3_native = { version: 1, allow_vae_fallback: false }
    // Other model families retain their editor geometry. H3 policy stays saved for switching back.
    if (format !== 'MiniMax' && current.h3_native) next.h3_native = current.h3_native
    return format === 'MiniMax' ? reconcileH3NativeTimeline(next) : next
  }

  function report(error: unknown) {
    setError(error instanceof H3NativeTimingError ? { code: error.code, message: error.message, segmentId: error.segmentId }
      : { code: 'EDIT_FAILED', message: error instanceof Error ? error.message : String(error) })
  }

  function commitEdit(candidate: TrackData | (() => TrackData)) {
    try {
      const requested = typeof candidate === 'function' ? candidate() : candidate
      const next = prepare(requested)
      const requestedTasks = new Map(requested.tracks.flatMap((track) => track.type === 'task' ? track.segments.map((item) => [item.id, item] as const) : []))
      const adjusted = next.tracks.flatMap((track) => track.type === 'task' ? track.segments : [])
        .filter((item) => {
          const before = requestedTasks.get(item.id)
          return before && (before.start_frame !== item.start_frame || before.end_frame !== item.end_frame
            || before.content.continuity_mode !== item.content.continuity_mode)
        })
      setError(null)
      if (current.h3_native && adjusted.length > 1) setMigration(next)
      else commit(next)
    } catch (error: unknown) {
      report(error)
    }
  }

  function previewEdit(candidate: TrackData): TrackData | null {
    try {
      return prepare(candidate)
    } catch (error: unknown) {
      report(error)
      return null
    }
  }

  function previewMigration() {
    try {
      setMigration(reconcileH3NativeTimeline({ ...current, h3_native: { version: 1, allow_vae_fallback: false } }))
      setError(null)
    } catch (error: unknown) {
      report(error)
    }
  }

  useEffect(() => {
    const changed = previousFormat.current !== format
    previousFormat.current = format
    if (!changed || format !== 'MiniMax' || !current.h3_native) return
    try {
      const next = reconcileH3NativeTimeline(current)
      if (JSON.stringify(next.tracks) !== JSON.stringify(current.tracks)) setMigration(next)
    } catch (error: unknown) {
      report(error)
    }
  }, [format, current])

  return { commitEdit, previewEdit, error, dismissError: () => setError(null), isNewH3,
    migration, previewMigration, cancelMigration: () => setMigration(null),
    applyMigration: () => { if (migration) commit(migration); setMigration(null) } }
}
