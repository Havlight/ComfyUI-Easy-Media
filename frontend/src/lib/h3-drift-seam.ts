import type {
  MultiTrackContinuityMode,
  MultiTrackSegmentContent,
  MultiTrackTaskMode,
} from '@/types/multitrack'

/**
 * Per-task seam settings for Matched Drift continuity.
 *
 * Matched Drift releases every copied context step with the noise schedule. The last
 * `drift_seam_steps` steps beside the seam can instead keep their noise capped at
 * `drift_seam_noise`, which pins pose and motion early without showing fine detail.
 */
export const DRIFT_SEAM_CONTINUITY_MODE: MultiTrackContinuityMode = 'context_drift_matched'
export const DRIFT_SEAM_MAX_STEPS = 4
export const DRIFT_SEAM_STEP_OPTIONS = [0, 1, 2, 3, 4] as const
export const DRIFT_SEAM_DEFAULT_NOISE = 0.5
export const DRIFT_SEAM_NOISE_OPTIONS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7] as const

export type DriftSeamContent = Pick<MultiTrackSegmentContent, 'drift_seam_steps' | 'drift_seam_noise'>

type Translate = (path: string, params?: Record<string, string | number>) => string

export interface DriftSeamEligibility {
  format?: string
  taskMode?: MultiTrackTaskMode
  continuityMode?: MultiTrackContinuityMode
  /** The task is not the first task, or a batch selection includes a later task. */
  hasContinuityTarget: boolean
}

/** A stored step count worth keeping: an integer 1..4. Zero means off and is never stored. */
export function normalizeDriftSeamSteps(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= DRIFT_SEAM_MAX_STEPS
    ? value
    : undefined
}

/** A stored noise cap worth keeping: a finite number in [0, 1]. */
export function normalizeDriftSeamNoise(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? value
    : undefined
}

/** Effective seam step count; 0 when the seam is off. */
export function getDriftSeamSteps(content: DriftSeamContent): number {
  return normalizeDriftSeamSteps(content.drift_seam_steps) ?? 0
}

/** Effective noise cap; the backend default when the field is absent or invalid. */
export function getDriftSeamNoise(content: DriftSeamContent): number {
  return normalizeDriftSeamNoise(content.drift_seam_noise) ?? DRIFT_SEAM_DEFAULT_NOISE
}

/** Seam settings apply to MiniMax Matched Drift continuations that sample (not passthrough). */
export function isDriftSeamEligible({
  format,
  taskMode,
  continuityMode,
  hasContinuityTarget,
}: Readonly<DriftSeamEligibility>): boolean {
  return format === 'MiniMax'
    && taskMode !== 'passthrough'
    && continuityMode === DRIFT_SEAM_CONTINUITY_MODE
    && hasContinuityTarget
}

/**
 * Patch that removes both seam keys. Patches are merged by spreading, so the keys are
 * set to undefined here and deleted by track data normalization on commit.
 */
export function clearDriftSeamPatch(): DriftSeamContent {
  return { drift_seam_steps: undefined, drift_seam_noise: undefined }
}

/** Patch for a seam step choice; 0 or an invalid count turns the seam off and removes both keys. */
export function driftSeamStepsPatch(steps: unknown): DriftSeamContent {
  const normalized = normalizeDriftSeamSteps(steps)
  return normalized === undefined ? clearDriftSeamPatch() : { drift_seam_steps: normalized }
}

/** Patch for a noise cap choice, or null when the value is not a number in [0, 1]. */
export function driftSeamNoisePatch(noise: unknown): DriftSeamContent | null {
  const normalized = normalizeDriftSeamNoise(noise)
  return normalized === undefined ? null : { drift_seam_noise: normalized }
}

/** Patch for a continuity mode change; leaving Matched Drift also removes the seam settings. */
export function continuityModeChangePatch(
  continuityMode: MultiTrackContinuityMode,
): Pick<MultiTrackSegmentContent, 'continuity_mode'> & DriftSeamContent {
  return continuityMode === DRIFT_SEAM_CONTINUITY_MODE
    ? { continuity_mode: continuityMode }
    : { continuity_mode: continuityMode, ...clearDriftSeamPatch() }
}

/** Parse a Select item value (always a string) back to a number. */
export function parseDriftSeamOptionValue(value: string): number | undefined {
  if (value.trim() === '') return undefined
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : undefined
}

/** Noise cap choices; a valid stored value outside the presets is kept as an extra sorted option. */
export function driftSeamNoiseOptions(current: number): number[] {
  const options: number[] = [...DRIFT_SEAM_NOISE_OPTIONS]
  if (normalizeDriftSeamNoise(current) === undefined || options.includes(current)) return options
  return [...options, current].sort((left, right) => left - right)
}

/** Compact noise text with at most three decimals, e.g. 0.5 -> "0.5", 0.25 -> "0.25". */
export function formatDriftSeamNoise(noise: number): string {
  return String(Math.round(noise * 1000) / 1000)
}

export function formatDriftSeamStepsLabel(t: Translate, steps: number): string {
  return steps > 0
    ? t('multitrack.driftSeamStepsValue', { steps })
    : t('multitrack.driftSeamStepsFree')
}

export function formatDriftSeamNoiseLabel(t: Translate, noise: number): string {
  return t('multitrack.driftSeamNoiseValue', { noise: formatDriftSeamNoise(noise) })
}
