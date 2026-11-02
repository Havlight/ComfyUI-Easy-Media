import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { H3PlanImportDialog } from '@/components/widgets/multitrack/H3PlanImportDialog'
import { prepareH3NativeEdit } from '@/hooks/use-h3-native-editing'
import type { TrackData } from '@/types/multitrack'

function timeline(): TrackData {
  const raw: TrackData = {
    h3_native: { version: 2 },
    total_length: 124,
    frame_rate: 24,
    tracks: [{ id: 'tasks', name: 'Task 0', type: 'task', color: '', muted: false, locked: false, segments: [{
      id: 't1', start_frame: 0, end_frame: 124, color: '',
      content: { media_type: 'none', task_mode: 'ref', continuity_mode: 'shot', user_prompt: 'Old line', images: [] },
    }] }],
  }
  return prepareH3NativeEdit(raw, raw, 'MiniMax')
}

function renderDialog(text: string, onApply = vi.fn()) {
  render(
    <H3PlanImportDialog
      request={{ fileName: 'plan.json', text }}
      current={timeline()}
      format="MiniMax"
      projects={[]}
      currentStatuses={[]}
      onApply={onApply}
      onClose={vi.fn()}
    />,
  )
  return onApply
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('H3PlanImportDialog', () => {
  it('previews each task, copies files through the server and applies once', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ results: [
      { source_type: 'input', file_path: 'easy_media/imports/abc.png', file_name: 'alice.png' },
    ] }))))
    vi.stubGlobal('fetch', fetchMock)
    const onApply = renderDialog(JSON.stringify({ version: 1, tasks: [
      { id: 't1', prompt: 'New line' },
      { seconds: 4.958, mode: 'ref', continuity: 'context', prompt: '<Picture 1> joins', images: ['C:/shots/alice.png'] },
    ] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-rows')).not.toBeNull())
    expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown[] as [string, RequestInit])[1].body))).toEqual({
      items: [{ media_type: 'image', file: 'C:/shots/alice.png' }],
    })
    expect(screen.getByTestId('h3-plan-row-t1').textContent).toContain('Changed')
    expect(screen.getByText('Img 1 · Vid 0 · Aud 0')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenCalledTimes(1)
    const [applied] = onApply.mock.calls[0] as [TrackData]
    expect(applied.tracks[0].segments.map((segment) => segment.content.user_prompt)).toEqual(['New line', '<Picture 1> joins'])
  })

  it('previews the LoRA rules a plan lists and hands them over on apply', async () => {
    vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === '/models/loras' ? ['mmh3\\a.safetensors'] : { results: [] })))))
    const onApply = vi.fn()
    render(
      <H3PlanImportDialog
        request={{ fileName: 'plan.json', loras: { rules: { version: 1, rules: [] } }, text: JSON.stringify({ version: 1, tasks: [{ id: 't1' }],
          loras: [{ lora: 'mmh3/a.safetensors', strength: 0.6, stage: 'second' }] }) }}
        current={timeline()}
        format="MiniMax"
        projects={[]}
        currentStatuses={[]}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    )

    await waitFor(() => expect(screen.getByTestId('h3-plan-lora-rows').textContent).toContain('mmh3/a.safetensors'))
    expect(screen.getByTestId('h3-plan-lora-rows').textContent).toContain('Second stage')
    expect(screen.getByTestId('h3-plan-lora-rows').textContent).toContain('1–end')
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    const [, rules] = onApply.mock.calls[0] as [TrackData, { rules: object[] }]
    expect(rules.rules).toEqual([expect.objectContaining({ lora: 'mmh3/a.safetensors', strength: 0.6, stage: 'second', start_segment: 1, segment_count: -1 })])
  })

  it('checks saved results without the rules a bypassed or muted LoRA node would take', async () => {
    vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === '/models/loras' ? ['mmh3/a.safetensors'] : { results: [] })))))
    const empty = { version: 1 as const, rules: [] }
    const project = { id: '15', name: 'p', project_snapshot: { known: true, segment_loras: empty, recipe: { sampling_mode: 'single' }, start_segment: 1, segment_count: -1 } }
    const text = JSON.stringify({ version: 1, tasks: [{ id: 't1' }], loras: [{ lora: 'mmh3/a.safetensors' }] })
    const statusRules = async (inactive: boolean) => {
      const fetchApi = vi.fn(async (_path: string, _init?: RequestInit) => new Response(JSON.stringify({ project_name: 'p', tasks: [] })))
      const view = render(
        <H3PlanImportDialog
          request={{ fileName: 'plan.json', loras: { rules: empty, ...(inactive ? { inactive: true as const } : {}) }, text }}
          current={timeline()}
          format="MiniMax"
          app={{ api: { fetchApi } } as unknown as ComfyApp}
          projects={[project]}
          currentStatuses={[]}
          onApply={vi.fn()}
          onClose={vi.fn()}
        />,
      )
      await waitFor(() => expect(fetchApi).toHaveBeenCalled())
      const [, init] = fetchApi.mock.calls[0]
      view.unmount()
      return JSON.parse(String(init?.body)).project_snapshot.segment_loras.rules as object[]
    }
    expect(await statusRules(false)).toEqual([expect.objectContaining({ lora: 'mmh3/a.safetensors' })])
    expect(await statusRules(true)).toEqual([])
  })

  it('stays open and says why when the import cannot be applied', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ results: [] })))))
    renderDialog(JSON.stringify({ version: 1, tasks: [{ id: 't1', prompt: 'New line' }] }), vi.fn(() => {
      throw new Error('The LoRA node is gone.')
    }))
    await waitFor(() => expect(screen.getByTestId('h3-plan-rows')).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByTestId('h3-plan-apply-error').textContent).toBe('The LoRA node is gone.')
  })

  it('lists blocking problems and keeps Apply disabled', async () => {
    renderDialog(JSON.stringify({ version: 1, tasks: [{ id: 't1', mode: 'r2v' }] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-errors').textContent).toContain('Task 1: Mode "r2v" is not one of'))
    expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('h3-plan-blocked').textContent).toBe('Fix the 1 error(s) listed above before applying.')
  })

  it('previews the timeline tracks a plan lists and names a clip that cannot be placed', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ results: [
      { source_type: 'input', file_path: 'song.wav', file_name: 'song.wav' },
    ] })))))
    const onApply = renderDialog(JSON.stringify({ version: 1, tasks: [{ id: 't1' }], tracks: [
      { type: 'audio', audio_locked: true, clips: [{ input: 'song.wav', start: 0, end: 5 }] },
    ] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-track-rows').textContent).toContain('NewAudioLockedsong.wav · 0s–5s'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    const [applied] = onApply.mock.calls[0] as [TrackData]
    expect(applied.tracks.map((track) => [track.type, track.audio_locked ?? false, track.segments.length])).toEqual([
      ['task', false, 1], ['audio', true, 1],
    ])
  })

  it('says which clip has a problem', async () => {
    renderDialog(JSON.stringify({ version: 1, tasks: [{ id: 't1' }], tracks: [
      { type: 'video', clips: [{ url: 'https://example.com/a.mp4', at: 1 }] },
    ] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-errors').textContent)
      .toBe('Track 1, clip 1: The length of https://example.com/a.mp4 is unknown; write end.'))
  })

  it('names the tasks once when several share a warning', async () => {
    renderDialog(JSON.stringify({ version: 1, tasks: [
      { id: 't1', audios: [{ track: 'gone' }] },
      { seconds: 4.958, continuity: 'context', audios: [{ track: 'gone' }] },
    ] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-warnings').textContent)
      .toBe('Tasks 1–2: No timeline track has id gone; this item is skipped.'))
    expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(false)
  })
})
