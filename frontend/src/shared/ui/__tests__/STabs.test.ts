import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import STabs from '../STabs.vue'

describe('STabs', () => {
  it('renders per-tab aria-label and a polite live badge when requested', () => {
    const wrapper = mount(STabs, {
      props: {
        modelValue: 'a',
        tabs: [
          { key: 'a', label: 'A' },
          { key: 'b', label: 'B', badge: 3, ariaLabel: 'B tab, 3 unread', badgeLive: true },
        ],
      },
    })
    const tabB = wrapper.findAll('[role="tab"]')[1]
    expect(tabB.attributes('aria-label')).toBe('B tab, 3 unread')
    const badge = wrapper.find('.s-tabs__badge')
    expect(badge.text()).toBe('3')
    // The polite live region is a persistent, visually-hidden sibling so
    // badge changes are announced (not the conditionally-rendered visible badge).
    const live = wrapper.find('.s-tabs__badge-live')
    expect(live.exists()).toBe(true)
    expect(live.attributes('aria-live')).toBe('polite')
    expect(live.text()).toBe('3')
  })

  it('omits aria attributes when the optional fields are unset (backward compatible)', () => {
    const wrapper = mount(STabs, {
      props: {
        modelValue: 'a',
        tabs: [
          { key: 'a', label: 'A' },
          { key: 'b', label: 'B', badge: 1 },
        ],
      },
    })
    const tabB = wrapper.findAll('[role="tab"]')[1]
    expect(tabB.attributes('aria-label')).toBeUndefined()
    expect(wrapper.find('.s-tabs__badge-live').exists()).toBe(false)
  })

  // AC-4: five of the six call sites sit in a scrolling page and must keep their
  // current auto-height behaviour. The modifier class is the whole difference, so
  // asserting its absence by default is what protects them.
  it('does not fill its parent height unless asked', () => {
    const wrapper = mount(STabs, {
      props: { modelValue: 'a', tabs: [{ key: 'a', label: 'A' }] },
    })
    expect(wrapper.find('.s-tabs').classes()).not.toContain('s-tabs--fill')
  })

  it('opts into filling its parent height', () => {
    const wrapper = mount(STabs, {
      props: { modelValue: 'a', tabs: [{ key: 'a', label: 'A' }], fill: true },
    })
    expect(wrapper.find('.s-tabs').classes()).toContain('s-tabs--fill')
  })
})
