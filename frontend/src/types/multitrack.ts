export type MultiTrackType = 'task' | 'video' | 'audio' | 'subtitle'

export interface H3NativePolicy {
  version: 2
}

export type MultiTrackTaskMode = 'default' | 'l2v' | 'ref' | 'edit' | 'passthrough'

export type MultiTrackContinuityMode = 'shot' | 'context' | 'context_drift'

export type MultiTrackRefImageSize = 'match' | 'max'

export type MultiTrackUserPromptVariant = 'a' | 'b'

export type MultiTrackMediaType = 'image' | 'audio' | 'video' | 'subtitle' | 'none'

export type MultiTrackSourceType = 'preset' | 'input' | 'output' | 'local' | 'url' | 'slot' | 'previous_frame'

export interface MultiTrackPanoramaView {
  version: 1
  projection: 'equirectangular'
  yaw: number
  pitch: number
  hfov: number
  aspect_ratio: number
}

export interface MultiTrackTaskImage {
  id: string
  source_type?: MultiTrackSourceType
  file_path?: string
  local_path?: string
  url?: string
  slot_name?: string
  file_name?: string
  panorama_view?: MultiTrackPanoramaView
  shared_reference?: boolean
  shared_reference_copy?: boolean
  muted?: boolean
}

/** 'track' marks a placeholder that places or adjusts a timeline reference. */
export type MultiTrackTaskMediaSource = MultiTrackSourceType | 'track'

/** A task's own reference video or audio, or a placeholder for a timeline reference. */
export interface MultiTrackTaskMedia {
  id: string
  source_type?: MultiTrackTaskMediaSource
  file_path?: string
  local_path?: string
  url?: string
  slot_name?: string
  file_name?: string
  /** Placeholders: the timeline track, plus the shared clip when the placeholder stands for one. */
  track_id?: string
  segment_id?: string
  shared_reference?: boolean
  shared_reference_copy?: boolean
  muted?: boolean
  /** Audio: cut to the task (timeline default) instead of taking up to 15 s (upload default). */
  fit?: boolean
  /** Video: give the video an <Audio N> soundtrack. */
  soundtrack?: boolean
}

export interface MultiTrackSegmentContent {
  media_type: MultiTrackMediaType
  source_type?: MultiTrackSourceType
  file_path?: string
  local_path?: string
  url?: string
  slot_name?: string
  file_name?: string
  text?: string
  user_prompt?: string
  user_prompt_b?: string
  user_prompt_variant?: MultiTrackUserPromptVariant
  system_prompt?: string
  task_mode?: MultiTrackTaskMode
  continuity_mode?: MultiTrackContinuityMode
  /** Drift only: context steps beside the seam that taper to the floor weight, 0..12. Absent means 4; 0 tapers none. */
  drift_taper_steps?: number
  /** Drift only: weight of the step beside the seam, 0..1. Absent means 0, a clean seam. */
  drift_taper_floor?: number
  /** Drift only: keep the high-resolution stage on the plain schedule. Stored only when true. */
  drift_taper_low_only?: boolean
  ref_image_size?: MultiTrackRefImageSize
  images?: MultiTrackTaskImage[]
  /** Ref and edit tasks: reference videos and audios beside the timeline's. */
  videos?: MultiTrackTaskMedia[]
  audios?: MultiTrackTaskMedia[]
  duration?: number
  volume_db?: number
  muted?: boolean
  speed?: number
  media_index?: number
  shared_reference?: boolean
  shared_media_index?: number
  subtitle_style?: MultiTrackSubtitleStyle
  subtitle_speech?: MultiTrackSubtitleSpeechSettings
}

export interface MultiTrackSubtitleSpeechSettings {
  model: 'VoxCPM2'
  prompt: string
  cfg: number
  steps: number
  referenceAudio: string
  referenceAudioSourceType?: Extract<MultiTrackSourceType, 'input' | 'output' | 'local'>
}

export interface MultiTrackSubtitleStyle {
  font_size: number
  color: string
  outline_color?: string
  background_color: string
  background_opacity: number
  x: number
  y: number
  width: number
}

export interface MultiTrackSegment {
  id: string
  start_frame: number
  end_frame: number
  origin_start_frame?: number
  content: MultiTrackSegmentContent
  color: string
}

export interface MultiTrackTaskMarker {
  id: string
  frame: number
}

export interface MultiTrack {
  id: string
  name: string
  type: MultiTrackType
  task_mode?: MultiTrackTaskMode
  color: string
  muted: boolean
  solo?: boolean
  visible?: boolean
  volume_db?: number
  locked: boolean
  audio_locked?: boolean
  media_index?: number
  segments: MultiTrackSegment[]
}

export interface TrackData {
  /** Missing means legacy timing; fallback never disables native geometry. */
  h3_native?: H3NativePolicy
  tracks: MultiTrack[]
  total_length: number
  frame_rate: number
  task_markers?: MultiTrackTaskMarker[]
  task_overview?: boolean
  muted?: boolean
  volume_db?: number
}

export interface TracksInfoMediaItem {
  index: number
  track_id: string
  segment_id: string
  source_type?: MultiTrackSourceType
  file_path?: string
  local_path?: string
  url?: string
  slot_name?: string
  file_name?: string
  duration?: number
  panorama_view?: MultiTrackPanoramaView
}

export interface TracksInfo {
  h3_native?: H3NativePolicy
  total_length: number
  frame_rate: number
  task_markers?: MultiTrackTaskMarker[]
  muted?: boolean
  volume_db?: number
  width: number
  height: number
  tracks: MultiTrack[]
  media: {
    images: TracksInfoMediaItem[]
    audio: TracksInfoMediaItem[]
    video: TracksInfoMediaItem[]
  }
}
