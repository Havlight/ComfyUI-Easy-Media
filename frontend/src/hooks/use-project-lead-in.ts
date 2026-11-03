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

/**
 * Plays a following clip's drawn overlap over the end of the playing clip, as the combined
 * video does. The overlay video is shown and synced imperatively on every playback frame, so
 * it switches on the exact frame between React's throttled timeline updates. Its sound
 * replaces the clip's while it shows, unless the clip keeps a locked sound.
 */
export function useProjectLeadIn({ clipId, clipStart, leadIn, frameRate, muted, videoRefs }: Readonly<ProjectLeadInOptions>) {
  const overlayRef = useRef<HTMLVideoElement | null>(null)
  const current = useRef({ clipId, clipStart, leadIn, muted })
  current.current = { clipId, clipStart, leadIn, muted }

  const sync = useCallback((frame: number, playing: boolean) => {
    const overlay = overlayRef.current
    const state = current.current
    if (!overlay || !state.leadIn || state.clipId === null) return
    const local = frame - state.clipStart
    const showing = local >= state.leadIn.startFrame
    const audible = showing && state.leadIn.audible
    overlay.style.visibility = showing ? 'visible' : 'hidden'
    overlay.muted = !audible || state.muted
    const clipVideo = videoRefs.current?.get(state.clipId)
    if (clipVideo) clipVideo.muted = audible || state.muted
    if (!showing) {
      overlay.pause()
      return
    }
    const target = (state.leadIn.sourceStartFrame + local - state.leadIn.startFrame) / frameRate
    try {
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
