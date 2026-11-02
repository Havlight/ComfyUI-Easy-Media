import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import type { TrackData } from '@/types/multitrack'

const MULTITRACK_HISTORY_LIMIT = 50

/**
 * A change to another node made with a timeline edit, such as a plan's LoRA rules: applied once
 * the edit is recorded, undone and redone with it.
 */
export interface LinkedChange {
  apply: () => void
  undo: () => void
}

interface MultiTrackHistoryState {
  past: TrackData[]
  future: TrackData[]
  /** The linked change of each entry, index for index with past and future. */
  pastLinked: (LinkedChange | undefined)[]
  futureLinked: (LinkedChange | undefined)[]
}

const EMPTY_HISTORY: MultiTrackHistoryState = { past: [], future: [], pastLinked: [], futureLinked: [] }

function serializeTrackData(data: TrackData): string {
  return JSON.stringify(data)
}

export function useMultiTrackHistory(data: TrackData, onChange: (value: TrackData) => void) {
  const [history, setHistory] = useState<MultiTrackHistoryState>(EMPTY_HISTORY)
  const dataRef = useRef(data)
  const expectedSerializedRef = useRef(serializeTrackData(data))
  const lastPropSerializedRef = useRef(serializeTrackData(data))
  const pendingLocalSerializedRef = useRef(new Set<string>())
  const staleLocalSerializedRef = useRef(new Set<string>())

  const serialized = serializeTrackData(data)

  useLayoutEffect(() => {
    const pendingLocalSerialized = pendingLocalSerializedRef.current
    if (pendingLocalSerialized.size > 0) {
      if (pendingLocalSerialized.has(serialized)) {
        pendingLocalSerialized.delete(serialized)
        if (pendingLocalSerialized.size === 0) staleLocalSerializedRef.current.clear()
        lastPropSerializedRef.current = serialized
        expectedSerializedRef.current = serialized
        dataRef.current = data
        return
      }
      if (serialized === lastPropSerializedRef.current || staleLocalSerializedRef.current.has(serialized)) return
      pendingLocalSerialized.clear()
      staleLocalSerializedRef.current.clear()
      expectedSerializedRef.current = serialized
      lastPropSerializedRef.current = serialized
      dataRef.current = data
      setHistory(EMPTY_HISTORY)
      return
    }

    dataRef.current = data
    if (serialized !== expectedSerializedRef.current) {
      expectedSerializedRef.current = serialized
      lastPropSerializedRef.current = serialized
      setHistory(EMPTY_HISTORY)
      return
    }
    lastPropSerializedRef.current = serialized
  }, [data, serialized])

  /** Show `nextData` as this widget's own change; nothing to do when the timeline is already that. */
  const moveTo = useCallback((currentData: TrackData, nextData: TrackData) => {
    const currentSerialized = serializeTrackData(currentData)
    const nextSerialized = serializeTrackData(nextData)
    if (nextSerialized === currentSerialized) return

    expectedSerializedRef.current = nextSerialized
    pendingLocalSerializedRef.current.add(nextSerialized)
    staleLocalSerializedRef.current.add(currentSerialized)
    dataRef.current = nextData
    onChange(nextData)
  }, [onChange])

  const commitChange = useCallback((nextData: TrackData, linked?: LinkedChange) => {
    const currentData = dataRef.current
    // An edit that only changes another node still gets its undo step.
    if (serializeTrackData(nextData) === serializeTrackData(currentData) && !linked) return

    setHistory((current) => ({
      past: [...current.past, currentData].slice(-MULTITRACK_HISTORY_LIMIT),
      future: [],
      pastLinked: [...current.pastLinked, linked].slice(-MULTITRACK_HISTORY_LIMIT),
      futureLinked: [],
    }))
    moveTo(currentData, nextData)
    linked?.apply()
  }, [moveTo])

  /** Apply metadata that is not an edit, such as probed media facts: no undo step, history kept. */
  const amendChange = useCallback((update: (current: TrackData) => TrackData | null) => {
    const nextData = update(dataRef.current)
    if (nextData) moveTo(dataRef.current, nextData)
  }, [moveTo])

  const undo = useCallback(() => {
    setHistory((current) => {
      const previous = current.past.at(-1)
      if (!previous) return current
      const linked = current.pastLinked.at(-1)

      const currentData = dataRef.current
      moveTo(currentData, previous)
      linked?.undo()

      return {
        past: current.past.slice(0, -1),
        future: [currentData, ...current.future].slice(0, MULTITRACK_HISTORY_LIMIT),
        pastLinked: current.pastLinked.slice(0, -1),
        futureLinked: [linked, ...current.futureLinked].slice(0, MULTITRACK_HISTORY_LIMIT),
      }
    })
  }, [moveTo])

  const redo = useCallback(() => {
    setHistory((current) => {
      const next = current.future[0]
      if (!next) return current
      const linked = current.futureLinked[0]

      const currentData = dataRef.current
      moveTo(currentData, next)
      linked?.apply()

      return {
        past: [...current.past, currentData].slice(-MULTITRACK_HISTORY_LIMIT),
        future: current.future.slice(1),
        pastLinked: [...current.pastLinked, linked].slice(-MULTITRACK_HISTORY_LIMIT),
        futureLinked: current.futureLinked.slice(1),
      }
    })
  }, [moveTo])

  return {
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    commitChange,
    amendChange,
    undo,
    redo,
  }
}
