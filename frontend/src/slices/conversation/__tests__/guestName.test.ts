// The guest form refuses exactly the names `normalise_label` refuses
// (docs/tasks/2026-10-07-display-name-validation, AC-3). Invisible code points
// are written as escapes: an editor or paste can strip them silently.
import { describe, expect, it } from 'vitest'

import { isUsableGuestName, normaliseGuestName } from '../utils/guestName'

describe('isUsableGuestName', () => {
  it.each([
    ['HANGUL FILLER', '\u3164'],
    ['HALFWIDTH HANGUL FILLER', '\uFFA0'],
    ['BRAILLE PATTERN BLANK', '\u2800'],
    ['a lone combining mark', '\u0301'],
    ['combining marks only', '\u0301\u0308\u0323'],
    ['a lone ZERO WIDTH JOINER', '\u200D'],
    ['a lone VARIATION SELECTOR-16', '\uFE0F'],
    ['joiners and selectors only', '\u200D\uFE0F\u200D'],
    ['fillers between spaces', ' \u3164 \u2800 '],
    ['an ideographic space', '\u3000'],
  ])('refuses %s', (_label, raw) => {
    expect(isUsableGuestName(raw)).toBe(false)
  })

  it('accepts a name with a visible character', () => {
    expect(isUsableGuestName('柯老師')).toBe(true)
    expect(isUsableGuestName('e\u0301')).toBe(true)
    expect(isUsableGuestName('❤\uFE0F')).toBe(true)
  })
})

describe('normaliseGuestName', () => {
  it('strips a filler inside a name', () => {
    expect(normaliseGuestName('Al\u3164ice')).toBe('Alice')
  })

  it('keeps an emoji ZWJ sequence whole', () => {
    const family = '\u{1F468}\u200D\u{1F469}\u200D\u{1F467}'
    expect(normaliseGuestName(family)).toBe(family)
  })
})
