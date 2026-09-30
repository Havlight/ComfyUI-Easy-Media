export type ProjectContinuityMode = 'shot' | 'context' | 'context_swap' | 'restart_bridge'

export interface ProjectBridge {
  left: { video: string; revision: string; segment_index: number }
  right: { video: string; revision: string; segment_index: number }
  left_frame_count: number
  right_frame_count: number
  before: number
  after: number
  file: string
}

export interface ProjectVideoFile {
  file_path: string
  file_name: string
  media_revision?: string
  source_frame_count: number
  continuity_mode?: ProjectContinuityMode
  bridge?: ProjectBridge | null
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
  bridge?: ProjectBridge | null
  enabled: boolean
  video_files?: ProjectVideoFile[]
}

export interface ProjectData {
  project_name: string
  width: number
  height: number
  frame_rate: number
  clips: ProjectClip[]
  auto_combine: boolean
  use_bridge: boolean
  updated_at?: number
}

export const DEFAULT_PROJECT_DATA: ProjectData = {
  project_name: 'default',
  width: 0,
  height: 0,
  frame_rate: 24,
  clips: [],
  auto_combine: true,
  use_bridge: true,
}
