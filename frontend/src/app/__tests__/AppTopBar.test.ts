// AC-4 / AC-5 (top-bar side): the project switcher stays in the top bar on
// mobile and is gone on desktop (where it lives in the sidebar instead).

import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import { i18n } from '@shared/i18n'
import { appRoutes } from '../../../tests/utils/routes'
import AppTopBar from '../components/AppTopBar.vue'

const SwitcherStub = { name: 'OrgProjectSwitcher', template: '<div data-testid="switcher-stub" />' }

async function mountTopBar() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: appRoutes })
  router.push('/')
  await router.isReady()

  return mount(AppTopBar, {
    props: { sidebarOpen: false },
    global: {
      plugins: [pinia, router, i18n],
      stubs: {
        OrgProjectSwitcher: SwitcherStub,
        NotificationBell: true,
        UserMenu: true,
        LocaleToggle: true,
        ThemeToggle: true,
      },
    },
  })
}

beforeEach(() => {
  window.innerWidth = 1280
})

describe('AppTopBar — switcher', () => {
  it('shows the switcher in the top bar on mobile (AC-5)', async () => {
    window.innerWidth = 375
    const wrapper = await mountTopBar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(true)
  })

  it('omits the top-bar switcher on desktop (AC-4)', async () => {
    window.innerWidth = 1280
    const wrapper = await mountTopBar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(false)
  })
})
