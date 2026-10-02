import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useT } from '@/lib/i18n'
import type { TrackData } from '@/types/multitrack'

interface Props {
  data: TrackData
  isNew: boolean
  migration: TrackData | null
  onChange: (data: TrackData) => void
  onMigrate: () => void
  onApply: () => void
  onCancel: () => void
}

export function H3NativeControls({ data, isNew, migration, onChange, onMigrate, onApply, onCancel }: Props) {
  const t = useT()
  const native = data.h3_native || isNew
  const before = new Map(data.tracks.flatMap((track) => track.segments.map((segment) => [segment.id, segment] as const)))
  const changes = migration?.tracks.flatMap((track) => track.type === 'task' ? track.segments : [])
    .filter((segment) => {
      const old = before.get(segment.id)
      return old?.start_frame !== segment.start_frame || old.end_frame !== segment.end_frame
        || old.content.continuity_mode !== segment.content.continuity_mode
    }) ?? []
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-b border-border px-2 py-1 text-[10px] text-muted-foreground">
      {native ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <label className="flex cursor-pointer items-center gap-2">
              <Checkbox checked={data.h3_native?.allow_vae_fallback ?? false}
                onCheckedChange={(checked) => onChange({ ...data, h3_native: { version: 1, allow_vae_fallback: checked === true } })} />
              {t('h3Native.allowFallback')}
            </label>
          </TooltipTrigger>
          <TooltipContent className="max-w-80">{t('h3Native.fallbackTooltip')}</TooltipContent>
        </Tooltip>
      ) : (
        <>
          <span>{t('h3Native.legacy')}</span>
          <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={onMigrate}>{t('h3Native.upgrade')}</Button>
        </>
      )}
      <Dialog open={migration !== null} onOpenChange={(open) => { if (!open) onCancel() }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('h3Native.upgrade')}</DialogTitle>
            <DialogDescription>{t('h3Native.migrationDescription', { count: changes.length })}</DialogDescription>
          </DialogHeader>
          <div className="max-h-64 overflow-auto text-xs tabular-nums">
            {changes.map((segment) => {
              const old = before.get(segment.id)
              return <p key={segment.id}>{old?.start_frame}–{old?.end_frame} → {segment.start_frame}–{segment.end_frame} {t('h3Native.frames')}</p>
            })}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={onCancel}>{t('h3Native.cancel')}</Button>
            <Button onClick={onApply}>{t('h3Native.apply')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
