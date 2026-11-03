import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import zh from '../../messages/zh.json'
import {
  ANCHOR_LATE_OPTIONS,
  ANCHOR_NOISE_OPTIONS,
  ANCHOR_PLACEMENTS,
  anchorHasBack,
  anchorLatePatch,
  anchorNoisePatch,
  anchorPlacementPatch,
  anchorRestartPatch,
  getAnchorLate,
  getAnchorNoise,
  getAnchorPlacement,
  isAnchorCustomized,
  isAnchorEligible,
  normalizeAnchorContent,
} from '@/lib/h3-anchor'
import { translate } from '@/lib/i18n'
import type { MultiTrackSegmentContent } from '@/types/multitrack'

describe('Anchor continuity settings', () => {
  it('defaults to keystone front + capstone back, the back frame after half the steps and context noise 0.8', () => {
    expect(getAnchorPlacement({})).toBe('keystone_front_capstone_back')
    expect(getAnchorLate({})).toBe(0.5)
    expect(getAnchorLate({ anchor_late: 0.3 })).toBe(0.5)
    expect(getAnchorNoise({})).toBe(0.8)
    expect(getAnchorNoise({ anchor_noise: 0 })).toBe(0)
    expect(getAnchorNoise({ anchor_noise: 0.5 })).toBe(0.8)
    expect(isAnchorCustomized({})).toBe(false)
    expect(isAnchorCustomized({ anchor_restart: true })).toBe(true)
    expect(isAnchorCustomized({ anchor_noise: 1 })).toBe(true)
    expect(anchorHasBack('keystone_front')).toBe(false)
    expect(anchorHasBack('capstone_front_back')).toBe(true)
  })

  it('stores only values that differ from the defaults', () => {
    expect(anchorPlacementPatch('keystone_front')).toStrictEqual({ anchor_placement: 'keystone_front' })
    expect(anchorPlacementPatch('keystone_front_capstone_back')).toStrictEqual({ anchor_placement: undefined })
    expect(anchorPlacementPatch('middle')).toBeNull()
    expect(anchorLatePatch(0)).toStrictEqual({ anchor_late: 0 })
    expect(anchorLatePatch(0.5)).toStrictEqual({ anchor_late: undefined })
    expect(anchorLatePatch(0.3)).toBeNull()
    expect(anchorNoisePatch(0)).toStrictEqual({ anchor_noise: 0 })
    expect(anchorNoisePatch(0.8)).toStrictEqual({ anchor_noise: undefined })
    expect(anchorNoisePatch(0.5)).toBeNull()
    expect(anchorRestartPatch(false)).toStrictEqual({ anchor_restart: undefined })
  })

  it('applies to MiniMax Anchor continuations that sample', () => {
    const base = { format: 'MiniMax', taskMode: 'default' as const, continuityMode: 'context_anchor' as const, hasContinuityTarget: true }
    expect(isAnchorEligible(base)).toBe(true)
    expect(isAnchorEligible({ ...base, continuityMode: 'context_drift' })).toBe(false)
    expect(isAnchorEligible({ ...base, taskMode: 'passthrough' })).toBe(false)
    expect(isAnchorEligible({ ...base, hasContinuityTarget: false })).toBe(false)
    expect(isAnchorEligible({ ...base, format: 'Seedance' })).toBe(false)
  })

  it('drops defaults, invalid values and settings of other continuity modes', () => {
    const anchor: MultiTrackSegmentContent = { media_type: 'none', continuity_mode: 'context_anchor' }
    expect(normalizeAnchorContent({ ...anchor, anchor_placement: 'capstone_front_back', anchor_late: 0.25, anchor_noise: 0, anchor_restart: true }))
      .toStrictEqual({ ...anchor, anchor_placement: 'capstone_front_back', anchor_late: 0.25, anchor_noise: 0, anchor_restart: true })
    expect(normalizeAnchorContent({ ...anchor, anchor_placement: 'keystone_front_capstone_back', anchor_late: 0.5, anchor_noise: 0.8, anchor_restart: false }))
      .toStrictEqual(anchor)
    expect(normalizeAnchorContent({ ...anchor, anchor_noise: 0.5 })).toStrictEqual(anchor)
    expect(normalizeAnchorContent({ ...anchor, continuity_mode: 'context', anchor_late: 0.25 }))
      .toStrictEqual({ ...anchor, continuity_mode: 'context' })
    // Without a back frame the late lock times nothing.
    expect(normalizeAnchorContent({ ...anchor, anchor_placement: 'keystone_front', anchor_late: 0 }))
      .toStrictEqual({ ...anchor, anchor_placement: 'keystone_front' })
  })

  it('labels every option in both locales', () => {
    const keys = [
      'anchorSettings', 'anchorPlacement', 'anchorLate', 'anchorNoise', 'anchorRestart', 'continuitySettingsReset',
      ...ANCHOR_PLACEMENTS.map((placement) => `anchorPlacement_${placement}`),
      ...ANCHOR_LATE_OPTIONS.map((late) => `anchorLate${Math.round(late * 100)}`),
      ...ANCHOR_NOISE_OPTIONS.map((noise) => `anchorNoise${Math.round(noise * 100)}`),
    ]
    for (const locale of ['en', 'zh']) {
      for (const key of keys) expect(translate(locale, `multitrack.${key}`)).not.toBe(`multitrack.${key}`)
    }
    expect(Object.keys(en.multitrack).filter((key) => key.startsWith('anchor')).sort())
      .toEqual(Object.keys(zh.multitrack).filter((key) => key.startsWith('anchor')).sort())
  })
})
