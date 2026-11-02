import { useEffect, useMemo, useRef } from 'react'
import {
  applyVideoAudioInfo,
  MAX_VIDEO_AUDIO_PROBES,
  probeVideoAudio,
  videosMissingAudioInfo,
} from '@/lib/video-audio-probe'
import type { TrackData } from '@/types/multitrack'

// Shared by every Editor: a file is probed once per page; unreadable files are not retried.
const knownAudio = new Map<string, boolean>()
const unreadable = new Set<string>()
const pending = new Set<string>()

/**
 * Record `has_audio` on videos whose audio is unknown. `amend` applies the result to the committed
 * data without an undo step, so undo never brings back a video the Editor would probe again.
 */
export function useVideoAudioProbe(
  data: TrackData,
  amend: (update: (current: TrackData) => TrackData | null) => void,
) {
  const mountedRef = useRef(true)
  const missingKey = useMemo(() => [...videosMissingAudioInfo(data).keys()].sort().join('\n'), [data])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!missingKey) return
    const missing = videosMissingAudioInfo(data)
    const cached = new Map([...missing.keys()].flatMap((key) => {
      const value = knownAudio.get(key)
      return value === undefined ? [] : [[key, value] as const]
    }))
    if (cached.size > 0) amend((current) => applyVideoAudioInfo(current, cached))

    const requests = [...missing.entries()]
      .filter(([key]) => !knownAudio.has(key) && !unreadable.has(key) && !pending.has(key))
      .slice(0, MAX_VIDEO_AUDIO_PROBES)
    if (requests.length === 0) return
    requests.forEach(([key]) => pending.add(key))
    probeVideoAudio(requests.map(([, source]) => source))
      .then((results) => {
        const found = new Map<string, boolean>()
        results.forEach((value, index) => {
          const key = requests[index][0]
          if (value === null) {
            unreadable.add(key)
            return
          }
          knownAudio.set(key, value)
          found.set(key, value)
        })
        if (mountedRef.current && found.size > 0) amend((current) => applyVideoAudioInfo(current, found))
      })
      .catch((error: unknown) => {
        console.warn('[MultiTrack] video audio probe failed:', error)
        requests.forEach(([key]) => unreadable.add(key))
      })
      .finally(() => requests.forEach(([key]) => pending.delete(key)))
    // missingKey stands for data here; other data changes need no new probe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingKey, amend])
}
