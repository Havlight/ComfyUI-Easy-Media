import { useId, type ReactNode } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  ANCHOR_CONTINUITY_MODE,
  ANCHOR_LATE_OPTIONS,
  ANCHOR_NOISE_OPTIONS,
  ANCHOR_PLACEMENTS,
  anchorHasBack,
  anchorLatePatch,
  anchorNoisePatch,
  anchorPlacementPatch,
  anchorRestartPatch,
  clearAnchorPatch,
  getAnchorLate,
  getAnchorNoise,
  getAnchorPlacement,
  getAnchorRestart,
  isAnchorCustomized,
} from '@/lib/h3-anchor'
import {
  DRIFT_TAPER_CONTINUITY_MODE,
  clearDriftTaperPatch,
  driftTaperFloorOptions,
  driftTaperFloorPatch,
  driftTaperLowOnlyPatch,
  driftTaperStepOptions,
  driftTaperStepsPatch,
  formatDriftTaperFloorLabel,
  formatDriftTaperStepsLabel,
  getDriftTaperFloor,
  getDriftTaperLowOnly,
  getDriftTaperSteps,
  isDriftTaperCustomized,
  parseDriftTaperOptionValue,
} from '@/lib/h3-drift-taper'
import { useT } from '@/lib/i18n'
import type { MultiTrackContinuityMode, MultiTrackSegmentContent } from '@/types/multitrack'

interface ContinuitySettingsProps {
  continuityMode: MultiTrackContinuityMode
  content: MultiTrackSegmentContent
  /** Applies a content patch to the task, or to every selected task. */
  onChange: (patch: Partial<MultiTrackSegmentContent>) => void
  /** Applies a patch to this task alone: a restart means something on one task only. */
  onTaskChange?: (patch: Partial<MultiTrackSegmentContent>) => void
  /** Whether this task or any other selected task differs from the defaults. */
  customized?: boolean
}

/** Anchor late lock shares as message keys: 0.25 -> anchorLate25. */
function anchorLateKey(late: number) {
  return `multitrack.anchorLate${Math.round(late * 100)}`
}

/** Context Noise strengths as message keys: 0.8 -> anchorNoise80. */
function anchorNoiseKey(noise: number) {
  return `multitrack.anchorNoise${Math.round(noise * 100)}`
}

/** A labelled setting; its control gets the help text's id for aria-describedby. */
function Setting({ label, help, children }: Readonly<{ label: string; help: string; children: (helpId: string) => ReactNode }>) {
  const helpId = useId()
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium text-foreground">{label}</div>
      {children(helpId)}
      <p id={helpId} className="text-[10px] leading-snug text-muted-foreground">{help}</p>
    </div>
  )
}

function DriftSettings({ content, onChange }: Readonly<Omit<ContinuitySettingsProps, 'continuityMode'>>) {
  const t = useT()
  const steps = getDriftTaperSteps(content)
  return (
    <>
      <Setting label={t('multitrack.driftTaperSteps')} help={t('multitrack.driftTaperStepsTooltip')}>
        {(helpId) => <Select value={String(steps)} onValueChange={(value) => {
          const parsed = parseDriftTaperOptionValue(value)
          if (parsed !== undefined) onChange(driftTaperStepsPatch(parsed))
        }}>
          <SelectTrigger aria-label={t('multitrack.driftTaperSteps')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {driftTaperStepOptions(steps).map((option) => (
              <SelectItem key={option} value={String(option)}>
                <span className="text-[11px]">{formatDriftTaperStepsLabel(t, option)}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>}
      </Setting>
      {steps > 0 && (
        <Setting label={t('multitrack.driftTaperFloor')} help={t('multitrack.driftTaperFloorTooltip')}>
          {(helpId) => <Select value={String(getDriftTaperFloor(content))} onValueChange={(value) => {
            const patch = driftTaperFloorPatch(parseDriftTaperOptionValue(value))
            if (patch) onChange(patch)
          }}>
            <SelectTrigger aria-label={t('multitrack.driftTaperFloor')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {driftTaperFloorOptions(getDriftTaperFloor(content)).map((option) => (
                <SelectItem key={option} value={String(option)}>
                  <span className="text-[11px]">{formatDriftTaperFloorLabel(t, option)}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>}
        </Setting>
      )}
      {steps > 0 && (
        <Setting label={t('multitrack.driftTaperStage')} help={t('multitrack.driftTaperStageTooltip')}>
          {(helpId) => <Select value={getDriftTaperLowOnly(content) ? 'low' : 'all'}
            onValueChange={(value) => onChange(driftTaperLowOnlyPatch(value === 'low'))}>
            <SelectTrigger aria-label={t('multitrack.driftTaperStage')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all"><span className="text-[11px]">{t('multitrack.driftTaperStageAll')}</span></SelectItem>
              <SelectItem value="low"><span className="text-[11px]">{t('multitrack.driftTaperStageLow')}</span></SelectItem>
            </SelectContent>
          </Select>}
        </Setting>
      )}
    </>
  )
}

function AnchorSettings({ content, onChange, onTaskChange }: Readonly<Omit<ContinuitySettingsProps, 'continuityMode'>>) {
  const t = useT()
  const restartId = useId()
  const restartHelpId = useId()
  const placement = getAnchorPlacement(content)
  return (
    <>
      <Setting label={t('multitrack.anchorPlacement')} help={t('multitrack.anchorPlacementTooltip')}>
        {(helpId) => <Select value={placement} onValueChange={(value) => {
          const patch = anchorPlacementPatch(value)
          if (patch) onChange(patch)
        }}>
          <SelectTrigger aria-label={t('multitrack.anchorPlacement')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ANCHOR_PLACEMENTS.map((option) => (
              <SelectItem key={option} value={option}>
                <span className="text-[11px]">{t(`multitrack.anchorPlacement_${option}`)}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>}
      </Setting>
      {anchorHasBack(placement) && (
        <Setting label={t('multitrack.anchorLate')} help={t('multitrack.anchorLateTooltip')}>
          {(helpId) => <Select value={String(getAnchorLate(content))} onValueChange={(value) => {
            const patch = anchorLatePatch(Number(value))
            if (patch) onChange(patch)
          }}>
            <SelectTrigger aria-label={t('multitrack.anchorLate')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ANCHOR_LATE_OPTIONS.map((option) => (
                <SelectItem key={option} value={String(option)}>
                  <span className="text-[11px]">{t(anchorLateKey(option))}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>}
        </Setting>
      )}
      <Setting label={t('multitrack.anchorNoise')} help={t('multitrack.anchorNoiseTooltip')}>
        {(helpId) => <Select value={String(getAnchorNoise(content))} onValueChange={(value) => {
          const patch = anchorNoisePatch(Number(value))
          if (patch) onChange(patch)
        }}>
          <SelectTrigger aria-label={t('multitrack.anchorNoise')} aria-describedby={helpId} className="h-8 w-full bg-card text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ANCHOR_NOISE_OPTIONS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                <span className="text-[11px]">{t(anchorNoiseKey(option))}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>}
      </Setting>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Checkbox id={restartId} aria-describedby={restartHelpId} checked={getAnchorRestart(content)}
            onCheckedChange={(checked) => (onTaskChange ?? onChange)(anchorRestartPatch(checked === true))} />
          <label htmlFor={restartId} className="text-[11px] font-medium text-foreground">{t('multitrack.anchorRestart')}</label>
        </div>
        <p id={restartHelpId} className="text-[10px] leading-snug text-muted-foreground">{t('multitrack.anchorRestartTooltip')}</p>
      </div>
    </>
  )
}

/**
 * The continuity mode's own settings behind one button, for modes that have any (Drift,
 * Anchor). Choosing the mode alone uses the defaults; a dot marks a task that changed them.
 */
export function ContinuitySettings({ continuityMode, content, onChange, onTaskChange, customized: selectionCustomized }: Readonly<ContinuitySettingsProps>) {
  const t = useT()
  const titleId = useId()
  const drift = continuityMode === DRIFT_TAPER_CONTINUITY_MODE
  if (!drift && continuityMode !== ANCHOR_CONTINUITY_MODE) return null
  const customized = selectionCustomized ?? (drift ? isDriftTaperCustomized(content) : isAnchorCustomized(content))
  const title = t(drift ? 'multitrack.driftSettings' : 'multitrack.anchorSettings')
  return (
    <Popover>
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button type="button" variant="outline" size="icon" className="relative h-8 w-8 bg-card" aria-label={title}>
                <SlidersHorizontal className="h-3.5 w-3.5" />
                {customized && (
                  <span data-testid="continuity-settings-customized"
                    className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-primary" />
                )}
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">{title}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent align="start" aria-labelledby={titleId} className="w-72 space-y-3 p-3">
        <div id={titleId} className="text-xs font-semibold text-foreground">{title}</div>
        {drift
          ? <DriftSettings content={content} onChange={onChange} />
          : <AnchorSettings content={content} onChange={onChange} onTaskChange={onTaskChange} />}
        <div className="flex justify-end">
          <Button type="button" variant="ghost" size="sm" className="h-7 text-[11px]" disabled={!customized}
            onClick={() => onChange(drift ? clearDriftTaperPatch() : clearAnchorPatch())}>
            {t('multitrack.continuitySettingsReset')}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
