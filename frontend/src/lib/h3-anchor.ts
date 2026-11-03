import type {
  MultiTrackAnchorPlacement,
  MultiTrackContinuityMode,
  MultiTrackSegmentContent,
  MultiTrackTaskMode,
} from '@/types/multitrack'

/**
 * Per-task settings of Anchor continuity (mirrors utils/h3_anchor.py), after
 * roycho87/clown_context_suite's Clown Keystone and Clown Late Lock: the chain's first
 * task (or the last task that restarted it) is the anchor. Its first single-frame latent
 * (keystone) is locked into each anchored task's first latent, and its last one
 * (capstone) is tacked onto the end of the video from the Late Lock step.
 *
 * Only values that differ from the defaults are stored, so untouched tasks keep the
 * default graph and generation fingerprint.
 */
export const ANCHOR_CONTINUITY_MODE: MultiTrackContinuityMode = 'context_anchor'
export const ANCHOR_PLACEMENTS: readonly MultiTrackAnchorPlacement[] = [
  'keystone_front_capstone_back', 'keystone_front', 'keystone_front_back', 'capstone_front_back',
]
export const ANCHOR_DEFAULT_PLACEMENT: MultiTrackAnchorPlacement = 'keystone_front_capstone_back'
export const ANCHOR_LATE_OPTIONS = [0, 0.25, 0.5, 0.75] as const
export const ANCHOR_DEFAULT_LATE = 0.5

export type AnchorContent = Pick<MultiTrackSegmentContent, 'anchor_placement' | 'anchor_late' | 'anchor_restart'>

export interface AnchorEligibility {
  format?: string
  taskMode?: MultiTrackTaskMode
  continuityMode?: MultiTrackContinuityMode
  /** The task is not the first task, or a batch selection includes a later task. */
  hasContinuityTarget: boolean
}

export function normalizeAnchorPlacement(value: unknown): MultiTrackAnchorPlacement | undefined {
  return (ANCHOR_PLACEMENTS as readonly unknown[]).includes(value) ? value as MultiTrackAnchorPlacement : undefined
}

export function normalizeAnchorLate(value: unknown): number | undefined {
  return typeof value === 'number' && (ANCHOR_LATE_OPTIONS as readonly number[]).includes(value) ? value : undefined
}

export function getAnchorPlacement(content: AnchorContent): MultiTrackAnchorPlacement {
  return normalizeAnchorPlacement(content.anchor_placement) ?? ANCHOR_DEFAULT_PLACEMENT
}

export function getAnchorLate(content: AnchorContent): number {
  return normalizeAnchorLate(content.anchor_late) ?? ANCHOR_DEFAULT_LATE
}

export function getAnchorRestart(content: AnchorContent): boolean {
  return content.anchor_restart === true
}

/** Whether the placement tacks a latent onto the back, which Late Lock times. */
export function anchorHasBack(placement: MultiTrackAnchorPlacement): boolean {
  return placement !== 'keystone_front'
}

/** Anchor settings apply to MiniMax Anchor continuations that sample (not passthrough). */
export function isAnchorEligible({ format, taskMode, continuityMode, hasContinuityTarget }: Readonly<AnchorEligibility>): boolean {
  return format === 'MiniMax' && taskMode !== 'passthrough' && continuityMode === ANCHOR_CONTINUITY_MODE && hasContinuityTarget
}

/** Whether any setting differs from the defaults. */
export function isAnchorCustomized(content: AnchorContent): boolean {
  return getAnchorPlacement(content) !== ANCHOR_DEFAULT_PLACEMENT || getAnchorLate(content) !== ANCHOR_DEFAULT_LATE
    || getAnchorRestart(content)
}

/**
 * Patch that removes every anchor key. Patches are merged by spreading, so the keys are
 * set to undefined here and deleted by track data normalization on commit.
 */
export function clearAnchorPatch(): AnchorContent {
  return { anchor_placement: undefined, anchor_late: undefined, anchor_restart: undefined }
}

export function anchorPlacementPatch(value: unknown): AnchorContent | null {
  const placement = normalizeAnchorPlacement(value)
  if (placement === undefined) return null
  return { anchor_placement: placement === ANCHOR_DEFAULT_PLACEMENT ? undefined : placement }
}

export function anchorLatePatch(value: unknown): AnchorContent | null {
  const late = normalizeAnchorLate(value)
  if (late === undefined) return null
  return { anchor_late: late === ANCHOR_DEFAULT_LATE ? undefined : late }
}

export function anchorRestartPatch(restart: boolean): AnchorContent {
  return { anchor_restart: restart ? true : undefined }
}

/** A split task's later parts continue its first part, which alone restarts the anchor. */
export function withoutAnchorRestart(content: MultiTrackSegmentContent): MultiTrackSegmentContent {
  if (content.anchor_restart === undefined) return content
  const rest = { ...content }
  delete rest.anchor_restart
  return rest
}

/**
 * Clean anchor fields during track data normalization, after the continuity mode is
 * normalized. Non-Anchor tasks, invalid values and defaults are dropped.
 */
export function normalizeAnchorContent(content: MultiTrackSegmentContent): MultiTrackSegmentContent {
  const normalized: MultiTrackSegmentContent = { ...content }
  const isAnchor = normalized.continuity_mode === ANCHOR_CONTINUITY_MODE
  const placement = normalizeAnchorPlacement(normalized.anchor_placement)
  const late = normalizeAnchorLate(normalized.anchor_late)
  if (!isAnchor || placement === undefined || placement === ANCHOR_DEFAULT_PLACEMENT) delete normalized.anchor_placement
  // Without a back frame the late lock has nothing to time.
  if (!isAnchor || late === undefined || late === ANCHOR_DEFAULT_LATE || !anchorHasBack(placement ?? ANCHOR_DEFAULT_PLACEMENT)) {
    delete normalized.anchor_late
  }
  if (!isAnchor || normalized.anchor_restart !== true) delete normalized.anchor_restart
  return normalized
}
