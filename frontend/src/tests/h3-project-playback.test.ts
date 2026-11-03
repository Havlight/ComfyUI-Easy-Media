import { describe, expect, it } from 'vitest'
import { projectLeadIn } from '@/lib/h3-project-playback'
import type { ProjectClip, ProjectNativeMetadata } from '@/types/project'

const parentMeta: ProjectNativeMetadata = {
  artifact_id: 'p', parent_artifact_id: null, source_kind: 'native_sampler',
  start_frame: 0, end_frame: 56, context_frames: 0, continuity_mode: 'shot',
}
const childMeta: ProjectNativeMetadata = {
  artifact_id: 'c', parent_artifact_id: 'p', source_kind: 'native_sampler',
  start_frame: 56, end_frame: 107, context_frames: 39, continuity_mode: 'context_anchor',
}

function clip(id: string, frames: number, meta: ProjectNativeMetadata, extra: Partial<ProjectClip> = {}, leadIn?: string): ProjectClip {
  const file = { file_path: `${id}.mp4`, file_name: `${id}.mp4`, media_revision: '7', source_frame_count: frames,
    native_metadata: meta, ...(leadIn ? { lead_in_path: leadIn } : {}) }
  return { id, index: 0, file_path: file.file_path, file_name: file.file_name, source_start_frame: 0, source_end_frame: frames,
    source_frame_count: frames, continuity_mode: meta.continuity_mode ?? 'shot', enabled: true, video_files: [file], ...extra }
}

describe('projectLeadIn', () => {
  const parent = clip('a', 56, parentMeta)
  const child = clip('b', 51, childMeta, {}, 'lead.mp4')

  it('covers the end of the version an Anchor continuation continued from, like the combined video', () => {
    expect(projectLeadIn(parent, child)).toStrictEqual({
      filePath: 'lead.mp4', mediaRevision: '7', startFrame: 17, endFrame: 56, sourceStartFrame: 0, audible: true,
    })
    // A clip that keeps a locked sound keeps it under the lead-in.
    expect(projectLeadIn({ ...parent, audio_locked: true }, child)?.audible).toBe(false)
    expect(projectLeadIn(parent, undefined)).toBeNull()
  })

  it('leaves each clip its own frames when the seam changed', () => {
    for (const following of [
      { ...child, source_start_frame: 3 },
      clip('b', 51, childMeta),
      clip('b', 51, { ...childMeta, parent_artifact_id: 'other' }, {}, 'lead.mp4'),
      clip('b', 51, { ...childMeta, continuity_mode: 'context_drift' }, {}, 'lead.mp4'),
      clip('b', 51, { ...childMeta, start_frame: 60 }, {}, 'lead.mp4'),
      clip('b', 51, { ...childMeta, source_kind: 'imported_seed' }, {}, 'lead.mp4'),
      clip('b', 51, { ...childMeta, context_frames: 0 }, {}, 'lead.mp4'),
      { ...child, file_path: 'missing.mp4' },
    ]) {
      expect(projectLeadIn(parent, following)).toBeNull()
    }
    expect(projectLeadIn({ ...parent, source_end_frame: 50 }, child)).toBeNull()
  })

  it('covers a predecessor trimmed shorter than the overlap from its own start', () => {
    const short = { ...parent, source_start_frame: 30 }
    expect(projectLeadIn(short, child)).toMatchObject({ startFrame: 0, endFrame: 26, sourceStartFrame: 13 })
  })
})
