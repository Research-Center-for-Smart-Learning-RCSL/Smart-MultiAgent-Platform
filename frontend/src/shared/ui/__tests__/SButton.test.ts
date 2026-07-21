import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import SButton from '../SButton.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/members', name: 'members', component: { template: '<div />' } },
  ],
})

describe('SButton', () => {
  // Regression: `:href` was bound unconditionally on the dynamic <component>,
  // so for as="router-link" it fell through to RouterLink as href=undefined.
  // A fallthrough attr wins over what the component renders, which stripped
  // the href RouterLink had computed — every router-link button in the app
  // rendered an anchor with no href, so it had no link role and could not be
  // middle-clicked or opened in a new tab.
  it('renders a real href for as="router-link"', async () => {
    router.push('/')
    await router.isReady()

    const w = mount(SButton, {
      props: { as: 'router-link', to: { name: 'members' } },
      global: { plugins: [router] },
    })

    const a = w.find('a')
    expect(a.exists()).toBe(true)
    expect(a.attributes('href')).toBe('/members')
  })

  it('renders href from `to` for as="a" and omits router-link props', () => {
    const w = mount(SButton, { props: { as: 'a', to: 'https://example.com' } })
    const a = w.find('a')
    expect(a.attributes('href')).toBe('https://example.com')
    expect(a.attributes('to')).toBeUndefined()
  })

  it('keeps type/disabled on a plain button and adds no href', () => {
    const w = mount(SButton, { props: { type: 'submit', disabled: true } })
    const btn = w.find('button')
    expect(btn.attributes('type')).toBe('submit')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('href')).toBeUndefined()
  })

  it('marks a disabled router-link with aria-disabled rather than the disabled attr', async () => {
    router.push('/')
    await router.isReady()

    const w = mount(SButton, {
      props: { as: 'router-link', to: { name: 'members' }, disabled: true },
      global: { plugins: [router] },
    })

    const a = w.find('a')
    expect(a.attributes('aria-disabled')).toBe('true')
    expect(a.attributes('disabled')).toBeUndefined()
  })
})
