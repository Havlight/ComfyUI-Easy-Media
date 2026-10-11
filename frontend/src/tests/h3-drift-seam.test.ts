import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import zh from '../../messages/zh.json'
import {
  clearDriftSeamPatch,
  continuityModeChangePatch,
  DRIFT_SEAM_DEFAULT_NOISE,
  DRIFT_SEAM_NOISE_OPTIONS,
  DRIFT_SEAM_STEP_OPTIONS,
  driftSeamNoiseOptions,
  driftSeamNoisePatch,
  driftSeamStepsPatch,
  formatDriftSeamNoise,
  formatDriftSeamNoiseLabel,
  formatDriftSeamStepsLabel,
  getDriftSeamNoise,
  getDriftSeamSteps,
  isDriftSeamEligible,
  normalizeDriftSeamNoise,
  normalizeDriftSeamSteps,
  parseDriftSeamOptionValue,
} from '@/lib/h3-drift-seam'
import { translate } from '@/lib/i18n'

const SEAM_MESSAGE_KEYS = [
  'driftSeamSteps',
  'driftSeamStepsFree',
  'driftSeamStepsValue',
  'driftSeamStepsTooltip',
  'driftSeamNoise',
  'driftSeamNoiseValue',
  'driftSeamNoiseTooltip',
] as const

describe('Matched Drift seam helpers', () => {
  it('offers seam steps 0..4 and noise caps 0..0.7 with a 0.5 default', () => {
    expect([...DRIFT_SEAM_STEP_OPTIONS]).toEqual([0, 1, 2, 3, 4])
    expect([...DRIFT_SEAM_NOISE_OPTIONS]).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
    expect(DRIFT_SEAM_DEFAULT_NOISE).toBe(0.5)
  })

  it('keeps only whole seam step counts from 1 to 4', () => {
    expect([1, 2, 3, 4].map(normalizeDriftSeamSteps)).toEqual([1, 2, 3, 4])
    for (const value of [0, 5, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, '2', true, null, undefined]) {
      expect(normalizeDriftSeamSteps(value)).toBeUndefined()
    }
  })

  it('keeps only finite noise caps between 0 and 1', () => {
    expect([0, 0.25, 0.5, 1].map(normalizeDriftSeamNoise)).toEqual([0, 0.25, 0.5, 1])
    for (const value of [-0.1, 1.1, Number.NaN, Number.POSITIVE_INFINITY, '0.5', false, null, undefined]) {
      expect(normalizeDriftSeamNoise(value)).toBeUndefined()
    }
  })

  it('reads effective values with off and backend-default fallbacks', () => {
    expect(getDriftSeamSteps({})).toBe(0)
    expect(getDriftSeamSteps({ drift_seam_steps: 3 })).toBe(3)
    expect(getDriftSeamSteps({ drift_seam_steps: 9 })).toBe(0)
    expect(getDriftSeamNoise({})).toBe(0.5)
    expect(getDriftSeamNoise({ drift_seam_noise: 0.2 })).toBe(0.2)
    expect(getDriftSeamNoise({ drift_seam_noise: 2 })).toBe(0.5)
  })

  it('applies only to sampled MiniMax Matched Drift continuations', () => {
    const eligible = {
      format: 'MiniMax',
      taskMode: 'default' as const,
      continuityMode: 'context_drift_matched' as const,
      hasContinuityTarget: true,
    }
    expect(isDriftSeamEligible(eligible)).toBe(true)
    expect(isDriftSeamEligible({ ...eligible, taskMode: undefined })).toBe(true)
    expect(isDriftSeamEligible({ ...eligible, format: 'Seedance' })).toBe(false)
    expect(isDriftSeamEligible({ ...eligible, format: undefined })).toBe(false)
    expect(isDriftSeamEligible({ ...eligible, taskMode: 'passthrough' })).toBe(false)
    expect(isDriftSeamEligible({ ...eligible, continuityMode: 'context_drift' })).toBe(false)
    expect(isDriftSeamEligible({ ...eligible, continuityMode: undefined })).toBe(false)
    expect(isDriftSeamEligible({ ...eligible, hasContinuityTarget: false })).toBe(false)
  })

  it('writes whole step counts and clears both keys when the seam is turned off', () => {
    expect(driftSeamStepsPatch(2)).toEqual({ drift_seam_steps: 2 })
    for (const value of [0, 5, 1.5, undefined]) {
      const patch = driftSeamStepsPatch(value)
      expect(patch).toStrictEqual({ drift_seam_steps: undefined, drift_seam_noise: undefined })
      expect(Object.keys(patch).sort()).toEqual(['drift_seam_noise', 'drift_seam_steps'])
    }
    expect(clearDriftSeamPatch()).toStrictEqual({ drift_seam_steps: undefined, drift_seam_noise: undefined })
  })

  it('writes valid noise caps and rejects others', () => {
    expect(driftSeamNoisePatch(0)).toStrictEqual({ drift_seam_noise: 0 })
    expect(driftSeamNoisePatch(0.3)).toStrictEqual({ drift_seam_noise: 0.3 })
    expect(driftSeamNoisePatch(1.5)).toBeNull()
    expect(driftSeamNoisePatch(undefined)).toBeNull()
  })

  it('clears seam keys whenever continuity leaves Matched Drift', () => {
    expect(continuityModeChangePatch('context_drift_matched')).toStrictEqual({ continuity_mode: 'context_drift_matched' })
    for (const mode of ['shot', 'context', 'context_drift'] as const) {
      expect(continuityModeChangePatch(mode)).toStrictEqual({
        continuity_mode: mode,
        drift_seam_steps: undefined,
        drift_seam_noise: undefined,
      })
    }
  })

  it('parses Select values back to numbers', () => {
    expect(parseDriftSeamOptionValue('0')).toBe(0)
    expect(parseDriftSeamOptionValue('3')).toBe(3)
    expect(parseDriftSeamOptionValue('0.7')).toBe(0.7)
    expect(parseDriftSeamOptionValue(String(0.123456789))).toBe(0.123456789)
    expect(parseDriftSeamOptionValue('')).toBeUndefined()
    expect(parseDriftSeamOptionValue('abc')).toBeUndefined()
  })

  it('keeps an off-preset stored noise cap as an extra sorted option', () => {
    expect(driftSeamNoiseOptions(0.5)).toEqual([...DRIFT_SEAM_NOISE_OPTIONS])
    expect(driftSeamNoiseOptions(0.25)).toEqual([0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7])
    expect(driftSeamNoiseOptions(0.9)).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.9])
    expect(driftSeamNoiseOptions(3)).toEqual([...DRIFT_SEAM_NOISE_OPTIONS])
  })

  it('formats compact seam labels in both locales', () => {
    const tEn = (path: string, params?: Record<string, string | number>) => translate('en', path, params)
    const tZh = (path: string, params?: Record<string, string | number>) => translate('zh', path, params)
    expect(formatDriftSeamNoise(0)).toBe('0')
    expect(formatDriftSeamNoise(0.5)).toBe('0.5')
    expect(formatDriftSeamNoise(0.1234)).toBe('0.123')
    expect(formatDriftSeamStepsLabel(tEn, 0)).toBe('Seam: free')
    expect(formatDriftSeamStepsLabel(tEn, 2)).toBe('Seam 2')
    expect(formatDriftSeamStepsLabel(tZh, 0)).toBe('接缝：不保留')
    expect(formatDriftSeamStepsLabel(tZh, 2)).toBe('接缝 2 步')
    expect(formatDriftSeamNoiseLabel(tEn, 0.5)).toBe('Cap 0.5')
    expect(formatDriftSeamNoiseLabel(tZh, 0)).toBe('上限 0')
  })
})

describe('Matched Drift seam messages', () => {
  it('defines every seam key in both locales', () => {
    const enMultitrack = en.multitrack as Record<string, string>
    const zhMultitrack = zh.multitrack as Record<string, string>
    for (const key of SEAM_MESSAGE_KEYS) {
      expect(enMultitrack[key], `en multitrack.${key}`).toEqual(expect.any(String))
      expect(zhMultitrack[key], `zh multitrack.${key}`).toEqual(expect.any(String))
      expect(zhMultitrack[key]).not.toBe(enMultitrack[key])
    }
    expect(enMultitrack.driftSeamStepsValue).toContain('{steps}')
    expect(zhMultitrack.driftSeamStepsValue).toContain('{steps}')
    expect(enMultitrack.driftSeamNoiseValue).toContain('{noise}')
    expect(zhMultitrack.driftSeamNoiseValue).toContain('{noise}')
  })

  it('explains the seam trade-offs in the tooltips', () => {
    expect(translate('en', 'multitrack.driftSeamStepsTooltip')).toContain('Matched Drift only')
    expect(translate('en', 'multitrack.driftSeamNoiseTooltip')).toContain('0.4–0.6')
    expect(translate('zh', 'multitrack.driftSeamStepsTooltip')).toContain('仅全程漂移控制')
    expect(translate('zh', 'multitrack.driftSeamNoiseTooltip')).toContain('0.4–0.6')
  })
})
