import { useCallback, useRef, type RefObject } from 'react'
import type { ProjectLeadIn } from '@/lib/h3-project-playback'

interface ProjectLeadInOptions {
  /** The clip playing now and the following clip's overlap that covers its end, if any. */
  clipId: string | null
  clipStart: number
  leadIn: ProjectLeadIn | null
  frameRate: number
  muted: boolean
  /** The preview's video elements by clip id. */
  videoRefs: RefObject<Map<string, HTMLVideoElement>>
}

/** A video element has a frame to show. */
const HAVE_CURRENT_DATA = 2

/**
 * Plays a following clip's drawn overlap over the end of the playing clip, as the combined
 * video does. The overlay video is shown and synced imperatively on every playback frame, so
 * it switches on the exact frame between React's throttled timeline updates. Its sound
 * replaces the clip's while it shows, unless the clip keeps a locked sound; the clip gets its
 * sound back whenever the overlay hides or goes away.
 */
export function useProjectLeadIn({ clipId, clipStart, leadIn, frameRate, muted, videoRefs }: Readonly<ProjectLeadInOptions>) {
  const overlayRef = useRef<HTMLVideoElement | null>(null)
  const current = useRef({ clipId, clipStart, leadIn, muted })
  current.current = { clipId, clipStart, leadIn, muted }

  const sync = useCallback((frame: number, playing: boolean) => {
    const overlay = overlayRef.current
    const state = current.current
    const clipVideo = state.clipId === null ? undefined : videoRefs.current?.get(state.clipId)
    if (!overlay || !state.leadIn) {
      if (clipVideo) clipVideo.muted = state.muted
      return
    }
    const { leadIn } = state
    const local = frame - state.clipStart
    const showing = local >= leadIn.startFrame
    // A frame without picture data would cover the clip with nothing: keep the clip until it loads.
    const shown = showing && overlay.readyState >= HAVE_CURRENT_DATA
    const audible = shown && leadIn.audible
    overlay.style.visibility = shown ? 'visible' : 'hidden'
    overlay.muted = !audible || state.muted
    if (clipVideo) clipVideo.muted = audible || state.muted
    const first = leadIn.sourceStartFrame / frameRate
    try {
      if (!showing) {
        // Rewound while hidden, so it shows its own first frame when the overlap starts.
        overlay.pause()
        if (Math.abs(overlay.currentTime - first) > 0.001) overlay.currentTime = first
        return
      }
      if (local >= leadIn.endFrame) {
        // Past the clip's end before React switches clips: hold the last frame, never restart.
        overlay.pause()
        return
      }
      const target = first + (local - leadIn.startFrame) / frameRate
      if (!playing || overlay.paused || Math.abs(overlay.currentTime - target) > 0.1) overlay.currentTime = target
    } catch (error) {
      console.error('[ProjectVideoCombineWidget] failed to seek the lead-in video:', error)
    }
    if (!playing) {
      overlay.pause()
    } else if (overlay.paused) {
      void overlay.play().catch((error: unknown) => {
        console.error('[ProjectVideoCombineWidget] failed to play the lead-in video:', error)
      })
    }
  }, [frameRate, videoRefs])

  return { overlayRef, sync }
}
