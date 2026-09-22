import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'

import { i18n } from '@shared/i18n'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import type * as TenancySlice from '@slices/tenancy'
import type * as ConversationSlice from '@slices/conversation'
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

vi.mock('@slices/conversation', async (importOriginal) => {
  const mod = await importOriginal<typeof ConversationSlice>()
  const vue = await import('vue')
  const { ref, computed } = vue
  return {
    ...mod,
    useChatroomCreate: () => ({
      showCreate: ref(false),
      createName: ref(''),
      createError: ref(null),
      createFlags: { allow_org_members: false, allow_project_members: true, allow_project_owners_only: false, allow_guest_links: false },
      openCreate: vi.fn(),
      submitCreate: vi.fn(),
      isCreating: computed(() => false),
    }),
    ChatroomCreateModal: { name: 'ChatroomCreateModal', template: '<div />' },
    listWorkspaces: vi.fn().mockResolvedValue([{ id: 'ws1', name: 'Default' }]),
  }
})

const SwitcherStub = { name: 'OrgProjectSwitcher', template: '<div data-testid="switcher-stub" />' }
const ChatroomListStub = { name: 'SidebarChatroomList', template: '<div data-testid="chatroom-list-stub" />' }

async function mountSidebar() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setMe({ id: 'u1', is_admin: false } as never)
  useWorkspaceStore().selectProject('p1', 'P1')

  const router = createRouter({ history: createMemoryHistory(), routes: appRoutes })
  router.push('/projects/p1/agents')
  await router.isReady()

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return mount(AppSidebar, {
    global: {
      plugins: [pinia, router, i18n, [VueQueryPlugin, { queryClient }]],
      stubs: { OrgProjectSwitcher: SwitcherStub, SidebarChatroomList: ChatroomListStub },
    },
  })
}

beforeEach(() => {
  role.decided = true
  role.isAuthorized = true
  window.innerWidth = 1280
})

describe('AppSidebar — New Chat button (AC-1)', () => {
  it('renders a New Chat button when a project is selected', async () => {
    const wrapper = await mountSidebar()
    const btn = wrapper.find('.new-chat-btn')
    expect(btn.exists()).toBe(true)
  })
})

describe('AppSidebar — chatroom list placement (AC-2)', () => {
  it('renders the chatroom list stub', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('[data-testid="chatroom-list-stub"]').exists()).toBe(true)
  })
})

describe('AppSidebar — Workspaces placement (AC-3)', () => {
  it('renders workspaces nav item', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/workspaces"]').exists()).toBe(true)
  })
})

describe('AppSidebar — Agent nav items (AC-4)', () => {
  it('renders AI Agents and Agent Groups', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/agents"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/agent-groups"]').exists()).toBe(true)
  })
})

describe('AppSidebar — Project Settings group (AC-5)', () => {
  it('renders Knowledge, Keys, and Manage sub-sections inside Project Settings', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/rag-configs"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/graphrag-configs"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/knowmap-configs"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/keys"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/search-keys"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/skills"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/activity-types"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/p1/mcp/egress-allowlist"]').exists()).toBe(true)
  })

  it('hides Manage sub-section from a non-owner (AC-5)', async () => {
    role.isAuthorized = false
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/projects/p1/skills"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/projects/p1/activity-types"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/projects/p1/mcp/egress-allowlist"]').exists()).toBe(false)
  })

  it('hides Manage until role is decided (AC-5 / R11.10)', async () => {
    role.decided = false
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/projects/p1/members"]').exists()).toBe(false)
  })
})

describe('AppSidebar — Global nav (AC-6)', () => {
  it('renders Organizations, Projects, and Invites', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/orgs"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/invites"]').exists()).toBe(true)
  })
})

describe('AppSidebar — removed items (AC-7)', () => {
  it('does not render My Keys or Notifications in the sidebar', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.find('a[href="/keys"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/notifications"]').exists()).toBe(false)
  })
})

describe('AppSidebar — aria-label (AC-9)', () => {
  it('has aria-label on the nav element', async () => {
    const wrapper = await mountSidebar()
    const nav = wrapper.find('nav')
    expect(nav.attributes('aria-label')).toBeTruthy()
  })
})

describe('AppSidebar — switcher placement', () => {
  it('renders the switcher on desktop', async () => {
    window.innerWidth = 1280
    const wrapper = await mountSidebar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(true)
  })

  it('omits the sidebar switcher on mobile', async () => {
    window.innerWidth = 375
    const wrapper = await mountSidebar()
    expect(wrapper.find('[data-testid="switcher-stub"]').exists()).toBe(false)
  })
})
