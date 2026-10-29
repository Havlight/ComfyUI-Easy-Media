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
  /** The task's stored list for this media type. */
  items: MultiTrackTaskMedia[]
  mediaTracks: MultiTrack[]
  frameRate: number
  canShare: (item: MultiTrackTaskMedia) => boolean
  onItemsChange: (items: MultiTrackTaskMedia[]) => void
  onSharedChange: (item: MultiTrackTaskMedia, enabled: boolean) => void
  onDeleteItem: (item: MultiTrackTaskMedia) => void
}

const STATUS_MESSAGES = {
  muted: ['multitrack.referenceStatusMuted', 'multitrack.referenceStatusMutedTooltip'],
  uncovered: ['multitrack.referenceStatusUncovered', 'multitrack.referenceStatusUncoveredTooltip'],
  excluded: ['multitrack.referenceStatusExcluded', 'multitrack.referenceStatusExcludedTooltip'],
  over_limit: ['multitrack.referenceStatusOverLimit', 'multitrack.referenceStatusOverLimitTooltip'],
  locked: ['multitrack.referenceStatusLocked', 'multitrack.referenceStatusLockedTooltip'],
} as const

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

/** Whether a slot's audio is fitted: timeline references fit unless turned off, uploads only when turned on. */
function slotFits(slot: H3ReferenceSlot): boolean {
  if (slot.reference) return slot.reference.fit
  return slot.kind === 'upload' ? slot.item?.fit === true : slot.item?.fit !== false
}

/**
 * A ref or edit task's reference videos or audios: timeline references that reach the task and
 * the task's own uploads, in the order the sampler numbers them. Drag cards to reorder.
 */
export function TaskReferenceMediaPanel({
  mediaType,
  slots,
  items,
  mediaTracks,
  frameRate,
  canShare,
  onItemsChange,
  onSharedChange,
  onDeleteItem,
}: Readonly<TaskReferenceMediaPanelProps>) {
  const t = useT()
  const draggedKeyRef = useRef<string | null>(null)
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [reselectId, setReselectId] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const list = mediaType === 'video' ? slots.video : slots.audio
  const activeCount = list.filter((slot) => slot.status === 'active').length
  const locked = mediaType === 'video' && list.some((slot) => slot.kind === 'locked')
  const canAdd = activeCount < H3_MEDIA_REFERENCE_LIMIT && !locked
  const reselectItem = items.find((item) => item.id === reselectId)
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

  let soundtrackNumber = 0
  const activeVideos = slots.video.filter((slot) => slot.status === 'active')
  const soundtracks = activeVideos.flatMap((slot, index) => (
    slot.reference?.soundtrack ? [{ slot, video: index + 1, audio: ++soundtrackNumber }] : []
  ))
  const numbers = new Map<string, number>()
  list.filter((slot) => slot.status === 'active').forEach((slot, index) => {
    numbers.set(slot.key, mediaType === 'video' ? index + 1 : soundtracks.length + index + 1)
  })
  const trackName = (trackId: string) => mediaTracks.find((track) => track.id === trackId)?.name ?? ''

  function writeSlots(next: H3ReferenceSlot[]) {
    onItemsChange(taskMediaItemsFromSlots(next))
  }

  function updateSlot(slot: H3ReferenceSlot, patch: Partial<MultiTrackTaskMedia>) {
    if (slot.kind === 'upload' && slot.item) {
      const id = slot.item.id
      onItemsChange(items.map((item) => (item.id === id ? { ...item, ...patch } : item)))
      return
    }
    // A timeline setting stores the order the task shows, so nothing moves.
    const [placeholder] = taskMediaItemsFromSlots([slot])
    writeSlots(list.map((candidate) => (
      candidate.key === slot.key ? { ...candidate, item: { ...placeholder, ...patch } } : candidate
    )))
  }

  function moveSlot(sourceKey: string, targetKey: string) {
    const from = list.findIndex((slot) => slot.key === sourceKey)
    const to = list.findIndex((slot) => slot.key === targetKey)
    if (from < 0 || to < 0 || from === to || list[from].kind === 'locked' || list[to].kind === 'locked') return
    const next = [...list]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    writeSlots(next)
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
    onItemsChange([...items, ...uploaded])
  }

  function handleSelected(filePath: string, source?: 'input' | 'output' | 'local') {
    const selected = createTaskMedia(filePath, selectedSource(filePath, source))
    if (reselectItem) {
      onItemsChange(items.map((item) => (item.id === reselectItem.id
        ? { ...selected, id: item.id, muted: item.muted, fit: item.fit, soundtrack: item.soundtrack }
        : item)))
    } else if (canAdd) {
      onItemsChange([...items, selected])
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
    return t('multitrack.referenceSeconds', { seconds: (slot.reference.frames / frameRate).toFixed(1) })
  }

  function sourceLabel(slot: H3ReferenceSlot): string {
    if (slot.kind === 'locked') return t('multitrack.referenceSourceLocked')
    const shared = slot.kind === 'shared' || slot.item?.shared_reference === true
    const origin = t(slot.kind === 'upload' ? 'multitrack.referenceSourceUpload' : 'multitrack.referenceSourceTimeline')
    return shared ? `${origin} · ${t('multitrack.referenceSourceShared')}` : origin
  }

  function action(
    slot: H3ReferenceSlot,
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
            data-testid={`task-media-${name}-${slot.key}`}
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

  function renderSlot(slot: H3ReferenceSlot) {
    const item = slot.item
    const isUpload = slot.kind === 'upload' && item !== undefined
    const number = numbers.get(slot.key)
    const name = isUpload ? itemName(item) : trackName(slot.track_id)
    const previewUrl = isUpload && mediaType === 'video' && item.source_type !== 'track'
      ? mediaContentToViewUrl({
          source_type: item.source_type ?? 'input', file_path: item.file_path, local_path: item.local_path,
          url: item.url, slot_name: item.slot_name,
        })
      : null
    const muted = item?.muted === true
    const fits = slotFits(slot)
    const soundtrack = slot.reference?.soundtrack ?? (item?.soundtrack !== false)
    const shared = item?.shared_reference === true
    const shareable = shared || (item !== undefined && canShare(item))
    const status = slot.status === 'active' ? null : STATUS_MESSAGES[slot.status]
    return (
      <div
        key={slot.key}
        draggable={slot.kind !== 'locked'}
        data-testid={`task-media-${slot.key}`}
        data-status={slot.status}
        role={isUpload && !shared ? 'button' : undefined}
        tabIndex={isUpload && !shared ? 0 : undefined}
        aria-label={isUpload && !shared ? t('multitrack.reselectReferenceMedia', { name }) : undefined}
        className={cn(
          'group/task-media relative flex aspect-square w-full flex-col items-center justify-center gap-1 overflow-hidden rounded-md border bg-card p-2 text-center',
          shared || slot.kind === 'shared' ? 'border-highlight' : 'border-border',
          isUpload && !shared && 'cursor-pointer',
        )}
        onClick={() => {
          if (isUpload && !shared) openSelector(item.id)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || !isUpload || shared) return
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          openSelector(item.id)
        }}
        onDragStart={() => {
          draggedKeyRef.current = slot.key
        }}
        onDragOver={(event) => {
          if (draggedKeyRef.current) event.preventDefault()
        }}
        onDrop={(event) => {
          const sourceKey = draggedKeyRef.current
          draggedKeyRef.current = null
          if (!sourceKey) return
          event.preventDefault()
          event.stopPropagation()
          moveSlot(sourceKey, slot.key)
        }}
      >
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
        <div className={cn('relative z-10 flex min-w-0 max-w-full flex-col items-center gap-0.5', slot.status !== 'active' && 'opacity-50')}>
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
              slot, 'shared',
              t(shared ? 'multitrack.disableSharedReference' : 'multitrack.enableSharedReference'),
              t(shareable ? 'multitrack.sharedReferenceMediaTooltip' : 'multitrack.sharedReferenceMediaLimitTooltip'),
              shared, () => onSharedChange(item, !shared), <Share2 />, { disabled: !shareable },
            )}
            {action(
              slot, 'muted',
              t(muted ? 'multitrack.includeReferenceMedia' : 'multitrack.excludeReferenceMedia'),
              t(muted ? 'multitrack.includeReferenceMediaTooltip' : 'multitrack.excludeReferenceMediaTooltip'),
              muted, () => updateSlot(slot, { muted: !muted }), <span className="text-[10px] font-bold">M</span>,
            )}
            {mediaType === 'video' && action(
              slot, 'soundtrack',
              t('multitrack.referenceSoundtrackToggle'), t('multitrack.referenceSoundtrackTooltip'),
              soundtrack, () => updateSlot(slot, { soundtrack: !soundtrack }),
              soundtrack ? <Volume2 /> : <VolumeX />, { disabled: !slot.reference },
            )}
            {mediaType === 'audio' && action(
              slot, 'fit',
              t('multitrack.referenceFit'), t('multitrack.referenceFitTooltip', { seconds: H3_MAX_REFERENCE_SECONDS }),
              fits, () => updateSlot(slot, { fit: !fits }), <Scissors />,
            )}
            {isUpload && action(
              slot, 'delete', t('multitrack.deleteReferenceMedia'), t('multitrack.deleteReferenceMedia'),
              undefined, () => onDeleteItem(item), <Trash2 />, { destructive: true },
            )}
          </div>
        )}
        {number !== undefined ? (
          <span
            data-testid={`task-media-index-${slot.key}`}
            className="absolute bottom-0 left-0 z-10 rounded-sm bg-black/50 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white"
          >
            {t(mediaType === 'video' ? 'multitrack.referenceVideoItem' : 'multitrack.referenceAudioItem', { n: number })}
          </span>
        ) : status ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="absolute bottom-0 left-0 z-10 rounded-sm bg-muted px-1.5 py-0.5 text-[9px] leading-none text-muted-foreground">
                {t(status[0])}
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-64">{t(status[1])}</TooltipContent>
          </Tooltip>
        ) : null}
      </div>
    )
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
        {mediaType === 'audio' && soundtracks.length > 0 && (
          <div data-testid="task-media-soundtracks" className="flex shrink-0 flex-col gap-0.5 rounded-md bg-card px-2 py-1 text-[10px] text-muted-foreground">
            {soundtracks.map(({ slot, video, audio }) => (
              <span key={slot.key} className="truncate">
                {t('multitrack.referenceSoundtrackRow', {
                  audio: t('multitrack.referenceAudioItem', { n: audio }),
                  video: t('multitrack.referenceVideoItem', { n: video }),
                  name: slot.kind === 'upload' && slot.item ? itemName(slot.item) : trackName(slot.track_id),
                })}
              </span>
            ))}
          </div>
        )}
        <Popover
          open={selectorOpen}
          onOpenChange={(open) => {
            setSelectorOpen(open)
            if (!open) setReselectId(null)
          }}
        >
          <PopoverAnchor asChild>
            <div className="grid shrink-0 grid-cols-2 content-start gap-2">
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
