import { useState } from 'react'
import { H3NativeTimingError, reconcileH3NativeTimeline } from '@/lib/h3-native-timing'
import { normalizeTrackData } from '@/lib/multitrack-utils'
import type { TrackData } from '@/types/multitrack'

/** Validate the entire edit before undo history receives it. */
export function useH3NativeEditing(
  current: TrackData,
  format: string | undefined,
  commit: (next: TrackData) => void,
) {
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [migration, setMigration] = useState<TrackData | null>(null)
  const isNewH3 = format === 'MiniMax' && !current.tracks.some((track) => track.type === 'task' && track.segments.length)

  function prepare(candidate: TrackData): TrackData {
    const next = normalizeTrackData(candidate)
    if (isNewH3 && !next.h3_native) next.h3_native = { version: 1, allow_vae_fallback: false }
    // Other model families retain their editor geometry. H3 policy stays saved for switching back.
    return format === 'MiniMax' ? reconcileH3NativeTimeline(next) : next
  }

  function report(error: unknown) {
    setError(error instanceof H3NativeTimingError ? { code: error.code, message: error.message }
      : { code: 'EDIT_FAILED', message: error instanceof Error ? error.message : String(error) })
  }

  function commitEdit(candidate: TrackData | (() => TrackData)) {
    try {
      const next = prepare(typeof candidate === 'function' ? candidate() : candidate)
      setError(null)
      commit(next)
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

  return { commitEdit, previewEdit, error, dismissError: () => setError(null), isNewH3,
    migration, previewMigration, cancelMigration: () => setMigration(null),
    applyMigration: () => { if (migration) commitEdit(migration); setMigration(null) } }
}
