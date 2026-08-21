import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SDrawer from '../SDrawer.vue'

// T-3 of docs/tasks/2026-08-19-mobile-viewport-and-breakpoints (F-42).
//
// The width itself lives in a scoped stylesheet, which jsdom will not compute
// and this tier cannot read — that assertion belongs to the source scan in
// app/__tests__/mobileViewportContract.test.ts and to AC-10 in the e2e tier at
// 320x568. What this file pins is the wiring the width hangs off: that a
// `size` prop reaches the panel as the modifier class the stylesheet targets,
// so a renamed or dropped class turns a test red rather than silently
// restoring the 320px default.

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: {} } })

function mountDrawer(props: Record<string, unknown> = {}): VueWrapper {
  return mount(SDrawer, {
    props: { open: true, ...props },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

function panel(wrapper: VueWrapper) {
  return wrapper.get('.s-drawer__panel')
}

describe('SDrawer', () => {
  it('applies the sm modifier the sidebar drawer is sized by', () => {
    // AppShell renders the mobile sidebar as <SDrawer size="sm">, and `--sm`
    // is the only size the responsive block does not otherwise narrow.
    const wrapper = mountDrawer({ size: 'sm' })

    expect(panel(wrapper).classes()).toContain('s-drawer__panel--sm')
    expect(panel(wrapper).classes()).not.toContain('s-drawer__panel--md')
  })

  it('defaults to md, so the sm width reaches only the consumers that ask', () => {
    const wrapper = mountDrawer()

    expect(panel(wrapper).classes()).toContain('s-drawer__panel--md')
  })

  it('carries the side modifier independently of size', () => {
    const wrapper = mountDrawer({ size: 'sm', side: 'left' })

    expect(panel(wrapper).classes()).toContain('s-drawer__panel--left')
    expect(panel(wrapper).classes()).toContain('s-drawer__panel--sm')
  })

  it('is a modal dialog, which is why it keeps --z-modal rather than --z-sidebar', () => {
    // Q-11 decided against lowering the drawer to --z-sidebar (100): at that
    // depth it would paint below the top bar (--z-topbar, 200), which is
    // exactly what its own backdrop exists to cover. The doc was corrected
    // instead. These are the properties that make it modal.
    const wrapper = mountDrawer({ size: 'sm' })

    expect(panel(wrapper).attributes('role')).toBe('dialog')
    expect(panel(wrapper).attributes('aria-modal')).toBe('true')
    expect(wrapper.find('.s-drawer__backdrop').exists()).toBe(true)
  })

  it('renders nothing while closed', () => {
    const wrapper = mountDrawer({ open: false })

    expect(wrapper.find('.s-drawer__panel').exists()).toBe(false)
  })
})
