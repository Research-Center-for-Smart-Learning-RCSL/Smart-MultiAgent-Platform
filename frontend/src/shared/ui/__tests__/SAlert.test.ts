import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import { i18n } from '@shared/i18n'
import SAlert from '../SAlert.vue'

function roleOf(variant: 'info' | 'success' | 'warning' | 'danger'): string | undefined {
  return mount(SAlert, {
    props: { variant, title: 'T' },
    global: { plugins: [i18n] },
  }).get('.s-alert').attributes('role')
}

describe('SAlert live-region politeness', () => {
  // docs/UI/11-responsive-a11y.md:293 maps the role from the variant. The
  // component hardcoded role="alert" on all four, so every static
  // informational panel announced assertively and pre-empted the page heading
  // on mount - three of them at once on /orgs/:id/transfer.
  it('interrupts for danger and warning', () => {
    expect(roleOf('danger')).toBe('alert')
    expect(roleOf('warning')).toBe('alert')
  })

  it('announces politely for info and success', () => {
    expect(roleOf('info')).toBe('status')
    expect(roleOf('success')).toBe('status')
  })

  // focusOnMount exists for transient submit errors, which are variant=danger;
  // it must not become a back door to assertive info panels.
  it('keeps the variant mapping when the alert takes focus on mount', () => {
    const wrapper = mount(SAlert, {
      props: { variant: 'info', title: 'T', focusOnMount: true },
      global: { plugins: [i18n] },
    })
    expect(wrapper.get('.s-alert').attributes('role')).toBe('status')
  })
})
