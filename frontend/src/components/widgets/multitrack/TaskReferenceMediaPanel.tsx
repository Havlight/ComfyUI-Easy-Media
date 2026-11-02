import { useRef, useState } from 'react'
import { AudioLines, CloudUpload, Film, Lock, Plus, Scissors, Share2, Trash2, Volume2, VolumeX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { MediaSelector } from '@/components/widgets/mediaSelector/MediaSelector'
import type { MediaTab } from '@/components/widgets/mediaSelector/MediaSelector'
import {
  H3_MAX_REFERENCE_SECONDS,
  H3_MEDIA_REFERENCE_LIMIT,
  type H3ExclusionReason,
  type H3ReferenceMediaType,
  type H3ReferenceSlot,
  type H3TaskReferenceSlots,
} from '@/lib/h3-references'
import { useT } from '@/lib/i18n'
import { mediaContentToViewUrl } from '@/lib/media-url'
import {
  createTaskMedia,
  taskMediaItemsFromSlots,
  uploadTaskMediaFile,
} from '@/lib/task-media-utils'
import { invalidateMediaListCache } from '@/stores/media-list-store'
import { cn } from '@/lib/utils'
import type { MultiTrack, MultiTrackSourceType, MultiTrackTaskMedia } from '@/types/multitrack'

interface TaskReferenceMediaPanelProps {
  mediaType: H3ReferenceMediaType
  slots: H3TaskReferenceSlots
  /** The task's stored lists; the audio tab also edits video soundtracks. */
  items: Record<H3ReferenceMediaType, MultiTrackTaskMedia[]>
  mediaTracks: MultiTrack[]
  frameRate: number
  onItemsChange: (mediaType: H3ReferenceMediaType, items: MultiTrackTaskMedia[]) => void
  onSharedChange: (item: MultiTrackTaskMedia, enabled: boolean) => void
  onDeleteItem: (item: MultiTrackTaskMedia) => void
}

type SlotPatch = { [Key in keyof MultiTrackTaskMedia]?: MultiTrackTaskMedia[Key] | undefined }

const STATUS_MESSAGES = {
  muted: ['multitrack.referenceStatusMuted', 'multitrack.referenceStatusMutedTooltip'],
  uncovered: ['multitrack.referenceStatusUncovered', 'multitrack.referenceStatusUncoveredTooltip'],
  over_limit: ['multitrack.referenceStatusOverLimit', 'multitrack.referenceStatusOverLimitTooltip'],
} as const

const REASON_MESSAGES: Record<H3ExclusionReason, readonly [string, string]> = {
  audio_locked: ['multitrack.referenceReasonAudioLocked', 'multitrack.referenceReasonAudioLockedTooltip'],
  track_muted: ['multitrack.referenceReasonTrackMuted', 'multitrack.referenceReasonTrackMutedTooltip'],
  duplicate: ['multitrack.referenceReasonDuplicate', 'multitrack.referenceReasonDuplicateTooltip'],
}

function itemName(item: MultiTrackTaskMedia): string {
  const path = item.file_path ?? item.local_path ?? item.url ?? item.slot_name ?? ''
  return item.file_name ?? path.split(/[\\/]/).pop() ?? path
}

function selectorValue(item: MultiTrackTaskMedia | undefined): string {
  return item ? item.file_path ?? item.local_path ?? item.url ?? '' : ''
}

function selectorTab(item: MultiTrackTaskMedia | undefined): MediaTab {
  if (item?.source_type === 'output') return 'outputs'
  if (item?.source_type === 'local') return 'local'
  if (item?.source_type === 'url') return 'url'
  return 'inputs'
}

function selectedSource(filePath: string, source?: 'input' | 'output' | 'local'): MultiTrackSourceType {
  if (source) return source
  return filePath.startsWith('http://') || filePath.startsWith('https://') ? 'url' : 'input'
}

/** Apply a patch, dropping fields set to undefined so a setting returns to its default. */
function patchItem(item: MultiTrackTaskMedia, patch: SlotPatch): MultiTrackTaskMedia {
  const next: Record<string, unknown> = { ...item, ...patch }
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete next[key]
  }
  return next as unknown as MultiTrackTaskMedia
}

/** Whether a slot's audio is fitted: timeline references fit unless turned off, uploads only when turned on. */
function slotFits(slot: H3ReferenceSlot): boolean {
  if (slot.reference) return slot.reference.fit
  return slot.kind === 'upload' ? slot.item?.fit === true : slot.item?.fit !== false
}

function isSent(slot: H3ReferenceSlot): boolean {
  return slot.status === 'active'
}

/**
 * A ref or edit task's reference videos or audios: timeline references that reach the task and
 * the task's own uploads, in the order the sampler numbers them. Drag cards to reorder. Timeline
 * audio that gives no reference by default (muted or audio-locked tracks) is listed with its
 * reason and can be included for this task. The audio tab starts with each video's soundtrack.
 */
export function TaskReferenceMediaPanel({
  mediaType,
  slots,
  items,
  mediaTracks,
  frameRate,
  onItemsChange,
  onSharedChange,
  onDeleteItem,
}: Readonly<TaskReferenceMediaPanelProps>) {
  const t = useT()
  const draggedKeyRef = useRef<string | null>(null)
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [reselectId, setReselectId] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const list = slots[mediaType]
  const ownItems = items[mediaType]
  const activeCount = list.filter(isSent).length
  const canAdd = activeCount < H3_MEDIA_REFERENCE_LIMIT
  const reselectItem = ownItems.find((item) => item.id === reselectId)
  const Icon = mediaType === 'video' ? Film : AudioLines

  if (!slots.customizable) {
    return (
      <div
        data-testid={`task-media-unused-${mediaType}`}
        className="flex min-h-0 w-full flex-1 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border bg-muted/30 p-4 text-center"
      >
        <Icon className="h-6 w-6 text-muted-foreground" />
        <span className="text-[10px] font-semibold">{t('multitrack.referenceMediaUnusedMode')}</span>
        <span className="text-[9px] text-muted-foreground">{t('multitrack.referenceMediaUnusedModeHint')}</span>
      </div>
    )
  }

  const activeVideos = slots.video.filter(isSent)
  let soundtrackCount = 0
  const soundtrackNumbers = activeVideos.map((slot) => (slot.reference?.soundtrack ? ++soundtrackCount : null))
  const numbers = new Map<string, number>()
  list.filter(isSent).forEach((slot, index) => {
    numbers.set(slot.key, mediaType === 'video' ? index + 1 : soundtrackCount + index + 1)
  })
  const trackName = (trackId: string) => mediaTracks.find((track) => track.id === trackId)?.name ?? ''
  const slotName = (slot: H3ReferenceSlot) => (
    slot.kind === 'upload' && slot.item ? itemName(slot.item) : trackName(slot.track_id)
  )

  function updateSlot(type: H3ReferenceMediaType, slot: H3ReferenceSlot, patch: SlotPatch) {
    if (slot.kind === 'upload' && slot.item) {
      const id = slot.item.id
      onItemsChange(type, items[type].map((item) => (item.id === id ? patchItem(item, patch) : item)))
      return
    }
    // A timeline setting stores the order the task shows, so nothing moves.
    const [placeholder] = taskMediaItemsFromSlots([slot])
    onItemsChange(type, taskMediaItemsFromSlots(slots[type].map((candidate) => (
      candidate.key === slot.key ? { ...candidate, item: patchItem(placeholder, patch) } : candidate
    ))))
  }

  function toggleIncluded(slot: H3ReferenceSlot) {
    if (slot.reason === 'audio_locked' || slot.reason === 'track_muted') {
      // Included by choice: excluding returns to the track's default instead of muting.
      updateSlot(mediaType, slot, { muted: slot.status === 'excluded' ? false : undefined })
      return
    }
    updateSlot(mediaType, slot, { muted: slot.item?.muted !== true })
  }

  function moveSlot(sourceKey: string, targetKey: string) {
    const from = list.findIndex((slot) => slot.key === sourceKey)
    const to = list.findIndex((slot) => slot.key === targetKey)
    if (from < 0 || to < 0 || from === to || list[from].kind === 'locked' || list[to].kind === 'locked') return
    const next = [...list]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    onItemsChange(mediaType, taskMediaItemsFromSlots(next))
  }

  function hasDraggedFile(dataTransfer: DataTransfer): boolean {
    return Array.from(dataTransfer.files).some((file) => file.type.startsWith(`${mediaType}/`))
      || Array.from(dataTransfer.items ?? []).some((item) => item.kind === 'file' && item.type.startsWith(`${mediaType}/`))
  }

  async function handleFileDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragOver(false)
    if (!event.dataTransfer || !canAdd) return
    const files = Array.from(event.dataTransfer.files)
      .filter((file) => file.type.startsWith(`${mediaType}/`))
      .slice(0, H3_MEDIA_REFERENCE_LIMIT - activeCount)
    const results = await Promise.allSettled(files.map((file) => uploadTaskMediaFile(file)))
    const uploaded = results.flatMap((result) => {
      if (result.status === 'fulfilled') return [result.value]
      console.error('[TaskReferenceMediaPanel] failed to upload reference media:', result.reason)
      return []
    })
    if (uploaded.length === 0) return
    invalidateMediaListCache('inputs')
    onItemsChange(mediaType, [...ownItems, ...uploaded])
  }

  function handleSelected(filePath: string, source?: 'input' | 'output' | 'local') {
    const selected = createTaskMedia(filePath, selectedSource(filePath, source))
    if (reselectItem) {
      // A new file is probed again, so its audio is unknown until then.
      onItemsChange(mediaType, ownItems.map((item) => (item.id === reselectItem.id
        ? patchItem({ ...selected, id: item.id }, { muted: item.muted, fit: item.fit, soundtrack: item.soundtrack })
        : item)))
    } else if (canAdd) {
      onItemsChange(mediaType, [...ownItems, selected])
    }
    setReselectId(null)
    setSelectorOpen(false)
  }

  function openSelector(itemId: string | null) {
    setReselectId(itemId)
    setSelectorOpen(true)
  }

  function slotDetail(slot: H3ReferenceSlot): string {
    if (!slot.reference) return ''
    if (slot.kind === 'upload' && !slot.reference.fit) {
      return t('multitrack.referenceWholeClip', { seconds: H3_MAX_REFERENCE_SECONDS })
    }
    const seconds = t('multitrack.referenceSeconds', { seconds: (slot.reference.frames / frameRate).toFixed(1) })
    return slot.reference.silent ? `${seconds} · ${t('multitrack.referenceSilentVideo')}` : seconds
  }

  function sourceLabel(slot: H3ReferenceSlot): string {
    if (slot.kind === 'locked') return t('multitrack.referenceSourceLocked')
    const tags = [t(slot.kind === 'upload' ? 'multitrack.referenceSourceUpload' : 'multitrack.referenceSourceTimeline')]
    if (slot.kind === 'shared' || slot.item?.shared_reference === true) tags.push(t('multitrack.referenceSourceShared'))
    if (isSent(slot) && slot.reason === 'audio_locked') tags.push(t('multitrack.referenceSourceLocked'))
    if (isSent(slot) && slot.reason === 'track_muted') tags.push(t('multitrack.referenceSourceMuted'))
    return tags.join(' · ')
  }

  function action(
    key: string,
    name: string,
    label: string,
    tooltip: string,
    pressed: boolean | undefined,
    onClick: () => void,
    children: React.ReactNode,
    { disabled = false, destructive = false }: { disabled?: boolean; destructive?: boolean } = {},
  ) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon"
            variant={pressed ? 'secondary' : 'ghost'}
            data-testid={`task-media-${name}-${key}`}
            className={cn(
              'h-6 w-6 cursor-pointer bg-background/70 hover:bg-background/90 [&_svg]:!size-3',
              destructive ? 'text-destructive hover:text-destructive' : pressed ? 'text-highlight' : 'text-muted-foreground',
            )}
            aria-label={label}
            aria-pressed={pressed}
            disabled={disabled}
            onClick={(event) => {
              event.stopPropagation()
              onClick()
            }}
          >
            {children}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-64">{tooltip}</TooltipContent>
      </Tooltip>
    )
  }

  function statusBadge(label: string, tooltip: string) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="absolute bottom-0 left-0 z-10 rounded-sm bg-muted px-1.5 py-0.5 text-[9px] leading-none text-muted-foreground">
            {label}
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-64">{tooltip}</TooltipContent>
      </Tooltip>
    )
  }

  function numberBadge(key: string, label: string) {
    return (
      <span
        data-testid={`task-media-index-${key}`}
        className="absolute bottom-0 left-0 z-10 rounded-sm bg-black/50 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white"
      >
        {label}
      </span>
    )
  }

  function soundtrackToggle(slot: H3ReferenceSlot, key = slot.key) {
    const soundtrack = slot.reference?.soundtrack ?? (slot.item?.soundtrack !== false)
    const silent = slot.reference?.silent === true
    return action(
      key, 'soundtrack', t('multitrack.referenceSoundtrackToggle'),
      t(silent ? 'multitrack.referenceSoundtrackSilentTooltip' : 'multitrack.referenceSoundtrackTooltip'),
      soundtrack, () => updateSlot('video', slot, { soundtrack: !soundtrack }),
      soundtrack ? <Volume2 /> : <VolumeX />, { disabled: !slot.reference || (silent && !soundtrack) },
    )
  }

  function cardFrame(key: string, status: string, content: React.ReactNode, props: React.HTMLAttributes<HTMLDivElement> & { draggable?: boolean } = {}, highlighted = false) {
    return (
      <div
        key={key}
        data-testid={`task-media-${key}`}
        data-status={status}
        {...props}
        className={cn(
          'group/task-media relative flex aspect-square w-full flex-col items-center justify-center gap-1 overflow-hidden rounded-md border bg-card p-2 text-center',
          highlighted ? 'border-highlight' : 'border-border',
          props.className,
        )}
      >
        {content}
      </div>
    )
  }

  function renderSoundtrack(slot: H3ReferenceSlot, index: number) {
    const key = `soundtrack:${slot.key}`
    const number = soundtrackNumbers[index]
    const silent = slot.reference?.silent === true
    const name = t('multitrack.referenceSoundtrackCardName', {
      video: t('multitrack.referenceVideoItem', { n: index + 1 }), name: slotName(slot),
    })
    return cardFrame(key, number === null ? 'off' : 'active', <>
      <span className="absolute left-1 top-1 z-10 rounded-sm bg-background/80 px-1 text-[8px] text-muted-foreground">
        {t('multitrack.referenceSourceSoundtrack')}
      </span>
      <div className={cn('relative z-10 flex min-w-0 max-w-full flex-col items-center gap-0.5', number === null && 'opacity-50')}>
        <Film className="h-5 w-5 text-muted-foreground" />
        <span className="max-w-full truncate text-[10px] font-medium" title={name}>{name}</span>
        {number !== null && (
          <span className="text-[9px] text-muted-foreground">
            {slotDetail({ ...slot, reference: slot.reference && { ...slot.reference, silent: false } })}
          </span>
        )}
      </div>
      {slot.kind !== 'locked' && (
        <div className="absolute right-0.5 top-1 z-20 flex gap-0.5 opacity-0 transition-opacity group-hover/task-media:opacity-100 group-focus-within/task-media:opacity-100">
          {soundtrackToggle(slot, key)}
        </div>
      )}
      {number !== null
        ? numberBadge(key, t('multitrack.referenceAudioItem', { n: number }))
        : statusBadge(t(silent ? 'multitrack.referenceSilentVideo' : 'multitrack.referenceSoundtrackOff'),
          t(silent ? 'multitrack.referenceSoundtrackSilentTooltip' : 'multitrack.referenceSoundtrackTooltip'))}
    </>)
  }

  function renderSlot(slot: H3ReferenceSlot) {
    const item = slot.item
    const isUpload = slot.kind === 'upload' && item !== undefined
    const number = numbers.get(slot.key)
    const name = slotName(slot)
    const previewUrl = isUpload && mediaType === 'video' && item.source_type !== 'track'
      ? mediaContentToViewUrl({
          source_type: item.source_type ?? 'input', file_path: item.file_path, local_path: item.local_path,
          url: item.url, slot_name: item.slot_name,
        })
      : null
    const shared = item?.shared_reference === true
    const optional = slot.reason === 'audio_locked' || slot.reason === 'track_muted'
    const included = optional ? slot.status !== 'excluded' : item?.muted !== true
    const fits = slotFits(slot)
    const status = slot.status === 'excluded' && slot.reason ? REASON_MESSAGES[slot.reason]
      : slot.status === 'active' || slot.status === 'excluded' ? null : STATUS_MESSAGES[slot.status]
    const reselectable = isUpload && !shared
    return cardFrame(slot.key, slot.status, <>
      {previewUrl ? (
        <video
          src={previewUrl}
          preload="metadata"
          muted
          className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-50"
        />
      ) : null}
      <span className="absolute left-1 top-1 z-10 max-w-[70%] truncate rounded-sm bg-background/80 px-1 text-[8px] text-muted-foreground">
        {sourceLabel(slot)}
      </span>
      <div className={cn('relative z-10 flex min-w-0 max-w-full flex-col items-center gap-0.5', !isSent(slot) && 'opacity-50')}>
        {slot.kind === 'locked' ? <Lock className="h-5 w-5 text-muted-foreground" /> : <Icon className="h-5 w-5 text-muted-foreground" />}
        <span className="max-w-full truncate text-[10px] font-medium" title={name}>{name}</span>
        <span className="text-[9px] text-muted-foreground">{slotDetail(slot)}</span>
      </div>
      {slot.kind !== 'locked' && (
        <div
          data-testid={`task-media-actions-${slot.key}`}
          className="absolute right-0.5 top-1 z-20 flex gap-0.5 opacity-0 transition-opacity group-hover/task-media:opacity-100 group-focus-within/task-media:opacity-100"
        >
          {isUpload && action(
            slot.key, 'shared',
            t(shared ? 'multitrack.disableSharedReference' : 'multitrack.enableSharedReference'),
            t('multitrack.sharedReferenceMediaTooltip'),
            shared, () => onSharedChange(item, !shared), <Share2 />,
          )}
          {action(
            slot.key, 'muted',
            t(included ? 'multitrack.excludeReferenceMedia' : 'multitrack.includeReferenceMedia'),
            t(included ? 'multitrack.excludeReferenceMediaTooltip' : 'multitrack.includeReferenceMediaTooltip'),
            !included, () => toggleIncluded(slot), <span className="text-[10px] font-bold">M</span>,
            { disabled: slot.reason === 'duplicate' },
          )}
          {mediaType === 'video' && soundtrackToggle(slot)}
          {mediaType === 'audio' && action(
            slot.key, 'fit',
            t('multitrack.referenceFit'), t('multitrack.referenceFitTooltip', { seconds: H3_MAX_REFERENCE_SECONDS }),
            fits, () => updateSlot(mediaType, slot, { fit: !fits }), <Scissors />,
          )}
          {isUpload && action(
            slot.key, 'delete', t('multitrack.deleteReferenceMedia'), t('multitrack.deleteReferenceMedia'),
            undefined, () => onDeleteItem(item), <Trash2 />, { destructive: true },
          )}
        </div>
      )}
      {number !== undefined
        ? numberBadge(slot.key, t(mediaType === 'video' ? 'multitrack.referenceVideoItem' : 'multitrack.referenceAudioItem', { n: number }))
        : status ? statusBadge(t(status[0]), t(status[1])) : null}
    </>, {
      draggable: slot.kind !== 'locked',
      role: reselectable ? 'button' : undefined,
      tabIndex: reselectable ? 0 : undefined,
      'aria-label': reselectable ? t('multitrack.reselectReferenceMedia', { name }) : undefined,
      className: reselectable ? 'cursor-pointer' : undefined,
      onClick: () => {
        if (reselectable) openSelector(item.id)
      },
      onKeyDown: (event) => {
        if (event.target !== event.currentTarget || !reselectable) return
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        openSelector(item.id)
      },
      onDragStart: () => {
        draggedKeyRef.current = slot.key
      },
      onDragOver: (event) => {
        if (draggedKeyRef.current) event.preventDefault()
      },
      onDrop: (event) => {
        const sourceKey = draggedKeyRef.current
        draggedKeyRef.current = null
        if (!sourceKey) return
        event.preventDefault()
        event.stopPropagation()
        moveSlot(sourceKey, slot.key)
      },
    }, shared || slot.kind === 'shared')
  }

  const addLabel = t(mediaType === 'video' ? 'multitrack.addVideo' : 'multitrack.addAudioSegment')
  return (
    <TooltipProvider delayDuration={300}>
      <div
        data-testid={`task-media-panel-${mediaType}`}
        aria-label={t(mediaType === 'video' ? 'multitrack.referenceVideos' : 'multitrack.referenceAudios')}
        className={cn(
          'flex min-h-0 w-full flex-1 flex-col gap-2 overflow-y-auto rounded-md border border-dashed p-3 transition-colors',
          isDragOver ? 'border-primary bg-accent/20' : 'border-border bg-muted/30',
        )}
        onDragEnter={(event) => {
          if (event.dataTransfer && hasDraggedFile(event.dataTransfer)) setIsDragOver(true)
        }}
        onDragOver={(event) => {
          if (!event.dataTransfer || !hasDraggedFile(event.dataTransfer)) return
          event.preventDefault()
          setIsDragOver(true)
        }}
        onDragLeave={(event) => {
          const related = event.relatedTarget
          if (related instanceof Node && event.currentTarget.contains(related)) return
          setIsDragOver(false)
        }}
        onDrop={(event) => {
          if (event.dataTransfer && hasDraggedFile(event.dataTransfer)) void handleFileDrop(event)
        }}
      >
        <Popover
          open={selectorOpen}
          onOpenChange={(open) => {
            setSelectorOpen(open)
            if (!open) setReselectId(null)
          }}
        >
          <PopoverAnchor asChild>
            <div className="grid shrink-0 grid-cols-2 content-start gap-2">
              {mediaType === 'audio' && activeVideos.map(renderSoundtrack)}
              {list.map(renderSlot)}
              {canAdd ? (
                <Button
                  type="button"
                  variant="outline"
                  className="aspect-square h-auto w-full flex-col gap-1 border-dashed text-muted-foreground"
                  aria-label={addLabel}
                  onClick={() => openSelector(null)}
                >
                  {list.length === 0 ? <CloudUpload className="h-6 w-6" /> : <Plus className="h-6 w-6" />}
                  {list.length === 0 && (
                    <span className="whitespace-normal text-[9px]">{t('multitrack.imageDropHint')}</span>
                  )}
                </Button>
              ) : null}
            </div>
          </PopoverAnchor>
          <PopoverContent className="w-auto p-0" align="end">
            <MediaSelector
              key={reselectId ?? `add-${mediaType}`}
              value={selectorValue(reselectItem)}
              mediaType={mediaType}
              defaultTab={selectorTab(reselectItem)}
              onChange={handleSelected}
            />
          </PopoverContent>
        </Popover>
      </div>
    </TooltipProvider>
  )
}
