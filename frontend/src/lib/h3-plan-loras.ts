import type { H3PlanChange, H3PlanFile, H3PlanIssue } from '@/lib/h3-plan'
import { uuid } from '@/lib/uuid'
import type { TrackData } from '@/types/multitrack'
import type { SegmentLoraPlan, SegmentLoraRule } from '@/types/segment-loras'

/**
 * Segment LoRA rules in a plan file. The H3 Segment LoRA node counts segments; a plan names
 * tasks instead, so its rules follow their tasks when tasks are added, removed or reordered.
 *
 * `at` is the first task a rule applies to and `until` the last, as a task id or the task's
 * 1-based position in the file; without `at` it starts at the first segment, without `until`
 * it runs to the end. A rule that starts or ends inside a task split by generation markers is
 * written with the node's own `start_segment` and `segment_count` and keeps those numbers.
 */
export interface H3PlanLora {
  id?: string
  lora: string
  strength?: number
  stage?: SegmentLoraRule['stage']
  enabled?: boolean
  at?: string | number
  until?: string | number
  start_segment?: number
  segment_count?: number
}

/** Why a plan's rules cannot be written to the LoRA node: it is not one node the Editor's Projects alone use. */
export type H3LoraNodeError = 'loras_ambiguous' | 'loras_shared' | 'loras_linked' | 'loras_unreadable'

/**
 * The connected H3 Segment LoRA node's rules and the installed LoRA files, or why rules cannot be
 * written there. `inactive` when the node is bypassed or muted, so runs use none of its rules.
 */
export type H3PlanLoraTarget = { rules: SegmentLoraPlan; files?: readonly string[]; inactive?: true } | { error: H3LoraNodeError }

export interface H3PlanLoraRow {
  id: string
  /** 1-based row after import; removed rules keep their old row. */
  index: number
  change: H3PlanChange
  lora: string
  strength: number
  stage: SegmentLoraRule['stage']
  enabled: boolean
  start: number
  /** The last segment, or null for every segment to the end. */
  end: number | null
}

export interface H3PlanLoraImport {
  rules: SegmentLoraPlan
  changed: boolean
  rows: H3PlanLoraRow[]
}

const LORA_KEYS = new Set(['id', 'lora', 'strength', 'stage', 'enabled', 'at', 'until', 'start_segment', 'segment_count'])
const STAGES = new Set<unknown>(['all', 'first', 'second'])

interface TaskSegments { id: string; first: number; last: number }

/** Each task's 1-based segment numbers as Project counts them: a generation marker inside a task adds a segment. */
export function h3TaskSegments(data: TrackData): TaskSegments[] {
  const tasks = data.tracks.filter((track) => track.type === 'task').flatMap((track) => track.segments)
    .sort((a, b) => a.start_frame - b.start_frame || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const markers = (data.task_markers ?? []).map((marker) => marker.frame)
  let next = 1
  return tasks.map((task) => {
    const inside = markers.filter((frame) => task.start_frame < frame && frame < task.end_frame).length
    const entry = { id: task.id, first: next, last: next + inside }
    next += inside + 1
    return entry
  })
}

/** A rule's first and last task; {} for the whole timeline; null when an end falls inside a task. */
function taskAnchors(rule: SegmentLoraRule, segments: TaskSegments[]): { at?: string; until?: string } | null {
  const end = rule.segment_count === -1 ? null : rule.start_segment + rule.segment_count - 1
  if (rule.start_segment === 1 && end === null) return {}
  const first = segments.find((segment) => segment.first === rule.start_segment)
  const last = end === null ? undefined : segments.find((segment) => segment.last === end)
  if (!first || (end !== null && !last)) return null
  return { at: first.id, ...(last ? { until: last.id } : {}) }
}

/** The node's rules as plan entries, by task where their ends are task boundaries. */
export function exportH3PlanLoras(plan: SegmentLoraPlan, data: TrackData): H3PlanLora[] {
  const segments = h3TaskSegments(data)
  return plan.rules.map((rule) => ({
    id: rule.id,
    lora: rule.lora,
    strength: rule.strength,
    stage: rule.stage,
    ...(rule.enabled ? {} : { enabled: false }),
    ...(taskAnchors(rule, segments) ?? { start_segment: rule.start_segment, segment_count: rule.segment_count }),
  }))
}

function sameRule(left: SegmentLoraRule, right: SegmentLoraRule): boolean {
  return left.enabled === right.enabled && left.lora === right.lora && left.start_segment === right.start_segment
    && left.segment_count === right.segment_count && left.strength === right.strength && left.stage === right.stage
}

function loraRows(before: readonly SegmentLoraRule[], after: readonly SegmentLoraRule[]): H3PlanLoraRow[] {
  const old = new Map(before.map((rule) => [rule.id, rule]))
  const row = (rule: SegmentLoraRule, index: number, change: H3PlanChange): H3PlanLoraRow => ({
    id: rule.id, index, change, lora: rule.lora, strength: rule.strength, stage: rule.stage, enabled: rule.enabled,
    start: rule.start_segment, end: rule.segment_count === -1 ? null : rule.start_segment + rule.segment_count - 1,
  })
  const rows = after.map((rule, index) => {
    const previous = old.get(rule.id)
    return row(rule, index + 1, !previous ? 'added' : sameRule(previous, rule) ? 'unchanged' : 'modified')
  })
  const kept = new Set(after.map((rule) => rule.id))
  before.forEach((rule, index) => { if (!kept.has(rule.id)) rows.push(row(rule, index + 1, 'removed')) })
  return rows
}

/** Whether the tasks or their segment numbers changed. */
function tasksMoved(before: TrackData, after: TrackData): boolean {
  return JSON.stringify(h3TaskSegments(before)) !== JSON.stringify(h3TaskSegments(after))
}

/**
 * Without `loras` in the plan, each rule keeps its tasks: it spans the tasks it covered that are
 * still there, and says so when that span now takes in other tasks too.
 */
function followTasks(rules: readonly SegmentLoraRule[], before: TrackData, after: TrackData, issues: H3PlanIssue[]): SegmentLoraRule[] {
  const old = h3TaskSegments(before)
  const order = old.map((segment) => segment.id)
  const timeline = h3TaskSegments(after)
  const now = new Map(timeline.map((segment) => [segment.id, segment]))
  const end = timeline.at(-1)?.last ?? 0
  const moved = tasksMoved(before, after)
  return rules.flatMap((rule) => {
    const anchors = taskAnchors(rule, old)
    if (!anchors?.at) {
      // Numbered inside a task split by a marker: the rule keeps its numbers.
      if (!anchors && moved) issues.push({ level: 'warning', code: 'lora_rule_kept_numbers', params: { lora: rule.lora } })
      return [rule]
    }
    const from = order.indexOf(anchors.at)
    const to = anchors.until ? order.indexOf(anchors.until) : order.length - 1
    const kept = order.slice(from, to + 1).filter((id) => now.has(id)).map((id) => now.get(id)!)
    if (!kept.length) {
      issues.push({ level: 'warning', code: 'lora_rule_dropped', params: { lora: rule.lora } })
      return []
    }
    const first = Math.min(...kept.map((segment) => segment.first))
    const last = anchors.until ? Math.max(...kept.map((segment) => segment.last)) : null
    const covered = kept.reduce((count, segment) => count + segment.last - segment.first + 1, 0)
    if ((last ?? end) - first + 1 !== covered) issues.push({ level: 'warning', code: 'lora_rule_spread', params: { lora: rule.lora } })
    return [{ ...rule, start_segment: first, segment_count: last === null ? -1 : last - first + 1 }]
  })
}

/** A file name as ComfyUI lists LoRAs, as the node checks it: no host path, no way out of the LoRA folder. */
function validLoraName(name: string): boolean {
  return !!name && !name.includes('\0') && !name.startsWith('/') && !name.includes(':') && !name.split('/').includes('..')
}

/** The plan's `loras` as node rules, against the tasks after import. */
function readRules(
  raw: unknown[], target: { rules: SegmentLoraPlan; files?: readonly string[] }, after: TrackData,
  positions: readonly (string | undefined)[], issues: H3PlanIssue[],
): SegmentLoraRule[] {
  const segments = new Map(h3TaskSegments(after).map((segment) => [segment.id, segment]))
  const anchor = (value: unknown, edge: 'first' | 'last'): number | undefined => {
    const id = typeof value === 'number' && Number.isInteger(value) && value > 0 ? positions[value - 1]
      : typeof value === 'string' && value ? value : undefined
    return id === undefined ? undefined : segments.get(id)?.[edge]
  }
  const ids = new Set<string>()
  const rules: SegmentLoraRule[] = []
  raw.forEach((value, index) => {
    const report = (level: H3PlanIssue['level'], code: string, params?: H3PlanIssue['params']) => {
      issues.push({ level, lora: index + 1, code, ...(params ? { params } : {}) })
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      report('error', 'invalid_lora')
      return
    }
    const item = value as Record<string, unknown>
    for (const key of Object.keys(item)) if (!LORA_KEYS.has(key)) report('warning', 'unknown_field', { field: key })
    const lora = typeof item.lora === 'string' ? item.lora.replaceAll('\\', '/') : ''
    if (!lora) {
      report('error', 'invalid_lora')
      return
    }
    if (!validLoraName(lora)) {
      report('error', 'invalid_lora_name', { lora })
      return
    }
    if (target.files && !target.files.includes(lora)) report('error', 'lora_missing', { lora })
    if (item.strength !== undefined && !(typeof item.strength === 'number' && Number.isFinite(item.strength))) {
      report('error', 'invalid_lora_strength')
    }
    if (item.stage !== undefined && !STAGES.has(item.stage)) report('error', 'invalid_lora_stage')
    if (item.enabled !== undefined && typeof item.enabled !== 'boolean') report('error', 'invalid_flag', { flag: 'enabled' })
    if (item.id !== undefined && (typeof item.id !== 'string' || !item.id)) {
      report('error', 'invalid_id')
      return
    }
    const id = (item.id as string | undefined) ?? uuid()
    if (ids.has(id)) {
      report('error', 'duplicate_lora_id', { id })
      return
    }
    ids.add(id)
    const bySegment = item.start_segment !== undefined || item.segment_count !== undefined
    if (bySegment && (item.at !== undefined || item.until !== undefined)) {
      report('error', 'lora_anchor_mixed')
      return
    }
    let start = 1
    let count = -1
    if (bySegment) {
      start = item.start_segment === undefined ? 1 : item.start_segment as number
      count = item.segment_count === undefined ? -1 : item.segment_count as number
      if (!Number.isInteger(start) || start < 1 || !Number.isInteger(count) || (count !== -1 && count < 1)) {
        report('error', 'invalid_lora_range')
        return
      }
    } else {
      if (item.at !== undefined) {
        const first = anchor(item.at, 'first')
        if (first === undefined) {
          report('error', 'unknown_anchor', { at: String(item.at) })
          return
        }
        start = first
      }
      if (item.until !== undefined) {
        const last = anchor(item.until, 'last')
        if (last === undefined) {
          report('error', 'unknown_anchor', { at: String(item.until) })
          return
        }
        if (last < start) {
          report('error', 'lora_range')
          return
        }
        count = last - start + 1
      }
    }
    rules.push({
      id, enabled: item.enabled !== false, lora, start_segment: start, segment_count: count,
      strength: typeof item.strength === 'number' ? item.strength : 1, stage: (item.stage as SegmentLoraRule['stage'] | undefined) ?? 'all',
    })
  })
  return rules
}

/**
 * The segment LoRA rules after a plan import: the plan's `loras` when it has them, otherwise
 * the node's rules moved with their tasks. Undefined when nothing is to be written.
 */
export function buildH3PlanLoras(
  plan: H3PlanFile, before: TrackData, after: TrackData, positions: readonly (string | undefined)[],
  target: H3PlanLoraTarget | undefined, issues: H3PlanIssue[],
): H3PlanLoraImport | undefined {
  const raw: unknown = plan.loras
  if (raw === undefined) {
    if (!target) return undefined
    if ('error' in target) {
      // The node's rules stay on their segment numbers while the tasks move under them.
      if (tasksMoved(before, after)) issues.push({ level: 'warning', code: 'loras_not_followed' })
      return undefined
    }
    const rules = followTasks(target.rules.rules, before, after, issues)
    const changed = rules.length !== target.rules.rules.length || rules.some((rule, index) => !sameRule(rule, target.rules.rules[index]))
    return { rules: { version: 1, rules }, changed, rows: loraRows(target.rules.rules, rules) }
  }
  if (!Array.isArray(raw)) {
    issues.push({ level: 'error', code: 'invalid_loras' })
    return undefined
  }
  if (!target) {
    if (raw.length) issues.push({ level: 'error', code: 'loras_no_node' })
    return undefined
  }
  if ('error' in target) {
    issues.push({ level: 'error', code: target.error })
    return undefined
  }
  const rules = readRules(raw, target, after, positions, issues)
  if (target.inactive && rules.some((rule) => rule.enabled)) issues.push({ level: 'warning', code: 'loras_inactive' })
  const old = target.rules.rules
  const changed = rules.length !== old.length || rules.some((rule, index) => rule.id !== old[index].id || !sameRule(rule, old[index]))
  return { rules: { version: 1, rules }, changed, rows: loraRows(old, rules) }
}

/** The LoRA files ComfyUI lists, with forward slashes as rules store them. */
export async function fetchH3LoraFiles(fetcher: (path: string, init: RequestInit) => Promise<Response>): Promise<string[]> {
  const response = await fetcher('/models/loras', {})
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const list: unknown = await response.json()
  if (!Array.isArray(list) || !list.every((item) => typeof item === 'string')) throw new Error('Invalid LoRA model list')
  return list.map((name: string) => name.replaceAll('\\', '/'))
}
