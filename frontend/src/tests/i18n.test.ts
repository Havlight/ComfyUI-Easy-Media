import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import zh from '../../messages/zh.json'
import { translate } from '@/lib/i18n'

type Messages = Record<string, Record<string, string>>

const SRC_DIR = resolve(__dirname, '..')
// `t('section.key')` from useT() and `translate(locale, 'section.key')`; dynamic keys are skipped.
const LITERAL_KEY_PATTERNS = [
  /\bt\(\s*(['"`])(\w+\.[\w.]+)\1/g,
  /\btranslate\([^,()]*,\s*(['"`])(\w+\.[\w.]+)\1/g,
]

function messageKeys(messages: Messages): string[] {
  return Object.entries(messages)
    .flatMap(([section, entries]) => Object.keys(entries).map((key) => `${section}.${key}`))
    .sort()
}

function hasMessage(messages: Messages, path: string): boolean {
  const dot = path.indexOf('.')
  return messages[path.slice(0, dot)]?.[path.slice(dot + 1)] !== undefined
}

function referencedMessageKeys(): Map<string, string[]> {
  const references = new Map<string, string[]>()
  for (const file of readdirSync(SRC_DIR, { recursive: true, encoding: 'utf8' })) {
    if (!/\.tsx?$/.test(file) || file.split(/[\\/]/)[0] === 'tests') continue
    const source = readFileSync(join(SRC_DIR, file), 'utf8')
    for (const pattern of LITERAL_KEY_PATTERNS) {
      for (const [, , path] of source.matchAll(pattern)) {
        const files = references.get(path) ?? []
        if (!files.includes(file)) files.push(file)
        references.set(path, files)
      }
    }
  }
  return references
}

describe('message catalogs', () => {
  it('keeps the en and zh key sets aligned', () => {
    expect(messageKeys(zh)).toEqual(messageKeys(en))
  })
})

describe('literal message key references', () => {
  const references = referencedMessageKeys()

  it('scans component sources for literal keys', () => {
    expect([...references.keys()]).toEqual(expect.arrayContaining([
      'mediaSelector.selectFile',
      'mediaSelector.cancelFilter',
      'multitrack.reselectImage',
    ]))
  })

  it.each([['en', en], ['zh', zh]] as const)('defines every literal key in %s', (_locale, messages) => {
    const missing = [...references]
      .filter(([path]) => !hasMessage(messages, path))
      .map(([path, files]) => `${path} (${files.join(', ')})`)
    expect(missing).toEqual([])
  })
})

describe('multitrack audio lock translations', () => {
  it('explains video timing and audio-track priority in the video lock tooltip', () => {
    expect(translate('zh', 'multitrack.videoAudioLockTooltip')).toContain('视频轨道画面的时间线')
    expect(translate('zh', 'multitrack.videoAudioLockTooltip')).toContain('音频轨道将优先')
    expect(translate('en', 'multitrack.videoAudioLockTooltip')).toContain('audio track takes priority')
  })
})
