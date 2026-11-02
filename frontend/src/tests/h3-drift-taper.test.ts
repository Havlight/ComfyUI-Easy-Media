import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import zh from '../../messages/zh.json'
import {
  clearDriftTaperPatch,
  continuityModeChangePatch,
  DRIFT_TAPER_DEFAULT_FLOOR,
  DRIFT_TAPER_DEFAULT_STEPS,
  DRIFT_TAPER_FLOOR_OPTIONS,
  DRIFT_TAPER_STEP_OPTIONS,
  driftTaperFloorOptions,
  driftTaperFloorPatch,
  driftTaperLowOnlyPatch,
  driftTaperStepOptions,
  driftTaperStepsPatch,
  formatDriftTaperFloor,
  formatDriftTaperFloorLabel,
  formatDriftTaperStepsLabel,
  getDriftTaperFloor,
  getDriftTaperLowOnly,
  getDriftTaperSteps,
  isDriftTaperEligible,
  normalizeDriftTaperContent,
  normalizeDriftTaperFloor,
  normalizeDriftTaperSteps,
  parseDriftTaperOptionValue,
} from '@/lib/h3-drift-taper'
import { translate } from '@/lib/i18n'
import type { MultiTrackSegmentContent } from '@/types/multitrack'

const TAPER_MESSAGE_KEYS = [
  'driftTaperSteps',
  'driftTaperStepsNone',
  'driftTaperStepsDefault',
  'driftTaperStepsValue',
  'driftTaperStepsAll',
  'driftTaperStepsTooltip',
  'driftTaperFloor',
  'driftTaperFloorValue',
  'driftTaperFloorTooltip',
  'driftTaperStage',
  'driftTaperStageAll',
  'driftTaperStageLow',
  'driftTaperStageTooltip',
] as const

describe('Drift taper helpers', () => {
  it('offers taper steps up to all 12 and floors up to 0.7, defaulting to Drift-Control', () => {
    expect([...DRIFT_TAPER_STEP_OPTIONS]).toEqual([0, 1, 2, 3, 4, 6, 12])
    expect([...DRIFT_TAPER_FLOOR_OPTIONS]).toEqual([0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7])
    expect(DRIFT_TAPER_DEFAULT_STEPS).toBe(4)
    expect(DRIFT_TAPER_DEFAULT_FLOOR).toBe(0)
  })

  it('keeps only whole step counts from 0 to 12', () => {
    expect([0, 1, 4, 12].map(normalizeDriftTaperSteps)).toEqual([0, 1, 4, 12])
    for (const value of [13, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, '2', true, null, undefined]) {
      expect(normalizeDriftTaperSteps(value)).toBeUndefined()
    }
  })

  it('keeps only finite floors between 0 and 1', () => {
    expect([0, 0.25, 0.5, 1].map(normalizeDriftTaperFloor)).toEqual([0, 0.25, 0.5, 1])
    for (const value of [-0.1, 1.1, Number.NaN, Number.POSITIVE_INFINITY, '0.5', false, null, undefined]) {
      expect(normalizeDriftTaperFloor(value)).toBeUndefined()
    }
  })

  it('reads effective values with Drift-Control fallbacks', () => {
    expect(getDriftTaperSteps({})).toBe(4)
    expect(getDriftTaperSteps({ drift_taper_steps: 0 })).toBe(0)
    expect(getDriftTaperSteps({ drift_taper_steps: 99 })).toBe(4)
    expect(getDriftTaperFloor({})).toBe(0)
    expect(getDriftTaperFloor({ drift_taper_floor: 0.25 })).toBe(0.25)
    expect(getDriftTaperFloor({ drift_taper_floor: 2 })).toBe(0)
    expect(getDriftTaperLowOnly({})).toBe(false)
    expect(getDriftTaperLowOnly({ drift_taper_low_only: true })).toBe(true)
  })

  it('applies only to sampled MiniMax Drift continuations', () => {
    const eligible = {
      format: 'MiniMax',
      taskMode: 'default' as const,
      continuityMode: 'context_drift' as const,
      hasContinuityTarget: true,
    }
    expect(isDriftTaperEligible(eligible)).toBe(true)
    expect(isDriftTaperEligible({ ...eligible, taskMode: undefined })).toBe(true)
    expect(isDriftTaperEligible({ ...eligible, format: 'Seedance' })).toBe(false)
    expect(isDriftTaperEligible({ ...eligible, taskMode: 'passthrough' })).toBe(false)
    expect(isDriftTaperEligible({ ...eligible, continuityMode: 'context' })).toBe(false)
    expect(isDriftTaperEligible({ ...eligible, continuityMode: undefined })).toBe(false)
    expect(isDriftTaperEligible({ ...eligible, hasContinuityTarget: false })).toBe(false)
  })

  it('stores only values that differ from the defaults', () => {
    expect(driftTaperStepsPatch(3)).toStrictEqual({ drift_taper_steps: 3 })
    expect(driftTaperStepsPatch(0)).toStrictEqual({ drift_taper_steps: 0 })
    for (const value of [4, 13, 1.5, undefined]) {
      expect(driftTaperStepsPatch(value)).toStrictEqual({ drift_taper_steps: undefined })
    }
    expect(driftTaperFloorPatch(0.25)).toStrictEqual({ drift_taper_floor: 0.25 })
    expect(driftTaperFloorPatch(0)).toStrictEqual({ drift_taper_floor: undefined })
    expect(driftTaperFloorPatch(1.5)).toBeNull()
    expect(driftTaperFloorPatch(undefined)).toBeNull()
    expect(driftTaperLowOnlyPatch(true)).toStrictEqual({ drift_taper_low_only: true })
    expect(driftTaperLowOnlyPatch(false)).toStrictEqual({ drift_taper_low_only: undefined })
    expect(clearDriftTaperPatch()).toStrictEqual({
      drift_taper_steps: undefined,
      drift_taper_floor: undefined,
      drift_taper_low_only: undefined,
    })
  })

  it('clears taper keys whenever continuity leaves Drift', () => {
    expect(continuityModeChangePatch('context_drift')).toStrictEqual({ continuity_mode: 'context_drift' })
    for (const mode of ['shot', 'context'] as const) {
      expect(continuityModeChangePatch(mode)).toStrictEqual({ continuity_mode: mode, ...clearDriftTaperPatch() })
    }
  })

  it('normalizes stored content: drops defaults, invalid values, and non-Drift taper keys', () => {
    const drift: MultiTrackSegmentContent = { media_type: 'none', continuity_mode: 'context_drift' }
    expect(normalizeDriftTaperContent({
      ...drift, drift_taper_steps: 3, drift_taper_floor: 0.25, drift_taper_low_only: true,
    })).toStrictEqual({ ...drift, drift_taper_steps: 3, drift_taper_floor: 0.25, drift_taper_low_only: true })
    expect(normalizeDriftTaperContent({
      ...drift, drift_taper_steps: 4, drift_taper_floor: 0, drift_taper_low_only: false,
    })).toStrictEqual(drift)
    expect(normalizeDriftTaperContent({ ...drift, drift_taper_steps: 99, drift_taper_floor: 3 }))
      .toStrictEqual(drift)
    expect(normalizeDriftTaperContent({
      ...drift, continuity_mode: 'shot', drift_taper_steps: 3, drift_taper_floor: 0.25, drift_taper_low_only: true,
    })).toStrictEqual({ ...drift, continuity_mode: 'shot' })
  })

  it('parses Select values back to numbers', () => {
    expect(parseDriftTaperOptionValue('0')).toBe(0)
    expect(parseDriftTaperOptionValue('12')).toBe(12)
    expect(parseDriftTaperOptionValue('0.25')).toBe(0.25)
    expect(parseDriftTaperOptionValue('')).toBeUndefined()
    expect(parseDriftTaperOptionValue('abc')).toBeUndefined()
  })

  it('keeps an off-preset stored value as an extra sorted option', () => {
    expect(driftTaperStepOptions(4)).toEqual([...DRIFT_TAPER_STEP_OPTIONS])
    expect(driftTaperStepOptions(5)).toEqual([0, 1, 2, 3, 4, 5, 6, 12])
    expect(driftTaperFloorOptions(0.25)).toEqual([...DRIFT_TAPER_FLOOR_OPTIONS])
    expect(driftTaperFloorOptions(0.9)).toEqual([...DRIFT_TAPER_FLOOR_OPTIONS, 0.9])
    expect(driftTaperFloorOptions(3)).toEqual([...DRIFT_TAPER_FLOOR_OPTIONS])
  })

  it('formats compact taper labels in both locales', () => {
    const tEn = (path: string, params?: Record<string, string | number>) => translate('en', path, params)
    const tZh = (path: string, params?: Record<string, string | number>) => translate('zh', path, params)
    expect(formatDriftTaperFloor(0)).toBe('0')
    expect(formatDriftTaperFloor(0.25)).toBe('0.25')
    expect(formatDriftTaperFloor(0.1234)).toBe('0.123')
    expect(formatDriftTaperStepsLabel(tEn, 0)).toBe('Taper: none')
    expect(formatDriftTaperStepsLabel(tEn, 3)).toBe('Taper 3')
    expect(formatDriftTaperStepsLabel(tEn, 4)).toBe('Taper 4 (default)')
    expect(formatDriftTaperStepsLabel(tEn, 12)).toBe('Taper all')
    expect(formatDriftTaperStepsLabel(tZh, 0)).toBe('不渐变(全程)')
    expect(formatDriftTaperStepsLabel(tZh, 3)).toBe('渐变 3 步')
    expect(formatDriftTaperFloorLabel(tEn, 0.25)).toBe('Floor 0.25')
    expect(formatDriftTaperFloorLabel(tZh, 0)).toBe('权重 0')
  })
})

describe('Drift taper messages', () => {
  it('defines every taper key in both locales', () => {
    const enMultitrack = en.multitrack as Record<string, string>
    const zhMultitrack = zh.multitrack as Record<string, string>
    for (const key of TAPER_MESSAGE_KEYS) {
      expect(enMultitrack[key], `en multitrack.${key}`).toEqual(expect.any(String))
      expect(zhMultitrack[key], `zh multitrack.${key}`).toEqual(expect.any(String))
      expect(zhMultitrack[key]).not.toBe(enMultitrack[key])
    }
    for (const locale of [enMultitrack, zhMultitrack]) {
      expect(locale.driftTaperStepsValue).toContain('{steps}')
      expect(locale.driftTaperStepsDefault).toContain('{steps}')
      expect(locale.driftTaperFloorValue).toContain('{floor}')
    }
  })
})
