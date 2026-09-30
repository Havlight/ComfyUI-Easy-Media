import type { ProjectClip } from '@/types/project'

export function isProjectBridgeReady(clip: ProjectClip, clips: ProjectClip[]): boolean {
  const enabled = clips.filter((item) => item.enabled !== false)
  const index = enabled.findIndex((item) => item.id === clip.id)
  const left = enabled[index - 1]
  const bridge = clip.bridge
  return !!bridge && !!left && left.index === clip.index - 1
    && left.file_name === bridge.left.video && left.media_revision === bridge.left.revision
    && clip.file_name === bridge.right.video && clip.media_revision === bridge.right.revision
    && left.source_end_frame === bridge.left_frame_count && clip.source_start_frame === 0
    && left.source_end_frame - left.source_start_frame >= bridge.before
    && clip.source_end_frame - clip.source_start_frame >= bridge.after
}

export interface ProjectBridgeOverlay {
  clipId: string
  file_path: string
  start: number
  end: number
}

export function projectBridgeOverlays(clips: ProjectClip[]): ProjectBridgeOverlay[] {
  const overlays: ProjectBridgeOverlay[] = []
  let cursor = 0
  for (const clip of clips) {
    if (clip.enabled === false) continue
    const duration = Math.max(1, clip.source_end_frame - clip.source_start_frame)
    if (clip.continuity_mode === 'restart_bridge' && isProjectBridgeReady(clip, clips)) {
      const bridge = clip.bridge!
      const start = cursor - bridge.before
      const previous = overlays.at(-1)
      if (!previous || start >= previous.end) {
        overlays.push({
          clipId: clip.id,
          file_path: clip.file_path.slice(0, clip.file_path.lastIndexOf('/') + 1) + bridge.file,
          start,
          end: cursor + bridge.after,
        })
      }
    }
    cursor += duration
  }
  return overlays
}
