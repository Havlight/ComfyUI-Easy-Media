import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useH3ProjectSnapshots } from '@/hooks/use-h3-project-snapshots'
import { useSegmentLoraEditor } from '@/hooks/use-segment-lora-editor'
import { useSegmentLoraPreview } from '@/hooks/use-segment-lora-preview'
import type { ReactWidgetProps } from '@/lib/create-react-widget'
import { LocaleContext, translate, useT } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import type { SegmentLoraPlan, SegmentLoraRule } from '@/types/segment-loras'

function CountField({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const t = useT()
  const [focused, setFocused] = useState(false)
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])
  const commit = () => {
    const parsed = Number(draft)
    if (Number.isInteger(parsed) && (parsed === -1 || parsed > 0)) onChange(parsed)
    else setDraft(String(value))
    setFocused(false)
  }
  return <Input className="h-8 text-center text-xs" inputMode="numeric" aria-label={t('segmentLoras.count')}
    title={t('segmentLoras.countHelp')} value={focused ? draft : value === -1 ? t('segmentLoras.untilEnd') : String(value)}
    onFocus={() => setFocused(true)} onChange={(event) => setDraft(event.target.value)} onBlur={commit}
    onKeyDown={(event) => {
      if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur() }
      if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault()
        const next = event.key === 'ArrowUp' ? (value === -1 ? 1 : value + 1) : (value <= 1 ? -1 : value - 1)
        onChange(next); setDraft(String(next))
      }
    }} />
}

function FilePicker({ value, files, onChange }: { value: string; files: string[]; onChange: (name: string) => void }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const filtered = files.filter((file) => file.toLowerCase().includes(search.toLowerCase()))
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button variant="outline" className="h-8 w-full justify-start px-2 text-xs" title={value} aria-label={t('segmentLoras.file')}>
      <span className="truncate">{value || t('segmentLoras.choose')}</span>
    </Button></PopoverTrigger>
    <PopoverContent className="w-96 max-w-[90vw] p-2" align="start">
      <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('segmentLoras.search')} aria-label={t('segmentLoras.search')} />
      <div className="mt-2 max-h-60 overflow-y-auto" role="listbox" aria-label={t('segmentLoras.file')}>
        {filtered.map((file) => <Button key={file} variant="ghost" role="option" aria-selected={file === value}
          className="h-auto min-h-8 w-full justify-start whitespace-normal break-all px-2 py-1 text-left text-xs"
          onClick={() => { onChange(file); setOpen(false) }}>{file}</Button>)}
        {!filtered.length && <p className="p-2 text-xs text-muted-foreground">{t('segmentLoras.noFiles')}</p>}
      </div>
    </PopoverContent>
  </Popover>
}

function RuleRow({ rule, number, files, update, remove, note }: {
  rule: SegmentLoraRule; number: number; files: string[]; note?: string
  update: (id: string, patch: Partial<SegmentLoraRule>) => void; remove: (id: string) => void
}) {
  const t = useT()
  const [advanced, setAdvanced] = useState(false)
  const patch = (value: Partial<SegmentLoraRule>) => update(rule.id, value)
  return <div className={cn('rounded-md border border-border bg-muted/20 p-2', !rule.enabled && 'opacity-60')}>
    <div className="mb-1.5 flex items-center gap-2">
      <Checkbox checked={rule.enabled} onCheckedChange={(enabled) => patch({ enabled: enabled === true })} aria-label={t('segmentLoras.enableRow', { n: number })} />
      <span className="text-xs text-muted-foreground">{number}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{note}</span>
      <Button variant="ghost" size="icon" className="size-6" aria-label={t('segmentLoras.advanced')} aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>
        {advanced ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
      </Button>
      <Button variant="ghost" size="icon" className="size-6" aria-label={t('segmentLoras.deleteRow', { n: number })} onClick={() => remove(rule.id)}><Trash2 className="size-3.5" /></Button>
    </div>
    <div className="grid grid-cols-2 gap-2 @min-[520px]:grid-cols-[minmax(0,1fr)_6rem_6rem_5rem]">
      <div className="min-w-0 space-y-1"><p className="text-xs text-muted-foreground">{t('segmentLoras.file')}</p>
        <FilePicker value={rule.lora} files={files} onChange={(lora) => patch({ lora })} /></div>
      <div className="min-w-0 space-y-1"><p className="text-xs text-muted-foreground">{t('segmentLoras.start')}</p>
        <NumberInput value={rule.start_segment} min={1} commitOnBlur onChange={(value) => patch({ start_segment: Math.round(value) })} className="h-8 w-full" aria-label={t('segmentLoras.start')} /></div>
      <div className="min-w-0 space-y-1"><p className="text-xs text-muted-foreground">{t('segmentLoras.count')}</p>
        <CountField value={rule.segment_count} onChange={(segment_count) => patch({ segment_count })} /></div>
      <div className="min-w-0 space-y-1"><p className="text-xs text-muted-foreground">{t('segmentLoras.strength')}</p>
        <NumberInput value={rule.strength} min={-Infinity} step={0.05} commitOnBlur onChange={(strength) => { if (Number.isFinite(strength)) patch({ strength }) }} className="h-8 w-full" aria-label={t('segmentLoras.strength')} /></div>
    </div>
    {advanced && <div className="mt-2 flex items-center gap-2 text-xs"><span>{t('segmentLoras.stage')}</span>
      <Select value={rule.stage} onValueChange={(stage: SegmentLoraRule['stage']) => patch({ stage })}>
        <SelectTrigger className="h-7 w-40 text-xs" aria-label={t('segmentLoras.stage')}><SelectValue /></SelectTrigger>
        <SelectContent>{(['all', 'first', 'second'] as const).map((stage) => <SelectItem key={stage} value={stage}>{t(`segmentLoras.${stage}`)}</SelectItem>)}</SelectContent>
      </Select>
    </div>}
    {!rule.lora && rule.enabled && <p className="mt-1 text-xs text-muted-foreground">{t('segmentLoras.incomplete')}</p>}
  </div>
}

export function H3SegmentLorasWidget({ value, onChange, node, app }: Readonly<ReactWidgetProps<SegmentLoraPlan>>) {
  const locale = app.ui?.settings?.settingsValues?.['Comfy.Locale']
  const t = (key: string, params?: Record<string, string | number>) => translate(locale, `segmentLoras.${key}`, params)
  const valid = value && value.version === 1 && Array.isArray(value.rules)
  const data = valid ? value : { version: 1 as const, rules: [] }
  const editor = useSegmentLoraEditor(data, onChange, node, app)
  const projects = useH3ProjectSnapshots(node)
  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState(false)
  const project = projects.find((item) => item.id === selected) ?? projects[0]
  const preview = useSegmentLoraPreview(app, project, expanded)
  const summary = preview.summary
  if (!valid) return <p className="p-3 text-xs text-destructive">{t('invalid')}</p>
  return <LocaleContext.Provider value={locale}>
    <div className="@container flex h-full min-h-0 flex-col gap-2 overflow-y-auto bg-background p-2 text-foreground"
      onPointerDown={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{t('description')}</span>
        <Button variant="ghost" size="icon" className="size-7 shrink-0" aria-label={t('refresh')} onClick={editor.refresh}><RefreshCw className="size-3.5" /></Button>
      </div>
      {editor.error && <p role="alert" className="text-xs text-destructive">{editor.error}</p>}
      {data.rules.map((rule, index) => {
        const usage = summary?.rules.find((item) => item.id === rule.id)
        const note = usage?.unused_reason ? t(usage.unused_reason) : usage ? t('range', { start: usage.start, end: usage.end }) : undefined
        return <RuleRow key={rule.id} rule={rule} number={index + 1} files={editor.files} note={note} update={editor.update} remove={editor.remove} />
      })}
      {!data.rules.length && <p className="py-3 text-center text-xs text-muted-foreground">{t('empty')}</p>}
      <Button variant="outline" size="sm" className="shrink-0 text-xs" onClick={editor.add}><Plus className="size-3.5" />{t('add')}</Button>
      <Button variant="ghost" size="sm" className="shrink-0 justify-start px-1 text-xs" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}{t('summary')}
      </Button>
      {expanded && <div className="space-y-2 text-xs">
        {!project && <p className="text-muted-foreground">{t('connect')}</p>}
        {projects.length > 1 && <Select value={project?.id} onValueChange={setSelected}>
          <SelectTrigger className="h-8 text-xs" aria-label={t('project')}><SelectValue /></SelectTrigger>
          <SelectContent>{projects.map((item) => <SelectItem key={item.id} value={item.id}>{item.name} · #{item.id}</SelectItem>)}</SelectContent>
        </Select>}
        {preview.error && <p role="alert" className="text-destructive">{preview.error}</p>}
        {project && <p className="text-muted-foreground">{preview.resolved ? t('resolved') : !project.project_snapshot.known || summary?.pending ? t('pending') : t('preview')}</p>}
        {summary?.conflicts.map((conflict, index) => <p key={index} role="alert" className="text-destructive">{t('conflict', { task: conflict.index + 1, rows: conflict.rows.join(', '), stage: t(conflict.stage) })}</p>)}
        {summary?.warnings?.map((warning, index) => <p key={index} role="status" className="text-muted-foreground">{t('upstreamDuplicate', { lora: warning.lora, tasks: warning.tasks.join(', '), stage: t(warning.stage) })}</p>)}
        {!!summary?.tasks.length && <div className="overflow-x-auto"><table className="w-full table-fixed text-left text-xs">
          <thead className="text-muted-foreground"><tr>{['task', 'first', 'second', 'run'].map((key) => <th key={key} className={cn('p-1 font-normal', key === 'task' && 'w-12', key === 'run' && 'w-20')}>{t(key)}</th>)}</tr></thead>
          <tbody>{summary.tasks.map((task) => <tr key={task.segment_id} className="border-t border-border align-top">
            <td className="p-1">{task.number}</td>
            {(['first', 'second'] as const).map((stage) => <td key={stage} className="break-all p-1">
              {task.stages[stage]?.map((rule) => `${rule.lora || t('incomplete')} × ${rule.strength}`).join(' + ') || (task.stages[stage] ? t('base') : '—')}
            </td>)}
            <td className="whitespace-nowrap p-1">{t(task.selected ? 'selected' : 'unselected')}</td>
          </tr>)}</tbody>
        </table></div>}
        <p className="text-muted-foreground">{t('upstream')}</p>
      </div>}
    </div>
  </LocaleContext.Provider>
}
