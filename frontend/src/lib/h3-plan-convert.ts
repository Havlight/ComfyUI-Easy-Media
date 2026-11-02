import { prepareH3NativeEdit } from '@/hooks/use-h3-native-editing'
import {
  buildH3PlanImport,
  formatH3PlanTaskNumbers,
  groupH3PlanIssues,
  h3PlanMediaKey,
  h3PlanMediaRequests,
  parseH3Plan,
  type H3PlanFile,
  type H3PlanIssue,
  type H3PlanIssueGroup,
  type H3PlanMediaRequest,
  type H3PlanMediaResult,
  type H3PlanRow,
  type H3PlanTrackRow,
} from '@/lib/h3-plan'
import { fetchH3LoraFiles, type H3PlanLoraImport, type H3PlanLoraTarget } from '@/lib/h3-plan-loras'
import { normalizeTrackData } from '@/lib/multitrack-utils'
import type { TrackData } from '@/types/multitrack'

type Translate = (path: string, params?: Record<string, string | number>) => string

/** Ask the server to copy or check every file a plan names. */
export async function resolveH3PlanMedia(
  requests: H3PlanMediaRequest[],
  fetcher: (path: string, init: RequestInit) => Promise<Response> = fetch,
): Promise<Map<string, H3PlanMediaResult>> {
  if (requests.length === 0) return new Map()
  const response = await fetcher('/easy-media/plan/resolve-media', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: requests }),
  })
  const payload = await response.json() as { results?: H3PlanMediaResult[]; error?: string }
  if (!response.ok || !Array.isArray(payload.results)) throw new Error(payload.error ?? `HTTP ${response.status}`)
  return new Map(requests.map((request, index) => [h3PlanMediaKey(request), payload.results![index]]))
}

/** One preview line: where the problem is, then what it is. */
export function h3PlanIssueText(item: H3PlanIssueGroup, t: Translate): string {
  const prefix = item.lora !== undefined ? t('h3Plan.loraPrefix', { n: item.lora })
    : item.track !== undefined
      ? item.clip !== undefined ? t('h3Plan.clipPrefix', { track: item.track, clip: item.clip }) : t('h3Plan.trackPrefix', { track: item.track })
      : item.tasks.length === 0 ? ''
        : item.tasks.length === 1 ? t('h3Plan.taskPrefix', { n: item.tasks[0] })
          : t('h3Plan.tasksPrefix', { list: formatH3PlanTaskNumbers(item.tasks) })
  return `${prefix}${t(`h3Plan.issue_${item.code}`, item.params)}`
}

/** The LoRA target with the installed files when the plan names LoRAs; a listing failure is a warning. */
export async function withH3LoraFiles(
  plan: H3PlanFile,
  target: H3PlanLoraTarget | undefined,
  fetcher: (path: string, init: RequestInit) => Promise<Response> = fetch,
): Promise<{ target: H3PlanLoraTarget | undefined; issues: H3PlanIssue[] }> {
  if (!target || 'error' in target || !Array.isArray(plan.loras) || plan.loras.length === 0) return { target, issues: [] }
  try {
    return { target: { ...target, files: await fetchH3LoraFiles(fetcher) }, issues: [] }
  } catch (cause: unknown) {
    return { target, issues: [{ level: 'warning', code: 'lora_files_unavailable', params: { message: cause instanceof Error ? cause.message : String(cause) } }] }
  }
}

export interface H3PlanConversion {
  /** The converted track data; null when an error blocks the plan. */
  track_data: TrackData | null
  errors: string[]
  warnings: string[]
  tasks: H3PlanRow[]
  tracks: H3PlanTrackRow[]
  /** The segment LoRA rules after the import, when the Editor's Project has a LoRA node to write. */
  segment_loras?: H3PlanLoraImport
}

/**
 * Convert a plan into an Editor's track data (and its LoRA node's rules) exactly as the import
 * dialog would, without touching any tab, for the skill to write into a workflow.
 */
export async function convertH3Plan(
  trackData: TrackData,
  plan: unknown,
  format: string,
  t: Translate,
  fetcher?: (path: string, init: RequestInit) => Promise<Response>,
  loras?: H3PlanLoraTarget,
): Promise<H3PlanConversion> {
  const lines = (issues: H3PlanIssueGroup[], level: 'error' | 'warning') => issues
    .filter((item) => item.level === level).map((item) => h3PlanIssueText(item, t))
  const parsed = parseH3Plan(JSON.stringify(plan))
  if (!parsed.plan) {
    return { track_data: null, errors: lines(groupH3PlanIssues(parsed.issues), 'error'), warnings: [], tasks: [], tracks: [] }
  }
  const current = normalizeTrackData(trackData)
  const media = await resolveH3PlanMedia(h3PlanMediaRequests(parsed.plan), fetcher)
  const listed = await withH3LoraFiles(parsed.plan, loras, fetcher)
  const result = buildH3PlanImport(current, parsed.plan, media, (candidate) => prepareH3NativeEdit(current, candidate, format), listed.target)
  const groups = groupH3PlanIssues([...listed.issues, ...result.issues])
  return {
    track_data: result.data,
    errors: lines(groups, 'error'),
    warnings: lines(groups, 'warning'),
    tasks: result.rows,
    tracks: result.trackRows,
    ...(result.loras ? { segment_loras: result.loras } : {}),
  }
}
