import { useEffect, useRef, useState } from 'react'
import { H3NativeTimingError, reconcileH3NativeTimeline } from '@/lib/h3-native-timing'
import { normalizeTrackData } from '@/lib/multitrack-utils'
import type { TrackData } from '@/types/multitrack'

/** One accepted edit, including native ripple adjustments, is one history entry. */
export function useH3NativeEditing(current: TrackData, format: string | undefined, commit: (next: TrackData) => void) {
  const [error, setError] = useState<{ code: string; message: string; segmentId?: string } | null>(null)
  const [notice, setNotice] = useState<number | null>(null)
  // Do not immediately redo an automatic conversion when the user undoes it.
  const converted = useRef(new Set<string>())
  const currentRef = useRef(current)
  currentRef.current = current
  const commitRef = useRef(commit)
  commitRef.current = commit

  function prepare(candidate: TrackData): TrackData {
    const next = normalizeTrackData(candidate)
    if (format !== 'MiniMax') return { ...next, h3_native: current.h3_native }
    for (const track of normalizeTrackData(current).tracks.filter((item) => item.locked)) {
      const updated = next.tracks.find((item) => item.id === track.id)
      if ((!updated || updated.locked) && JSON.stringify(track.segments) !== JSON.stringify(updated?.segments)) {
        throw new H3NativeTimingError('TRACK_LOCKED', 'Unlock the track before editing its contents.')
      }
    }
    return reconcileH3NativeTimeline({ ...next, h3_native: { ...next.h3_native, version: 2 } })
  }

  function report(cause: unknown) {
    setError(cause instanceof H3NativeTimingError ? { code: cause.code, message: cause.message, segmentId: cause.segmentId }
      : { code: 'EDIT_FAILED', message: cause instanceof Error ? cause.message : String(cause) })
  }

  function apply(next: TrackData, requested: TrackData) {
    const before = new Map(requested.tracks.flatMap((track) => track.type === 'task' ? track.segments.map((item) => [item.id, item] as const) : []))
    const count = next.tracks.flatMap((track) => track.type === 'task' ? track.segments : []).filter((item) => {
      const old = before.get(item.id)
      return old && (old.start_frame !== item.start_frame || old.end_frame !== item.end_frame || old.content.continuity_mode !== item.content.continuity_mode)
    }).length
    setError(null)
    setNotice(count || null)
    commitRef.current(next)
  }

  function commitEdit(candidate: TrackData | (() => TrackData)) {
    try {
      const requested = typeof candidate === 'function' ? candidate() : candidate
      apply(prepare(requested), requested)
    } catch (cause: unknown) { report(cause) }
  }

  function previewEdit(candidate: TrackData): TrackData | null {
    try { return prepare(candidate) }
    catch (cause: unknown) { report(cause); return null }
  }

  const serialized = JSON.stringify(current)
  useEffect(() => {
    if (format !== 'MiniMax') return
    const key = format + serialized
    if (converted.current.has(key)) return
    try {
      const before = currentRef.current
      const next = prepare(before)
      if (JSON.stringify(next) !== serialized) {
        converted.current.add(key)
        if (converted.current.size > 256) converted.current.delete(converted.current.values().next().value!)
        apply(next, before)
      }
    } catch (cause: unknown) { report(cause) }
  }, [format, serialized])

  useEffect(() => {
    if (notice === null) return
    const timer = setTimeout(() => setNotice(null), 7000)
    return () => clearTimeout(timer)
  }, [notice])

  return { commitEdit, previewEdit, error, dismissError: () => setError(null), notice, dismissNotice: () => setNotice(null) }
}
