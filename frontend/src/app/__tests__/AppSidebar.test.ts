// AC-1 (owner sees Manage group), AC-2 (non-owner / undecided hide it),
// AC-4/AC-5 (switcher in the sidebar on desktop only). useProjectRole is mocked
// so the role gate is controllable; the session + workspace stores are real.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import { i18n } from '@shared/i18n'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import type * as TenancySlice from '@slices/tenancy'
import { appRoutes } from '../../../tests/utils/routes'
import AppSidebar from '../components/AppSidebar.vue'

const role = vi.hoisted(() => ({ decided: true, isAuthorized: true }))
vi.mock('@slices/tenancy', async (importOriginal) => {
  const { computed } = await import('vue')
  return {
    ...(await importOriginal<typeof TenancySlice>()),
    useProjectRole: () => ({
      isAdmin: computed(() => false),
      isOwner: computed(() => role.isAuthorized),
      isAuthorized: computed(() => role.isAuthorized),
      decided: computed(() => role.decided),
    }),
  }
})

const SwitcherStub = { name: 'OrgProjectSwitcher', template: '<div data-testid="switcher-stub" />' }
const ChatroomListStub = { name: 'SidebarChatroomList', template: '<div />' }

async function mountSidebar() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setMe({ id: 'u1', is_admin: false } as never)
  useWorkspaceStore().selectProject('p1', 'P1')

  const router = createRouter({ history: createMemoryHistory(), routes: appRoutes })
  router.push('/projects/p1/agents')
  await router.isReady()

  return mount(AppSidebar, {
    global: {
      plugins: [pinia, router, i18n],
      stubs: { OrgProjectSwitcher: SwitcherStub, SidebarChatroomList: ChatroomListStub },
    },
  })
}

beforeEach(() => {
  role.decided = true
  role.isAuthorized = true
  window.innerWidth = 1280
})

describe('AppSidebar — Manage group', () => {
  it('shows members / skills / activity types to an owner (AC-1)', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/skills"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/activity-types"]').exists()).toBe(true)
  })

  it('hides the group entirely from a non-owner member (AC-2)', async () => {
    role.isAuthorized = false
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/projects/p1/skills"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/projects/p1/activity-types"]').exists()).toBe(false)
  })

  it('keeps the group hidden until the role is decided (AC-2 / R11.10)', async () => {
    role.decided = false
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(false)
  })
})

describe('AppSidebar — switcher placement', () => {
  it('renders the switcher in the sidebar on desktop (AC-4)', async () => {
    window.innerWidth = 1280
    const wrapper = await mountSidebar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(true)
  })

  it('omits the sidebar switcher on mobile (AC-5)', async () => {
    window.innerWidth = 375
    const wrapper = await mountSidebar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(false)
  })
})
