import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import zh from '../../messages/zh.json'
import { translate } from '@/lib/i18n'

function messageKeys(messages: Record<string, Record<string, string>>): string[] {
  return Object.entries(messages)
    .flatMap(([section, entries]) => Object.keys(entries).map((key) => `${section}.${key}`))
    .sort()
}

describe('message catalogs', () => {
  it('keeps the en and zh key sets aligned', () => {
    expect(messageKeys(zh)).toEqual(messageKeys(en))
  })
})

describe('multitrack audio lock translations', () => {
  it('explains video timing and audio-track priority in the video lock tooltip', () => {
    expect(translate('zh', 'multitrack.videoAudioLockTooltip')).toContain('视频轨道画面的时间线')
    expect(translate('zh', 'multitrack.videoAudioLockTooltip')).toContain('音频轨道将优先')
    expect(translate('en', 'multitrack.videoAudioLockTooltip')).toContain('audio track takes priority')
  })
})
