import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'

export type QueueResult =
  | { status: 'queued'; prompt_id: string }
  | { status: 'failed' | 'unknown'; error: string }

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return (error.message || error.name).slice(0, 4000)
  return (typeof error === 'object' ? JSON.stringify(error) : String(error)).slice(0, 4000)
}

/** ComfyApp has no public busy accessor; a native call queued meanwhile could run another graph. */
export function nativeQueueBusy(app: ComfyApp): boolean {
  return Boolean(Reflect.get(app, 'processingQueue'))
}

/**
 * Press the native Run once for the visible workflow, whose id must be `workflowId`.
 * Observe the native response to obtain prompt_id; never build or submit an API prompt here.
 * ComfyApp owns serialization and before/afterQueued hooks.
 */
export async function queueCurrentWorkflow(app: ComfyApp, workflowId: unknown): Promise<QueueResult> {
  let submissionAttempted = false
  let promptId: string | undefined
  let queueError: unknown
  try {
    if (nativeQueueBusy(app)) {
      throw new Error('ComfyUI started another submission meanwhile; the workflow was not queued')
    }
    const original = app.api.queuePrompt
    let observing = true
    const observe: typeof original = async (number, data, ...options) => {
      if (!observing) return original.call(app.api, number, data, ...options)
      if (data.workflow.id !== workflowId || submissionAttempted) {
        queueError = new Error('Workflow changed or another run started during submission')
        throw queueError
      }
      submissionAttempted = true
      try {
        const response = await original.call(app.api, number, data, ...options)
        promptId = response.prompt_id
        return response
      } catch (error) {
        queueError = error
        throw error
      }
    }
    app.api.queuePrompt = observe
    try {
      const success = await app.queuePrompt(0, 1)
      // An accepted prompt remains queued even if a later UI callback fails.
      if (promptId) return { status: 'queued', prompt_id: promptId }
      if (!success || queueError) throw queueError ?? new Error('ComfyUI rejected the workflow; check node errors')
      throw new Error('ComfyUI did not return a prompt_id; check queue/history before retrying')
    } finally {
      observing = false
      if (app.api.queuePrompt === observe) app.api.queuePrompt = original
    }
  } catch (error) {
    if (promptId) return { status: 'queued', prompt_id: promptId }
    // Network failure may occur after the backend accepted the prompt.
    return { status: submissionAttempted ? 'unknown' : 'failed', error: errorMessage(error) }
  }
}
