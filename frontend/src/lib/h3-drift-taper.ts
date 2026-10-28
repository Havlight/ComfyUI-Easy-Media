import type {
  MultiTrackContinuityMode,
  MultiTrackSegmentContent,
  MultiTrackTaskMode,
} from '@/types/multitrack'

/**
 * Per-task seam taper for Drift continuity (mirrors utils/h3_drift_taper.py).
 *
 * Drift releases the copied context with the noise schedule: a context step with weight
 * `w` carries `w` times the next sigma's noise. The last `drift_taper_steps` steps beside
 * the seam fall from 1 to `drift_taper_floor`. Four steps to 0 is the original
 * Drift-Control and the default; 0 steps releases every step with the schedule.
 * `drift_taper_low_only` keeps the high-resolution stage on the plain schedule.
 *
 * Only values that differ from the defaults are stored, so untouched tasks keep the
 * original Drift graph and generation fingerprint.
 */
export const DRIFT_TAPER_CONTINUITY_MODE: MultiTrackContinuityMode = 'context_drift'
export const DRIFT_TAPER_DEFAULT_STEPS = 4
export const DRIFT_TAPER_MAX_STEPS = 12
export const DRIFT_TAPER_STEP_OPTIONS = [0, 1, 2, 3, 4, 6, 12] as const
export const DRIFT_TAPER_DEFAULT_FLOOR = 0
export const DRIFT_TAPER_FLOOR_OPTIONS = [0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7] as const

export type DriftTaperContent = Pick<
  MultiTrackSegmentContent,
  'drift_taper_steps' | 'drift_taper_floor' | 'drift_taper_low_only'
>

type Translate = (path: string, params?: Record<string, string | number>) => string

export interface DriftTaperEligibility {
  format?: string
  taskMode?: MultiTrackTaskMode
  continuityMode?: MultiTrackContinuityMode
  /** The task is not the first task, or a batch selection includes a later task. */
  hasContinuityTarget: boolean
}

/** A valid step count: an integer 0..12. */
export function normalizeDriftTaperSteps(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= DRIFT_TAPER_MAX_STEPS
    ? value
    : undefined
}

/** A valid floor weight: a finite number in [0, 1]. */
export function normalizeDriftTaperFloor(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? value
    : undefined
}

/** Effective step count; the default when the field is absent or invalid. */
export function getDriftTaperSteps(content: DriftTaperContent): number {
  return normalizeDriftTaperSteps(content.drift_taper_steps) ?? DRIFT_TAPER_DEFAULT_STEPS
}

/** Effective floor weight; the default when the field is absent or invalid. */
export function getDriftTaperFloor(content: DriftTaperContent): number {
  return normalizeDriftTaperFloor(content.drift_taper_floor) ?? DRIFT_TAPER_DEFAULT_FLOOR
}

export function getDriftTaperLowOnly(content: DriftTaperContent): boolean {
  return content.drift_taper_low_only === true
}

/** Taper settings apply to MiniMax Drift continuations that sample (not passthrough). */
export function isDriftTaperEligible({
  format,
  taskMode,
  continuityMode,
  hasContinuityTarget,
}: Readonly<DriftTaperEligibility>): boolean {
  return format === 'MiniMax'
    && taskMode !== 'passthrough'
    && continuityMode === DRIFT_TAPER_CONTINUITY_MODE
    && hasContinuityTarget
}

/**
 * Patch that removes every taper key. Patches are merged by spreading, so the keys are
 * set to undefined here and deleted by track data normalization on commit.
 */
export function clearDriftTaperPatch(): DriftTaperContent {
  return { drift_taper_steps: undefined, drift_taper_floor: undefined, drift_taper_low_only: undefined }
}

/** Patch for a step choice; the default or an invalid count removes the key. */
export function driftTaperStepsPatch(steps: unknown): DriftTaperContent {
  const normalized = normalizeDriftTaperSteps(steps)
  return {
    drift_taper_steps: normalized === undefined || normalized === DRIFT_TAPER_DEFAULT_STEPS ? undefined : normalized,
  }
}

/** Patch for a floor choice, or null when the value is not a number in [0, 1]. */
export function driftTaperFloorPatch(floor: unknown): DriftTaperContent | null {
  const normalized = normalizeDriftTaperFloor(floor)
  if (normalized === undefined) return null
  return { drift_taper_floor: normalized === DRIFT_TAPER_DEFAULT_FLOOR ? undefined : normalized }
}

export function driftTaperLowOnlyPatch(lowOnly: boolean): DriftTaperContent {
  return { drift_taper_low_only: lowOnly ? true : undefined }
}

/** Patch for a continuity mode change; leaving Drift also removes the taper settings. */
export function continuityModeChangePatch(
  continuityMode: MultiTrackContinuityMode,
): Pick<MultiTrackSegmentContent, 'continuity_mode'> & DriftTaperContent {
  return continuityMode === DRIFT_TAPER_CONTINUITY_MODE
    ? { continuity_mode: continuityMode }
    : { continuity_mode: continuityMode, ...clearDriftTaperPatch() }
}

/**
 * Clean taper fields during track data normalization, after the continuity mode is
 * normalized. Non-Drift tasks, invalid values and defaults are dropped.
 */
export function normalizeDriftTaperContent(content: MultiTrackSegmentContent): MultiTrackSegmentContent {
  const normalized: MultiTrackSegmentContent = { ...content }
  const isDrift = normalized.continuity_mode === DRIFT_TAPER_CONTINUITY_MODE
  const steps = normalizeDriftTaperSteps(normalized.drift_taper_steps)
  const floor = normalizeDriftTaperFloor(normalized.drift_taper_floor)
  if (!isDrift || steps === undefined || steps === DRIFT_TAPER_DEFAULT_STEPS) delete normalized.drift_taper_steps
  if (!isDrift || floor === undefined || floor === DRIFT_TAPER_DEFAULT_FLOOR) delete normalized.drift_taper_floor
  if (!isDrift || normalized.drift_taper_low_only !== true) delete normalized.drift_taper_low_only
  return normalized
}

/** Parse a Select item value (always a string) back to a number. */
export function parseDriftTaperOptionValue(value: string): number | undefined {
  if (value.trim() === '') return undefined
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : undefined
}

/** Choices with a valid stored value outside the presets kept as an extra sorted option. */
function withCurrentOption(presets: readonly number[], current: number, valid: boolean): number[] {
  const options = [...presets]
  if (!valid || options.includes(current)) return options
  return [...options, current].sort((left, right) => left - right)
}

export function driftTaperStepOptions(current: number): number[] {
  return withCurrentOption(DRIFT_TAPER_STEP_OPTIONS, current, normalizeDriftTaperSteps(current) !== undefined)
}

export function driftTaperFloorOptions(current: number): number[] {
  return withCurrentOption(DRIFT_TAPER_FLOOR_OPTIONS, current, normalizeDriftTaperFloor(current) !== undefined)
}

/** Compact weight text with at most three decimals, e.g. 0.5 -> "0.5", 0.25 -> "0.25". */
export function formatDriftTaperFloor(floor: number): string {
  return String(Math.round(floor * 1000) / 1000)
}

export function formatDriftTaperStepsLabel(t: Translate, steps: number): string {
  if (steps === 0) return t('multitrack.driftTaperStepsNone')
  if (steps === DRIFT_TAPER_MAX_STEPS) return t('multitrack.driftTaperStepsAll')
  return t(steps === DRIFT_TAPER_DEFAULT_STEPS ? 'multitrack.driftTaperStepsDefault' : 'multitrack.driftTaperStepsValue', { steps })
}

export function formatDriftTaperFloorLabel(t: Translate, floor: number): string {
  return t('multitrack.driftTaperFloorValue', { floor: formatDriftTaperFloor(floor) })
}
