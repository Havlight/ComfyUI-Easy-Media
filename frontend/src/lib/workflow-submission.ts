import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { applyAgentTab, readAgentTab, type TabApplyJob, type TabApplyResult, type TabReadJob, type TabReadResult } from '@/lib/agent-tabs'
import { convertH3Plan, type H3PlanConversion } from '@/lib/h3-plan-convert'
import { translate } from '@/lib/i18n'
import { errorMessage, nativeQueueBusy, queueCurrentWorkflow } from '@/lib/native-queue'
import { uuid } from '@/lib/uuid'
import type { TrackData } from '@/types/multitrack'

type Workflow = NonNullable<Parameters<ComfyApp['loadGraphData']>[0]>

export interface WorkflowSubmission {
  kind?: 'workflow'
  request_id: string
  claim_token: string
  workflow: Workflow
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

type BridgeJob = WorkflowSubmission | PlanConversionJob | TabReadJob | TabApplyJob

type SubmissionResult =
  | { status: 'loaded'; tab?: string }
  | { status: 'queued'; prompt_id: string; tab?: string }
  | { status: 'converted'; conversion: H3PlanConversion }
  | { status: 'failed' | 'unknown'; error: string }
  | TabReadResult
  | TabApplyResult

function runJob(app: ComfyApp, job: BridgeJob): Promise<SubmissionResult> {
  switch (job.kind) {
    case 'plan': return runPlanConversion(app, job)
    case 'tabs': return readAgentTab(app, job)
    case 'apply': return applyAgentTab(app, job)
    default: return runWorkflowSubmission(app, job)
  }
}

/** Open the workflow in a new tab, then use the same queue path as the Run button. */
export async function runWorkflowSubmission(
  app: ComfyApp,
  job: WorkflowSubmission,
): Promise<SubmissionResult> {
  try {
    if (nativeQueueBusy(app)) {
      throw new Error('ComfyUI is already submitting a workflow; wait for it to finish')
    }
    const workflow = structuredClone(job.workflow)
    // A fresh identity prevents same-name imports reusing another existing tab.
    workflow.id = uuid()
    const loaded: unknown = await app.loadGraphData(
      workflow, true, true, `${job.name.replace(/\.json$/i, '')}-${job.request_id}`,
    )
    if (loaded === false || app.rootGraph.serialize().id !== workflow.id) {
      throw new Error('ComfyUI did not load the requested workflow')
    }
    app.canvas.setDirty(true, true)
    const tab = app.extensionManager.workflow.activeWorkflow?.path
    const opened = tab ? { tab } : {}
    if (!job.auto_queue) return { status: 'loaded', ...opened }
    const result = await queueCurrentWorkflow(app, workflow.id)
    return result.status === 'queued' ? { ...result, ...opened } : result
  } catch (error) {
    return { status: 'failed', error: errorMessage(error) }
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
  let pendingResult: { job: BridgeJob; result: SubmissionResult } | undefined
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
      }) as { job: BridgeJob | null }
      reportedError = false
      const job = response.job
      if (job) {
        const result = await runJob(app, job)
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

  // A reloaded page keeps its tab's socket ID, so say this page is gone instead of letting the
  // server list it beside its replacement until the heartbeat expires. A page restored from the
  // back-forward cache registers again on its next poll.
  function leave() {
    if (!app.api.clientId) return
    void app.api.fetchApi('/easy-media/workflow/leave', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId, session_id: app.api.clientId }),
      keepalive: true,
    }).catch(() => {})
  }

  app.api.addCustomEventListener?.(WORKFLOW_JOB_EVENT, wake)
  window.addEventListener('pagehide', leave)
  // Defer until extension setup and the initial workflow restore have completed.
  timer = setTimeout(() => { void poll() }, 1000)
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    window.removeEventListener('pagehide', leave)
    app.api.removeCustomEventListener?.(WORKFLOW_JOB_EVENT, wake)
  }
}
