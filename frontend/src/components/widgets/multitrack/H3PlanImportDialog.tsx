import { useEffect, useState } from 'react'
import type { ComfyApp } from '@comfyorg/comfyui-frontend-types'
import { AlertTriangle, Loader2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { prepareH3NativeEdit } from '@/hooks/use-h3-native-editing'
import { fetchH3TimelineStatuses, H3_STALE_STATUSES, h3TasksWithStatus, type TimelineStatus } from '@/hooks/use-h3-timeline-status'
import {
  buildH3PlanImport,
  groupH3PlanIssues,
  h3PlanMediaRequests,
  parseH3Plan,
  type H3PlanChange,
  type H3PlanImport,
  type H3PlanIssue,
  type H3PlanIssueGroup,
  type H3PlanMediaResult,
} from '@/lib/h3-plan'
import { h3PlanIssueText, resolveH3PlanMedia, withH3LoraFiles } from '@/lib/h3-plan-convert'
import type { H3PlanLoraTarget } from '@/lib/h3-plan-loras'
import type { ConnectedH3Project } from '@/lib/h3-project-snapshot'
import { useT } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import type { TrackData } from '@/types/multitrack'
import type { SegmentLoraPlan } from '@/types/segment-loras'

export interface H3PlanImportRequest {
  fileName: string
  text: string
  /** The H3 Segment LoRA node feeding the Editor's Projects when the file was chosen. */
  loras?: H3PlanLoraTarget
}

interface H3PlanImportDialogProps {
  request: H3PlanImportRequest | null
  current: TrackData
  format: string | undefined
  app?: ComfyApp
  projects: ConnectedH3Project[]
  /** The linked Projects' statuses for the current timeline. */
  currentStatuses: TimelineStatus[]
  /** `loras` is set when the import also changes the LoRA node's rules. Throws when it cannot apply. */
  onApply: (data: TrackData, loras?: SegmentLoraPlan) => void
  onClose: () => void
}

type Preview =
  | { state: 'loading' }
  /** `showLoras` when the file lists LoRA rules or the import moves the node's rules. */
  | { state: 'ready'; result: H3PlanImport; invalidated: Set<string>; showLoras: boolean }

const CHANGE_CLASSES: Record<H3PlanChange, string> = {
  unchanged: 'text-muted-foreground',
  modified: 'text-highlight',
  added: 'text-primary',
  removed: 'text-destructive line-through',
}

/**
 * Preview a plan file before it replaces the task track: what each task becomes, the segment
 * LoRA rules it writes, which saved results stop matching, and any problem that blocks the
 * import. Applying is one undo step.
 */
export function H3PlanImportDialog({
  request,
  current,
  format,
  app,
  projects,
  currentStatuses,
  onApply,
  onClose,
}: Readonly<H3PlanImportDialogProps>) {
  const t = useT()
  const [preview, setPreview] = useState<Preview>({ state: 'loading' })
  const [applyError, setApplyError] = useState<string | null>(null)

  useEffect(() => {
    if (!request) return
    let cancelled = false
    setPreview({ state: 'loading' })
    setApplyError(null)
    const fail = (issues: H3PlanIssue[]) => {
      if (!cancelled) setPreview({ state: 'ready', result: { data: null, rows: [], trackRows: [], issues }, invalidated: new Set(), showLoras: false })
    }
    const parsed = parseH3Plan(request.text)
    if (!parsed.plan) {
      fail(parsed.issues)
      return
    }
    const plan = parsed.plan
    void (async () => {
      let media: Map<string, H3PlanMediaResult>
      try {
        media = await resolveH3PlanMedia(h3PlanMediaRequests(plan))
      } catch (cause: unknown) {
        fail([{ level: 'error', code: 'resolve_failed', params: { message: cause instanceof Error ? cause.message : String(cause) } }])
        return
      }
      const listed = await withH3LoraFiles(plan, request.loras)
      const result = buildH3PlanImport(current, plan, media, (candidate) => prepareH3NativeEdit(current, candidate, format), listed.target)
      result.issues.unshift(...listed.issues)
      const rules = result.loras?.changed ? result.loras.rules : undefined
      // A bypassed or muted LoRA node takes the rules, but runs use none of them.
      const runRules = listed.target && 'rules' in listed.target && listed.target.inactive ? undefined : rules
      let invalidated = new Set<string>()
      if (result.data && app?.api && projects.length > 0) {
        try {
          // A saved result goes stale when it matched before the import and no longer does,
          // its LoRA rules included.
          const proposed = await fetchH3TimelineStatuses(app, runRules ? projects.map((project) => ({
            ...project, project_snapshot: { ...project.project_snapshot, segment_loras: runRules },
          })) : projects, result.data)
          const stale = new Set(h3TasksWithStatus(proposed, H3_STALE_STATUSES))
          invalidated = new Set(h3TasksWithStatus(currentStatuses, new Set(['saved', 'pending'])).filter((id) => stale.has(id)))
        } catch (cause: unknown) {
          result.issues.push({ level: 'warning', code: 'status_unavailable', params: { message: cause instanceof Error ? cause.message : String(cause) } })
        }
      }
      if (!cancelled) setPreview({ state: 'ready', result, invalidated, showLoras: plan.loras !== undefined || !!rules })
    })()
    return () => {
      cancelled = true
    }
    // The preview belongs to the file that opened it; later timeline edits do not rebuild it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  const result = preview.state === 'ready' ? preview.result : null
  const groups = groupH3PlanIssues(result?.issues ?? [])
  const errors = groups.filter((item) => item.level === 'error')
  const warnings = groups.filter((item) => item.level === 'warning')
  const counts = (change: H3PlanChange) => result?.rows.filter((row) => row.change === change).length ?? 0
  const issueText = (item: H3PlanIssueGroup) => h3PlanIssueText(item, t)
  const loraRows = preview.state === 'ready' && preview.showLoras ? result?.loras?.rows ?? [] : []

  return (
    <Dialog open={request !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      {/* A column so the preview scrolls between a fixed header and footer; above ComfyUI's top bar (z 999). */}
      <DialogContent className="z-[1000] flex max-h-[85vh] max-w-4xl flex-col overflow-hidden" data-testid="h3-plan-import-dialog">
        <DialogHeader>
          <DialogTitle>{t('h3Plan.importTitle')}</DialogTitle>
          <DialogDescription>
            {request?.fileName}
            {result && result.rows.length > 0 ? ` · ${t('h3Plan.summary', {
              added: counts('added'), modified: counts('modified'), removed: counts('removed'), unchanged: counts('unchanged'),
            })}` : ''}
          </DialogDescription>
        </DialogHeader>
        {preview.state === 'loading' ? (
          <div className="flex items-center gap-2 py-6 text-[12px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('h3Plan.loading')}
          </div>
        ) : (
          <div className="flex min-h-0 flex-col gap-3 overflow-y-auto text-[11px]">
            {errors.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="h3-plan-errors">
                {errors.map((item, index) => (
                  <li key={`error-${index}`} className="flex items-start gap-1 text-destructive">
                    <XCircle className="mt-px h-3 w-3 shrink-0" />
                    {issueText(item)}
                  </li>
                ))}
              </ul>
            )}
            {result && result.rows.length > 0 && (
              <table className="w-full table-fixed border-collapse" data-testid="h3-plan-rows">
                <thead className="text-left text-muted-foreground">
                  <tr className="border-b border-border">
                    <th className="w-8 py-1">#</th>
                    <th className="w-16 py-1">{t('h3Plan.columnChange')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnSeconds')}</th>
                    <th className="w-20 py-1">{t('h3Plan.columnMode')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnContinuity')}</th>
                    <th className="w-32 py-1">{t('h3Plan.columnReferences')}</th>
                    <th className="py-1">{t('h3Plan.columnPrompt')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row) => (
                    <tr key={`${row.change}:${row.id}`} data-testid={`h3-plan-row-${row.id}`} className="border-b border-border/50 align-top">
                      <td className="py-1 tabular-nums">{row.change === 'removed' ? '–' : row.index}</td>
                      <td className={cn('py-1', CHANGE_CLASSES[row.change])}>{t(`h3Plan.change_${row.change}`)}</td>
                      <td className="py-1 tabular-nums">
                        {row.beforeSeconds !== undefined ? `${row.beforeSeconds} → ${row.seconds}` : row.seconds}
                      </td>
                      <td className="py-1">{t(`multitrackTaskModes.${row.mode}`)}</td>
                      <td className="py-1">{t(`multitrackContinuityModes.${row.continuity}`)}</td>
                      <td className="py-1">
                        {row.change === 'removed' ? '' : t('h3Plan.referenceCounts', { images: row.images, videos: row.videos, audios: row.audios })}
                      </td>
                      <td className="py-1">
                        <div className="truncate" title={row.prompt}>{row.prompt}</div>
                        {preview.state === 'ready' && preview.invalidated.has(row.id) && row.change !== 'removed' && (
                          <div className="mt-0.5 flex items-center gap-1 text-highlight">
                            <AlertTriangle className="h-3 w-3 shrink-0" />
                            {t('h3Plan.savedInvalidated')}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {result && result.trackRows.length > 0 && (
              <table className="w-full table-fixed border-collapse" data-testid="h3-plan-track-rows">
                <thead className="text-left text-muted-foreground">
                  <tr className="border-b border-border">
                    <th className="w-8 py-1">#</th>
                    <th className="w-16 py-1">{t('h3Plan.columnChange')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnTrack')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnSettings')}</th>
                    <th className="py-1">{t('h3Plan.columnClips')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trackRows.map((row) => (
                    <tr key={`${row.change}:${row.id}`} data-testid={`h3-plan-track-${row.id}`} className="border-b border-border/50 align-top">
                      <td className="py-1 tabular-nums">{row.change === 'removed' ? '–' : row.index}</td>
                      <td className={cn('py-1', CHANGE_CLASSES[row.change])}>{t(`h3Plan.change_${row.change}`)}</td>
                      <td className="py-1">{t(`h3Plan.trackType_${row.type}`)}</td>
                      <td className="py-1">
                        {[row.audioLocked && t('h3Plan.trackLocked'), row.muted && t('h3Plan.trackMuted')].filter(Boolean).join(' · ')}
                      </td>
                      <td className="py-1">
                        {row.clips.length === 0 ? <span className="text-muted-foreground">{t('h3Plan.noClips')}</span> : row.clips.map((clip, index) => (
                          <div key={index} className="truncate" title={clip.name}>
                            {t('h3Plan.clipRange', { name: clip.name, start: clip.start, end: clip.end })}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {loraRows.length > 0 && (
              <table className="w-full table-fixed border-collapse" data-testid="h3-plan-lora-rows">
                <thead className="text-left text-muted-foreground">
                  <tr className="border-b border-border">
                    <th className="w-8 py-1">#</th>
                    <th className="w-16 py-1">{t('h3Plan.columnChange')}</th>
                    <th className="py-1">{t('h3Plan.columnLora')}</th>
                    <th className="w-16 py-1">{t('h3Plan.columnStrength')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnStage')}</th>
                    <th className="w-24 py-1">{t('h3Plan.columnSegments')}</th>
                  </tr>
                </thead>
                <tbody>
                  {loraRows.map((row) => (
                    <tr key={`${row.change}:${row.id}`} data-testid={`h3-plan-lora-${row.id}`} className="border-b border-border/50 align-top">
                      <td className="py-1 tabular-nums">{row.change === 'removed' ? '–' : row.index}</td>
                      <td className={cn('py-1', CHANGE_CLASSES[row.change])}>{t(`h3Plan.change_${row.change}`)}</td>
                      <td className="py-1">
                        <div className="truncate" title={row.lora}>
                          {row.lora}{row.enabled ? '' : ` · ${t('segmentLoras.disabled')}`}
                        </div>
                      </td>
                      <td className="py-1 tabular-nums">{row.strength}</td>
                      <td className="py-1">{t(`h3Plan.stage_${row.stage}`)}</td>
                      <td className="py-1 tabular-nums">
                        {row.end === null ? t('h3Plan.loraRangeToEnd', { start: row.start }) : t('h3Plan.loraRange', { start: row.start, end: row.end })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {warnings.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="h3-plan-warnings">
                {warnings.map((item, index) => (
                  <li key={`warning-${index}`} className="flex items-start gap-1 text-muted-foreground">
                    <AlertTriangle className="mt-px h-3 w-3 shrink-0 text-highlight" />
                    {issueText(item)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <DialogFooter className="items-center">
          {errors.length > 0 && (
            <span className="text-[11px] text-destructive sm:mr-auto" data-testid="h3-plan-blocked">
              {t('h3Plan.blocked', { count: errors.length })}
            </span>
          )}
          {applyError && (
            <span className="text-[11px] text-destructive sm:mr-auto" data-testid="h3-plan-apply-error">{applyError}</span>
          )}
          <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
          <Button
            type="button"
            disabled={!result?.data}
            onClick={() => {
              if (!result?.data) return
              try {
                onApply(result.data, result.loras?.changed ? result.loras.rules : undefined)
              } catch (cause: unknown) {
                setApplyError(cause instanceof Error ? cause.message : String(cause))
              }
            }}
          >
            {t('h3Plan.apply')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
