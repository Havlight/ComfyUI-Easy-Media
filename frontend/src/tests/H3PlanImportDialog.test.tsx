import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

  it('lists blocking problems and keeps Apply disabled', async () => {
    renderDialog(JSON.stringify({ version: 1, tasks: [{ id: 't1', mode: 'r2v' }] }))

    await waitFor(() => expect(screen.getByTestId('h3-plan-errors').textContent).toContain('Task 1: Mode "r2v" is not one of'))
    expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('h3-plan-blocked').textContent).toBe('Fix the 1 error(s) listed above before applying.')
  })
})
