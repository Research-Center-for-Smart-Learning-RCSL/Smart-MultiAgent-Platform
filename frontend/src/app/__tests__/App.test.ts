import { describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'

import { i18n } from '@shared/i18n'
import { useSessionStore } from '@shared/stores/session'
import App from '../App.vue'
import { router as appRouter } from '../router'

const AppTopBarStub = { name: 'AppTopBar', template: '<header class="topbar-stub" />' }
const AppSidebarStub = { name: 'AppSidebar', template: '<div class="sidebar-stub" />' }

// A view that throws during render, to drive the error boundary.
const ThrowingView = {
  name: 'ThrowingView',
  setup() {
    return () => {
      throw new Error('render exploded')
    }
  },
}

const OK_VIEW = { template: '<div class="ok-view" />' }

function testRoutes(): RouteRecordRaw[] {
  return [
    { path: '/', name: 'root', component: OK_VIEW, meta: { layout: 'public' } },
    { path: '/app', name: 'app', component: OK_VIEW, meta: { requiresAuth: true } },
    { path: '/boom', name: 'boom', component: ThrowingView, meta: { requiresAuth: true } },
    // Mirrors the real catch-all record; the record itself is pinned separately
    // below, so this fixture cannot drift into passing on its own terms.
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: OK_VIEW,
      meta: { layout: 'auto' },
    },
  ]
}

async function mountApp(path: string, authenticated: boolean): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)

  const session = useSessionStore()
  if (authenticated) {
    // Set before mount: useBanKickGuard's watcher has no `immediate`, so an
    // already-populated session never opens its per-user socket here.
    session.me = {
      id: 'u-1',
      email: 'a@b.test',
      email_verified: true,
      is_admin: false,
      status: 'active',
      display_name: null,
    }
  }

  const router = createRouter({ history: createMemoryHistory(), routes: testRoutes() })
  router.push(path)
  await router.isReady()

  return mount(App, {
    global: {
      plugins: [pinia, router, i18n],
      stubs: {
        teleport: true,
        AppTopBar: AppTopBarStub,
        AppSidebar: AppSidebarStub,
        SDrawer: true,
        Toaster: true,
        SConfirmDialog: true,
        SIdleDialog: true,
        SNetworkBanner: true,
        ImpersonationBanner: { name: 'ImpersonationBanner', template: '<div class="banner-stub" />' },
      },
    },
  })
}

describe('App shell composition', () => {
  // F-5: the banner must share a flow container with the layout so it can
  // reserve its own height. The teleported siblings stay outside it; they take
  // no part in flow and the wrapper would only constrain them.
  it('puts the impersonation banner and the layout in one flow wrapper', async () => {
    const wrapper = await mountApp('/app', true)

    const root = wrapper.find('.app-root')
    expect(root.exists()).toBe(true)
    expect(root.find('.banner-stub').exists()).toBe(true)
    expect(root.find('.app-shell').exists()).toBe(true)
    expect(root.find('toaster-stub').exists()).toBe(false)
    expect(root.find('s-confirm-dialog-stub').exists()).toBe(false)
    expect(root.find('s-idle-dialog-stub').exists()).toBe(false)
  })

  // The network banner used to sit outside .app-root, and its below-topbar
  // offset was pure arithmetic over --topbar-height-total - which knows nothing
  // about the impersonation banner displacing the top bar. Inside the
  // zero-height anchor, "below the top bar" is measured from where the
  // impersonation banner ENDS, so neither file has to know the other's height.
  // A non-transformed ancestor does not constrain a fixed child, so the
  // unauthenticated mode is unaffected by the move.
  it('anchors the network banner below the impersonation banner, in flow', async () => {
    const wrapper = await mountApp('/app', true)

    const anchor = wrapper.find('.app-root > .app-root__overlay-anchor')
    expect(anchor.exists()).toBe(true)
    expect(anchor.find('s-network-banner-stub').exists()).toBe(true)

    // Order is the whole mechanism: an anchor placed BEFORE the impersonation
    // banner would be back at y = 0 and measure nothing.
    const children = [...wrapper.get('.app-root').element.children].map((el) => el.className)
    expect(children.indexOf('banner-stub')).toBeLessThan(
      children.indexOf('app-root__overlay-anchor'),
    )
  })

  // F-6: the boundary's own docstring promises a fallback "keeping the rest of
  // the shell alive". Wrapping the layout made the fallback replace it.
  it('keeps the shell mounted when a view throws during render', async () => {
    const wrapper = await mountApp('/boom', true)

    expect(wrapper.find('.error-boundary').exists()).toBe(true)
    expect(wrapper.find('.app-shell').exists()).toBe(true)
    expect(wrapper.find('.topbar-stub').exists()).toBe(true)
    expect(wrapper.find('.sidebar-stub').exists()).toBe(true)
  })

  // F-7: a `layout: 'auto'` route follows the session, per
  // docs/UI/02-layout-shell.md:351.
  it('gives an authenticated visitor the app shell on an unknown URL', async () => {
    const wrapper = await mountApp('/no-such-section/typo', true)

    expect(wrapper.find('.app-shell').exists()).toBe(true)
    expect(wrapper.find('.auth-layout').exists()).toBe(false)
  })

  it('gives an anonymous visitor the auth layout on an unknown URL', async () => {
    const wrapper = await mountApp('/no-such-section/typo', false)

    expect(wrapper.find('.auth-layout').exists()).toBe(true)
    expect(wrapper.find('.app-shell').exists()).toBe(false)
  })

  // The fixture above declares the meta itself, so this pins the production
  // record: without it the fixture would keep passing while the app regressed.
  it('declares the auto layout on the real catch-all route', () => {
    expect(appRouter.resolve('/no-such-section/typo').meta.layout).toBe('auto')
  })
})
