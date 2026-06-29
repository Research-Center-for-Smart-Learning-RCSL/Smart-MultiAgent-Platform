import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'
import SCharCount from '../SCharCount.vue'

function mountCount(props: { current: number; max: number; hideUntilNear?: boolean }) {
  return mount(SCharCount, { props, global: { plugins: [i18n] } })
}

describe('SCharCount', () => {
  it('renders the current/max count', () => {
    const w = mountCount({ current: 1234, max: 100000 })
    // Numbers are locale-formatted; assert the raw digits survive grouping.
    expect(w.text().replace(/\D/g, '')).toContain('1234')
    expect(w.text().replace(/\D/g, '')).toContain('100000')
  })

  it('is hidden until near the limit when hideUntilNear is set', () => {
    const w = mountCount({ current: 10, max: 100, hideUntilNear: true })
    expect(w.find('.s-char-count').exists()).toBe(false)
  })

  it('appears once within the warn ratio', () => {
    const w = mountCount({ current: 92, max: 100, hideUntilNear: true })
    expect(w.find('.s-char-count').exists()).toBe(true)
    expect(w.find('.s-char-count--warn').exists()).toBe(true)
  })

  it('switches to danger tone near the max', () => {
    const w = mountCount({ current: 100, max: 100 })
    expect(w.find('.s-char-count--danger').exists()).toBe(true)
  })

  it('is always shown without hideUntilNear', () => {
    const w = mountCount({ current: 0, max: 100 })
    expect(w.find('.s-char-count').exists()).toBe(true)
  })
})
