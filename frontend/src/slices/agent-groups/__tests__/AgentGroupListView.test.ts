import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import AgentGroupListView from '../views/AgentGroupListView.vue'

const routes = [
  { path: '/projects/:projectId/agent-groups', name: 'agentGroups.list', component: AgentGroupListView },
  { path: '/agent-groups/:groupId', name: 'agentGroups.detail', component: { template: '<div />' } },
]

const GROUP = {
  id: 'g_1',
  project_id: 'proj_1',
  name: 'Research Team',
  concept_map_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
}

function signInAs(userId: string): void {
  const session = useSessionStore()
  session.me = { id: userId, email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
}

function seed(role: 'owner' | 'member', groups: unknown[] = [GROUP]): void {
  server.use(
    http.get('/api/projects/proj_1/agent-groups', () => HttpResponse.json(groups)),
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([{ user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' }]),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 120))
  await wrapper.vm.$nextTick()
}

describe('AgentGroupListView', () => {
  it('lists the project agent groups', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupListView, { routes, initialRoute: '/projects/proj_1/agent-groups' })
    signInAs('u_1')
    await settle(wrapper)
    expect(wrapper.text()).toContain('Research Team')
  })

  it('enables the create control for a project owner', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupListView, { routes, initialRoute: '/projects/proj_1/agent-groups' })
    signInAs('u_1')
    await settle(wrapper)
    const createBtn = wrapper.find('button.s-btn--primary')
    expect(createBtn.exists()).toBe(true)
    expect((createBtn.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('renders but disables the create control for a non-owner, and shows the note', async () => {
    // Hardened pattern: the control is never v-if-hidden (so a real owner never
    // sees it flash in) but stays disabled until authorization resolves — a
    // non-owner gets an inert button, not a hidden one.
    seed('member')
    const wrapper = await renderView(AgentGroupListView, { routes, initialRoute: '/projects/proj_1/agent-groups' })
    signInAs('u_1')
    await settle(wrapper)
    const createBtn = wrapper.find('button.s-btn--primary')
    expect(createBtn.exists()).toBe(true)
    expect((createBtn.element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('agentGroups.list.ownerOnly')
  })
})
