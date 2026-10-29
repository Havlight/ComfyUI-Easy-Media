import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { convertH3Plan, type H3PlanConversion } from '@/lib/h3-plan-convert'
import { translate } from '@/lib/i18n'
import { uuid } from '@/lib/uuid'
import type { TrackData } from '@/types/multitrack'

type Workflow = NonNullable<Parameters<ComfyApp['loadGraphData']>[0]>

export interface WorkflowSubmission {
  kind?: 'workflow'
  request_id: string
  claim_token: string
  workflow: Workflow
  mode: 'new_tab' | 'replace'
  name: string
  auto_queue: boolean
}

/** A plan to convert into track data with this page's importer; no tab opens. */
export interface PlanConversionJob {
  kind: 'plan'
  request_id: string
  claim_token: string
  track_data: TrackData
  plan: unknown
  format: string
}

/** The server's targeted wake-up: a hidden tab's own polling may run only once a minute. */
export const WORKFLOW_JOB_EVENT = 'easy_media_workflow_job'

type SubmissionResult =
  | { status: 'loaded' }
  | { status: 'queued'; prompt_id: string }
  | { status: 'converted'; conversion: H3PlanConversion }
  | { status: 'failed' | 'unknown'; error: string }

function errorMessage(error: unknown): string {
  if (error instanceof Error) return (error.message || error.name).slice(0, 4000)
  return (typeof error === 'object' ? JSON.stringify(error) : String(error)).slice(0, 4000)
}

/** Load the visible editor first, then use the same queue path as the Run button. */
export async function runWorkflowSubmission(
  app: ComfyApp,
  job: WorkflowSubmission,
): Promise<SubmissionResult> {
  let submissionAttempted = false
  let promptId: string | undefined
  let queueError: unknown
  try {
    // ComfyApp has no public busy accessor. A queued native call can otherwise
    // return early and run against a different workflow later.
    if (Reflect.get(app, 'processingQueue')) {
      throw new Error('ComfyUI is already submitting a workflow; wait for it to finish')
    }
    const current = app.extensionManager.workflow.activeWorkflow
    if (job.mode === 'replace' && !current) {
      throw new Error('There is no active workflow to replace')
    }
    const workflow = structuredClone(job.workflow)
    // A fresh identity prevents same-name imports reusing another existing tab.
    workflow.id = uuid()
    const loaded: unknown = await app.loadGraphData(
      workflow, true, true,
      job.mode === 'replace' ? current : `${job.name.replace(/\.json$/i, '')}-${job.request_id}`,
    )
    if (loaded === false || app.rootGraph.serialize().id !== workflow.id) {
      throw new Error('ComfyUI did not load the requested workflow')
    }
    app.canvas.setDirty(true, true)
    if (!job.auto_queue) return { status: 'loaded' }
    if (Reflect.get(app, 'processingQueue')) {
      throw new Error('ComfyUI started another submission while loading; workflow was opened but not queued')
    }

    // Observe the native response to obtain prompt_id; never build or submit an
    // API prompt here. ComfyApp owns serialization and before/afterQueued hooks.
    const original = app.api.queuePrompt
    let observing = true
    const observe: typeof original = async (number, data, ...options) => {
      if (!observing) return original.call(app.api, number, data, ...options)
      if (data.workflow.id !== workflow.id || submissionAttempted) {
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

/** Convert a plan in the page's own locale, so messages read as the preview would show them. */
export async function runPlanConversion(app: ComfyApp, job: PlanConversionJob): Promise<SubmissionResult> {
  try {
    const locale = (app as unknown as { ui?: { settings?: { settingsValues?: Record<string, unknown> } } })
      .ui?.settings?.settingsValues?.['Comfy.Locale']
    const t = (path: string, params?: Record<string, string | number>) => translate(typeof locale === 'string' ? locale : undefined, path, params)
    const conversion = await convertH3Plan(job.track_data, job.plan, job.format, t,
      (path, init) => app.api.fetchApi(path, init))
    return { status: 'converted', conversion }
  } catch (error) {
    return { status: 'failed', error: errorMessage(error) }
  }
}

/** Each page claims at most one job. A lost response is never executed twice. */
export function installWorkflowSubmission(app: ComfyApp): () => void {
  const clientId = uuid()
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let pendingResult: { job: WorkflowSubmission | PlanConversionJob; result: SubmissionResult } | undefined
  let reportedError = false
  let polling = false
  let wakeAgain = false

  async function post(path: string, body: object): Promise<unknown> {
    const response = await app.api.fetchApi(`/easy-media/workflow/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new Error(`Workflow bridge: HTTP ${response.status}: ${await response.text()}`)
    return response.json()
  }

  async function poll(): Promise<void> {
    polling = true
    try {
      if (!app.api.clientId) return
      if (pendingResult) {
        const { job, result } = pendingResult
        await post(`submissions/${job.request_id}/result`, { ...result, claim_token: job.claim_token })
        pendingResult = undefined
      }
      if (!app.extensionManager.workflow.activeWorkflow || app.extensionManager.workflow.isBusy) return
      const response = await post('poll', {
        client_id: clientId,
        session_id: app.api.clientId,
        title: document.title.slice(0, 300),
      }) as { job: WorkflowSubmission | PlanConversionJob | null }
      reportedError = false
      const job = response.job
      if (job) {
        const result = job.kind === 'plan' ? await runPlanConversion(app, job) : await runWorkflowSubmission(app, job)
        pendingResult = { job, result }
        if (result.status === 'failed' || result.status === 'unknown') {
          app.extensionManager.toast.add({ severity: 'error', summary: 'Easy Media workflow', detail: result.error, life: 8000 })
        }
        // Deliver the result now rather than on the next, possibly throttled, tick.
        wakeAgain = true
      }
    } catch (error) {
      if (!reportedError) console.error('[Easy Media] Workflow submission bridge:', error)
      reportedError = true
    } finally {
      polling = false
      if (!stopped) schedule(wakeAgain && !reportedError ? 0 : reportedError ? 5000 : 1000)
      wakeAgain = false
    }
  }

  function schedule(delay: number) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { void poll() }, delay)
  }

  // The server names this page's socket when a job arrives for it.
  function wake() {
    if (stopped) return
    if (polling) wakeAgain = true
    else schedule(0)
  }

  app.api.addCustomEventListener?.(WORKFLOW_JOB_EVENT, wake)
  // Defer until extension setup and the initial workflow restore have completed.
  timer = setTimeout(() => { void poll() }, 1000)
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    app.api.removeCustomEventListener?.(WORKFLOW_JOB_EVENT, wake)
  }
}
