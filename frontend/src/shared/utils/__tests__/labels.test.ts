// The client mirror of backend/shared_kernel/labels.py
// (docs/tasks/2026-10-07-display-name-validation, AC-3 and the code review's
// agent-form finding). Invisible code points are written as escapes.
import { describe, expect, it } from 'vitest'

import { isUsableLabel, isUsableModelLabel, normaliseLabel } from '../labels'

describe('isUsableModelLabel', () => {
  it.each([
    ['quotes around spaces', '"  "'],
    ['brackets around a space', '[ ]'],
    ['quotes around a blank cell', '"\u2800"'],
    ['a filler', '\u3164'],
  ])('refuses %s', (_label, raw) => {
    expect(isUsableModelLabel(raw)).toBe(false)
  })

  it('accepts a name that keeps a visible character', () => {
    expect(isUsableModelLabel('[TA] Helper')).toBe(true)
    expect(isUsableModelLabel('柯老師')).toBe(true)
  })

  it('is stricter than the human rule only on delimiters', () => {
    expect(isUsableLabel('"  "')).toBe(true)
    expect(isUsableModelLabel('"  "')).toBe(false)
  })
})

describe('normaliseLabel', () => {
  it('strips an override and a filler', () => {
    expect(normaliseLabel('Bob\u202ETe\u3164acher')).toBe('BobTeacher')
  })
})
