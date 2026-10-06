export interface SegmentLoraRule {
  id: string
  enabled: boolean
  lora: string
  start_segment: number
  segment_count: number
  strength: number
  stage: 'all' | 'first' | 'second'
}
export interface SegmentLoraPlan { version: 1; rules: SegmentLoraRule[] }
export const EMPTY_LORA_PLAN: SegmentLoraPlan = { version: 1, rules: [] }
export interface LoraProjectSnapshot {
  known: boolean
  segment_loras: SegmentLoraPlan
  recipe: { sampling_mode: string; first_pass_only: boolean }
  start_segment: number
  segment_count: number
}
export interface LoraTaskSummary {
  segment_id: string
  number: number
  index: number
  selected: boolean
  stages: Partial<Record<'first' | 'second', (SegmentLoraRule & { row: number })[]>>
}
export interface LoraSummary {
  pending?: boolean
  warnings?: { stage: string; lora: string; tasks: number[] }[]
  tasks: LoraTaskSummary[]
  rules: { id: string; row: number; start: number; end: number; unused_reason: string | null; incomplete: boolean }[]
  conflicts: { index: number; stage: string; rows: number[]; lora: string }[]
}
