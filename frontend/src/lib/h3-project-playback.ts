import type { ProjectClip, ProjectVideoFile } from '@/types/project'

/** The following clip's drawn overlap, played over the end of a clip. */
export interface ProjectLeadIn {
  /** The lead-in video of the following clip's selected version. */
  filePath: string
  mediaRevision?: string
  /** Frame of the (trimmed) clip where the lead-in starts covering it. */
  startFrame: number
  /** Frame of the lead-in video shown at ``startFrame``. */
  sourceStartFrame: number
  /** The lead-in's own sound plays, unless the clip keeps a locked song or video sound. */
  audible: boolean
}

/** The version a clip plays, with its native provenance when it has one. */
export function selectedProjectFile(clip: ProjectClip): ProjectVideoFile | undefined {
  return clip.video_files?.find((file) => file.file_path === clip.file_path)
}

/**
 * Mirrors utils/h3_native_assembly.py (overlap_owner and native_lead_in_layers), so the
 * preview plays what the combined video does: an Anchor continuation that continued from
 * this exact version, with the seam untrimmed on both sides, covers the end of ``clip`` with
 * the overlap it drew. ``following`` is the next enabled clip, if any.
 */
export function projectLeadIn(clip: ProjectClip, following: ProjectClip | undefined): ProjectLeadIn | null {
  if (!following || following.source_start_frame !== 0) return null
  const parent = selectedProjectFile(clip)?.native_metadata
  const file = selectedProjectFile(following)
  const child = file?.native_metadata
  if (!parent || !child || !file?.lead_in_path) return null
  if (parent.source_kind !== 'native_sampler' || child.source_kind !== 'native_sampler'
      || child.continuity_mode !== 'context_anchor' || !child.context_frames
      || child.parent_artifact_id !== parent.artifact_id || parent.end_frame !== child.start_frame
      || parent.start_frame + clip.source_end_frame !== parent.end_frame) {
    return null
  }
  const duration = clip.source_end_frame - clip.source_start_frame
  const frames = Math.min(child.context_frames, duration)
  return {
    filePath: file.lead_in_path,
    mediaRevision: file.media_revision,
    startFrame: duration - frames,
    sourceStartFrame: child.context_frames - frames,
    audible: clip.audio_locked !== true,
  }
}
