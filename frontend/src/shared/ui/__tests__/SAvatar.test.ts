import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import SAvatar from '../SAvatar.vue'

function initialOf(name: string): string {
  return mount(SAvatar, { props: { name } }).get('.s-avatar__initials').text()
}

// Names keep emoji on purpose (backend/shared_kernel/labels.py), and
// `charAt(0)` took half of a surrogate pair, rendering a replacement glyph.
describe('SAvatar initial', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('upper-cases a letter', () => {
    expect(initialOf('alice')).toBe('A')
  })

  it('takes a whole emoji, not half of its surrogate pair', () => {
    expect(initialOf('🦊 Fox')).toBe('🦊')
  })

  it('takes a whole joined emoji sequence', () => {
    expect(initialOf('👩‍🏫 Teacher')).toBe('👩‍🏫')
  })

  it('takes a whole astral CJK character', () => {
    expect(initialOf('𠮷野')).toBe('𠮷')
  })

  it('is empty for an empty name', () => {
    expect(initialOf('')).toBe('')
  })

  it('still takes a whole code point where Intl.Segmenter is missing', () => {
    vi.stubGlobal('Intl', { ...Intl, Segmenter: undefined })
    expect(initialOf('🦊 Fox')).toBe('🦊')
  })
})
