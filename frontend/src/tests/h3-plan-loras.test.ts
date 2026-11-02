import { describe, expect, it } from 'vitest'
import { prepareH3NativeEdit } from '@/hooks/use-h3-native-editing'
import { buildH3PlanImport, exportH3Plan, parseH3Plan } from '@/lib/h3-plan'
import { exportH3PlanLoras, h3TaskSegments, type H3PlanLoraTarget } from '@/lib/h3-plan-loras'
import type { MultiTrackSegment, TrackData } from '@/types/multitrack'
import type { SegmentLoraRule } from '@/types/segment-loras'

function task(id: string, start: number): MultiTrackSegment {
  return { id, start_frame: start, end_frame: start + 124, color: '', content: { media_type: 'none', task_mode: 'default', continuity_mode: 'shot', user_prompt: id } }
}

/** Three five-second shots: segments 1, 2 and 3. */
function timeline(): TrackData {
  const raw: TrackData = {
    h3_native: { version: 2 }, total_length: 372, frame_rate: 24,
    tracks: [{ id: 'tasks', name: 'Task 0', type: 'task', color: '', muted: false, locked: false,
      segments: [task('s01', 0), task('s02', 124), task('s03', 248)] }],
  }
  return prepareH3NativeEdit(raw, raw, 'MiniMax')
}

function rule(id: string, start: number, count: number, extra: Partial<SegmentLoraRule> = {}): SegmentLoraRule {
  return { id, enabled: true, lora: `mmh3/${id}.safetensors`, start_segment: start, segment_count: count, strength: 0.8, stage: 'all', ...extra }
}

function target(...rules: SegmentLoraRule[]): H3PlanLoraTarget {
  return { rules: { version: 1, rules }, files: ['mmh3/a.safetensors', ...rules.map((item) => item.lora)] }
}

function importWith(plan: object, loras: H3PlanLoraTarget | undefined, current = timeline()) {
  const parsed = parseH3Plan(JSON.stringify({ version: 1, tasks: [{ id: 's01' }, { id: 's02' }, { id: 's03' }], ...plan }))
  return buildH3PlanImport(current, parsed.plan!, new Map(), (candidate) => prepareH3NativeEdit(current, candidate, 'MiniMax'), loras)
}

describe('segment LoRA rules in H3 plan files', () => {
  it('exports rules by task, and by segment number where a marker splits the task', () => {
    const data = timeline()
    expect(exportH3PlanLoras({ version: 1, rules: [
      rule('all', 1, -1), rule('head', 1, 2), rule('mid', 2, 2, { enabled: false, stage: 'second' }), rule('tail', 2, -1),
    ] }, data)).toEqual([
      { id: 'all', lora: 'mmh3/all.safetensors', strength: 0.8, stage: 'all' },
      { id: 'head', lora: 'mmh3/head.safetensors', strength: 0.8, stage: 'all', at: 's01', until: 's02' },
      { id: 'mid', lora: 'mmh3/mid.safetensors', strength: 0.8, stage: 'second', enabled: false, at: 's02', until: 's03' },
      { id: 'tail', lora: 'mmh3/tail.safetensors', strength: 0.8, stage: 'all', at: 's02' },
    ])
    // A marker inside the last task makes it segments 3 and 4.
    const split = { ...data, task_markers: [{ id: 'm', frame: 300 }] }
    expect(h3TaskSegments(split)).toEqual([{ id: 's01', first: 1, last: 1 }, { id: 's02', first: 2, last: 2 }, { id: 's03', first: 3, last: 4 }])
    expect(exportH3PlanLoras({ version: 1, rules: [rule('part', 4, 1)] }, split)).toEqual([
      { id: 'part', lora: 'mmh3/part.safetensors', strength: 0.8, stage: 'all', start_segment: 4, segment_count: 1 }])
    // Without a LoRA node the plan has no `loras`; with one, importing the export changes nothing.
    expect(exportH3Plan(data)).not.toHaveProperty('loras')
    const plan = exportH3Plan(data, { version: 1, rules: [rule('mid', 2, 2)] })
    const again = importWith(plan, target(rule('mid', 2, 2)))
    expect(again.loras).toMatchObject({ changed: false, rows: [{ id: 'mid', change: 'unchanged', start: 2, end: 3 }] })
  })

  it("writes the plan's rules by task id or position and lists what changed", () => {
    const result = importWith({ loras: [
      { id: 'mid', lora: 'mmh3/mid.safetensors', strength: 0.5, at: 's03' },
      { lora: 'mmh3\\a.safetensors', at: 1, until: 2, stage: 'first' },
    ] }, target(rule('old', 1, -1), rule('mid', 2, 2)))
    expect(result.issues).toEqual([])
    expect(result.data).not.toBeNull()
    const rules = result.loras!.rules.rules
    expect(rules[0]).toEqual({ id: 'mid', enabled: true, lora: 'mmh3/mid.safetensors', start_segment: 3, segment_count: -1, strength: 0.5, stage: 'all' })
    expect(rules[1]).toMatchObject({ enabled: true, lora: 'mmh3/a.safetensors', start_segment: 1, segment_count: 2, strength: 1, stage: 'first' })
    expect(result.loras!.changed).toBe(true)
    expect(result.loras!.rows.map((row) => [row.change, row.lora, row.start, row.end])).toEqual([
      ['modified', 'mmh3/mid.safetensors', 3, null], ['added', 'mmh3/a.safetensors', 1, 2], ['removed', 'mmh3/old.safetensors', 1, null]])
  })

  it.each([
    [{ loras: [{ lora: 'mmh3/a.safetensors' }] }, undefined, 'loras_no_node'],
    [{ loras: [{ lora: 'mmh3/a.safetensors' }] }, { error: 'loras_shared' } as H3PlanLoraTarget, 'loras_shared'],
    [{ loras: {} }, target(), 'invalid_loras'],
    [{ loras: [{ lora: 'mmh3/gone.safetensors' }] }, target(), 'lora_missing'],
    [{ loras: [{ strength: 1 }] }, target(), 'invalid_lora'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', stage: 'third' }] }, target(), 'invalid_lora_stage'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', strength: 'big' }] }, target(), 'invalid_lora_strength'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', at: 's01', start_segment: 1 }] }, target(), 'lora_anchor_mixed'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', at: 's03', until: 's01' }] }, target(), 'lora_range'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', at: 's09' }] }, target(), 'unknown_anchor'],
    [{ loras: [{ lora: 'mmh3/a.safetensors', segment_count: 0 }] }, target(), 'invalid_lora_range'],
    [{ loras: [{ lora: '../a.safetensors' }] }, target(), 'invalid_lora_name'],
    [{ loras: [{ lora: 'C:/loras/a.safetensors' }] }, target(), 'invalid_lora_name'],
    [{ loras: [{ id: 'x', lora: 'mmh3/a.safetensors' }, { id: 'x', lora: 'mmh3/a.safetensors' }] }, target(), 'duplicate_lora_id'],
  ])('refuses %j', (plan, loras, code) => {
    const result = importWith(plan, loras)
    expect(result.issues.filter((item) => item.level === 'error').map((item) => item.code)).toEqual([code])
    expect(result.data).toBeNull()
  })

  it('writes rules to a bypassed or muted LoRA node, and says runs will not use them', () => {
    const inactive: H3PlanLoraTarget = { ...target(), inactive: true }
    const result = importWith({ loras: [{ lora: 'mmh3/a.safetensors', at: 's02' }] }, inactive)
    expect(result.loras).toMatchObject({ changed: true, rules: { rules: [{ lora: 'mmh3/a.safetensors', start_segment: 2 }] } })
    expect(result.issues).toContainEqual({ level: 'warning', code: 'loras_inactive' })
    // Disabled rules are not meant to run anyway.
    expect(importWith({ loras: [{ lora: 'mmh3/a.safetensors', enabled: false }] }, inactive).issues).toEqual([])
  })

  it('needs no LoRA node for an empty list', () => {
    const result = importWith({ loras: [] }, undefined)
    expect(result.issues).toEqual([])
    expect(result.loras).toBeUndefined()
  })

  it('moves rules with their tasks when the plan leaves loras out', () => {
    const loras = target(rule('all', 1, -1), rule('mid', 2, 2), rule('first', 1, 1))
    expect(importWith({}, loras).loras).toMatchObject({ changed: false })
    // s01 goes, a new task comes between s02 and s03.
    const result = importWith({ tasks: [{ id: 's02' }, { id: 'n1', seconds: 5.17 }, { id: 's03' }] }, loras)
    expect(result.data).not.toBeNull()
    expect(result.loras!.rules.rules.map((item) => [item.id, item.start_segment, item.segment_count])).toEqual([['all', 1, -1], ['mid', 1, 3]])
    expect(result.issues).toContainEqual({ level: 'warning', code: 'lora_rule_dropped', params: { lora: 'mmh3/first.safetensors' } })
    // The new task between s02 and s03 now falls inside the rule.
    expect(result.issues).toContainEqual({ level: 'warning', code: 'lora_rule_spread', params: { lora: 'mmh3/mid.safetensors' } })
    expect(result.loras!.rows.map((row) => row.change)).toEqual(['unchanged', 'modified', 'removed'])
  })

  it('keeps a rule over its tasks when the plan reorders them, and says what else it now covers', () => {
    // s01..s02, then the order s02, s03, s01: the rule spans s02 to s01, with s03 between.
    const result = importWith({ tasks: [{ id: 's02' }, { id: 's03' }, { id: 's01' }] }, target(rule('head', 1, 2)))
    expect(result.loras!.rules.rules.map((item) => [item.start_segment, item.segment_count])).toEqual([[1, 3]])
    expect(result.issues).toContainEqual({ level: 'warning', code: 'lora_rule_spread', params: { lora: 'mmh3/head.safetensors' } })
  })

  it('says when rules cannot follow the tasks they were on', () => {
    // A rule numbered inside a task split by a marker keeps its numbers.
    const split = { ...timeline(), task_markers: [{ id: 'm', frame: 300 }] }
    const kept = importWith({ tasks: [{ id: 's02' }, { id: 's01' }, { id: 's03' }] }, target(rule('part', 4, 1)), split)
    expect(kept.loras!.rules.rules[0]).toMatchObject({ start_segment: 4, segment_count: 1 })
    expect(kept.issues).toContainEqual({ level: 'warning', code: 'lora_rule_kept_numbers', params: { lora: 'mmh3/part.safetensors' } })
    // A node the plan cannot write keeps its numbers too.
    const shared = importWith({ tasks: [{ id: 's02' }, { id: 's01' }, { id: 's03' }] }, { error: 'loras_shared' })
    expect(shared.issues).toContainEqual({ level: 'warning', code: 'loras_not_followed' })
    expect(importWith({}, { error: 'loras_shared' }).issues).toEqual([])
  })
})
