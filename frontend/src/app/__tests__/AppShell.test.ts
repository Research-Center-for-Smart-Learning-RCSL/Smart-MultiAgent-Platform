import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { i18n } from '@shared/i18n'
import { appRoutes } from '../../../tests/utils/routes'
import AppShell from '../layouts/AppShell.vue'

// A toggle is the only interaction we need from the top bar; stub it down to a
// button that re-emits the event so the test does not pull in the bar's stores
// and queries. The sidebar/drawer just need to be presence-detectable.
const AppTopBarStub = {
  name: 'AppTopBar',
  template: '<button class="toggle" @click="$emit(\'toggle-sidebar\')" />',
}
const AppSidebarStub = { name: 'AppSidebar', template: '<div class="sidebar-content" />' }

async function mountShell(initialRoute: string) {
  const pinia = createPinia()
  setActivePinia(pinia)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: appRoutes,
  })
  router.push(initialRoute)
  await router.isReady()

  return mount(AppShell, {
    global: {
      plugins: [pinia, router, i18n],
      stubs: {
        teleport: true,
        AppTopBar: AppTopBarStub,
        AppSidebar: AppSidebarStub,
        SDrawer: true,
      },
    },
  })
}

describe('AppShell sidebar toggle', () => {
  beforeEach(() => {
    // useBreakpoint reads window.innerWidth on mount; force the desktop branch
    // so the toggle drives the collapsed-state computed rather than the drawer.
    window.innerWidth = 1280
  })

  it('starts collapsed on an immersive route (chatroom)', async () => {
    const wrapper = await mountShell('/chatrooms/abc123')
    expect(wrapper.classes()).toContain('app-shell--sidebar-collapsed')
    expect(wrapper.find('aside.app-shell__sidebar').exists()).toBe(false)
  })

  // Regression: on immersive routes autoCollapsed is permanently true, so an
  // OR-based collapse computed left the toggle unable to ever expand the
  // sidebar — the hamburger looked dead. The manual override must win.
  it('expands when the user toggles on an immersive route', async () => {
    const wrapper = await mountShell('/chatrooms/abc123')

    await wrapper.find('button.toggle').trigger('click')

    expect(wrapper.classes()).not.toContain('app-shell--sidebar-collapsed')
    expect(wrapper.find('aside.app-shell__sidebar').exists()).toBe(true)
  })

  it('collapses again on a second toggle', async () => {
    const wrapper = await mountShell('/chatrooms/abc123')

    await wrapper.find('button.toggle').trigger('click')
    await wrapper.find('button.toggle').trigger('click')

    expect(wrapper.classes()).toContain('app-shell--sidebar-collapsed')
    expect(wrapper.find('aside.app-shell__sidebar').exists()).toBe(false)
  })

  it('resets the manual override on navigation', async () => {
    const wrapper = await mountShell('/chatrooms/abc123')

    await wrapper.find('button.toggle').trigger('click')
    expect(wrapper.classes()).not.toContain('app-shell--sidebar-collapsed')

    // Navigating to another immersive route drops the override; the route's
    // auto behavior (collapsed) takes over again.
    await wrapper.vm.$router.push('/chatrooms/def456')
    await wrapper.vm.$nextTick()

    expect(wrapper.classes()).toContain('app-shell--sidebar-collapsed')
  })
})
