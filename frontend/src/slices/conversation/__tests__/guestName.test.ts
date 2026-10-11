// The guest form refuses exactly the names `normalise_label` refuses
// (docs/tasks/2026-10-07-display-name-validation, AC-3). Invisible code points
// are written as escapes: an editor or paste can strip them silently.
import { describe, expect, it } from 'vitest'

import { isUsableGuestName, normaliseGuestName } from '../utils/guestName'

describe('isUsableGuestName', () => {
  it.each([
    ['HANGUL FILLER', 'ㅤ'],
    ['HALFWIDTH HANGUL FILLER', 'ﾠ'],
    ['BRAILLE PATTERN BLANK', '⠀'],
    ['a lone combining mark', '́'],
    ['combining marks only', '̣́̈'],
    ['a lone ZERO WIDTH JOINER', '‍'],
    ['a lone VARIATION SELECTOR-16', '️'],
    ['joiners and selectors only', '‍️‍'],
    ['fillers between spaces', ' ㅤ ⠀ '],
    ['an ideographic space', '　'],
  ])('refuses %s', (_label, raw) => {
    expect(isUsableGuestName(raw)).toBe(false)
  })

  it('accepts a name with a visible character', () => {
    expect(isUsableGuestName('柯老師')).toBe(true)
    expect(isUsableGuestName('é')).toBe(true)
    expect(isUsableGuestName('❤️')).toBe(true)
  })
})

describe('normaliseGuestName', () => {
  it('strips a filler inside a name', () => {
    expect(normaliseGuestName('Alㅤice')).toBe('Alice')
  })

  it('keeps an emoji ZWJ sequence whole', () => {
    const family = '\u{1F468}‍\u{1F469}‍\u{1F467}'
    expect(normaliseGuestName(family)).toBe(family)
  })
})
