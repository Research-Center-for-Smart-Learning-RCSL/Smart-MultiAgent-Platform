/* eslint-disable vue/one-component-per-file, vue/require-default-prop -- Inline test doubles keep the host-prop contract visible. */
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { i18n } from '@shared/i18n'
import en from '@app/locales/en.json'
import zhTW from '@app/locales/zh-TW.json'

vi.mock('@shared/composables', () => ({ useBanKickGuard: vi.fn() }))
vi.mock('../ErrorBoundary.vue', async () => {
  const { defineComponent } = await import('vue')
  return { default: defineComponent({ name: 'ErrorBoundary', template: '<div><slot /></div>' }) }
})
vi.mock('../layouts/AuthLayout.vue', async () => {
  const { defineComponent } = await import('vue')
  return { default: defineComponent({ name: 'AuthLayout', template: '<div><slot /></div>' }) }
})
vi.mock('../layouts/AppShell.vue', async () => {
  const { defineComponent } = await import('vue')
  return { default: defineComponent({ name: 'AppShell', template: '<div><slot /></div>' }) }
})
vi.mock('../layouts/PublicLayout.vue', async () => {
  const { defineComponent } = await import('vue')
  return { default: defineComponent({ name: 'PublicLayout', template: '<div><slot /></div>' }) }
})
vi.mock('@slices/admin', async () => {
  const { defineComponent } = await import('vue')
  return { ImpersonationBanner: defineComponent({ name: 'ImpersonationBanner', template: '<div />' }) }
})
vi.mock('@shared/ui', async () => {
  const { defineComponent } = await import('vue')
  return {
    SConfirmDialog: defineComponent({ name: 'SConfirmDialog', template: '<div />' }),
    SIdleDialog: defineComponent({ name: 'SIdleDialog', template: '<div />' }),
    SNetworkBanner: defineComponent({
      name: 'SNetworkBanner',
      props: { belowTopbar: Boolean },
      template: '<div />',
    }),
  }
})
vi.mock('vue-sonner', async () => {
  const { defineComponent } = await import('vue')
  return {
    Toaster: defineComponent({
      name: 'Toaster',
      props: {
        position: String,
        duration: Number,
        containerAriaLabel: String,
        toastOptions: Object,
      },
      template: '<div />',
    }),
  }
})

import App from '../App.vue'

async function renderApp(path: string, locale: 'en' | 'zh-TW') {
  // App.vue reads the session store to resolve the 'auto' layout, so a pinia
  // must be active even for the three layouts that never consult it.
  const pinia = createPinia()
  setActivePinia(pinia)
  i18n.global.mergeLocaleMessage('en', en)
  i18n.global.mergeLocaleMessage('zh-TW', zhTW)
  i18n.global.locale.value = locale
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app', component: { template: '<div />' }, meta: { layout: 'app' } },
      { path: '/login', component: { template: '<div />' }, meta: { layout: 'auth' } },
      { path: '/public', component: { template: '<div />' }, meta: { layout: 'public' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return shallowMount(App, { global: { plugins: [pinia, router, i18n] } })
}

describe('App feedback hosts', () => {
  it.each([
    ['en', 'Notifications', 'Close notification'],
    ['zh-TW', '通知', '關閉通知'],
  ] as const)('passes translated toaster labels in %s', async (locale, container, close) => {
    const wrapper = await renderApp('/app', locale)
    const toaster = wrapper.getComponent({ name: 'Toaster' })
    expect(toaster.props('containerAriaLabel')).toBe(container)
    expect(toaster.props('toastOptions')).toEqual({ closeButtonAriaLabel: close })
  })

  it('requests the network top-bar offset only for the app layout', async () => {
    const app = await renderApp('/app', 'en')
    expect(app.getComponent({ name: 'SNetworkBanner' }).props('belowTopbar')).toBe(true)
    const auth = await renderApp('/login', 'en')
    expect(auth.getComponent({ name: 'SNetworkBanner' }).props('belowTopbar')).toBe(false)
    const publicPage = await renderApp('/public', 'en')
    expect(publicPage.getComponent({ name: 'SNetworkBanner' }).props('belowTopbar')).toBe(false)
  })
})
