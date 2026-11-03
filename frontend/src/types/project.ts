export type ProjectContinuityMode = 'shot' | 'context' | 'context_drift' | 'context_anchor'

/** Provenance of a native H3 version: which version a continuation continued from. */
export interface ProjectNativeMetadata {
  artifact_id?: string
  parent_artifact_id?: string | null
  source_kind?: string
  start_frame: number
  end_frame: number
  context_frames: number
  continuity_mode?: ProjectContinuityMode
}

export interface ProjectVideoFile {
  file_path: string
  file_name: string
  media_revision?: string
  source_frame_count: number
  native_stale?: boolean
  fallback_history?: Array<{ reason?: string }>
  continuity_mode?: ProjectContinuityMode
  native_metadata?: ProjectNativeMetadata
  /** The overlap an Anchor continuation drew, played over its predecessor's end. */
  lead_in_path?: string
}

export interface ProjectClip {
  id: string
  index: number
  file_path: string
  file_name: string
  media_revision?: string
  source_start_frame: number
  source_end_frame: number
  source_frame_count: number
  continuity_mode: ProjectContinuityMode
  enabled: boolean
  /** The task plays a locked song or video sound instead of its generated audio. */
  audio_locked?: boolean
  video_files?: ProjectVideoFile[]
}

export interface ProjectData {
  project_name: string
  width: number
  height: number
  frame_rate: number
  clips: ProjectClip[]
  auto_combine: boolean
  updated_at?: number
}

export const DEFAULT_PROJECT_DATA: ProjectData = {
  project_name: 'default',
  width: 0,
  height: 0,
  frame_rate: 24,
  clips: [],
  auto_combine: true,
}
