import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import AgentGroupDetailView from '../views/AgentGroupDetailView.vue'

const routes = [
  { path: '/agent-groups/:groupId', name: 'agentGroups.detail', component: AgentGroupDetailView },
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

const GROUP = {
  id: 'g_1',
  project_id: 'proj_1',
  name: 'Research Team',
  concept_map_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
}

const AGENTS = [
  { id: 'a_1', project_id: 'proj_1', name: 'Alice', key_group_id: 'kg_1' },
  { id: 'a_2', project_id: 'proj_1', name: 'Bob', key_group_id: 'kg_1' },
]

function signInAs(userId: string): void {
  const session = useSessionStore()
  session.me = { id: userId, email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
}

function seed(role: 'owner' | 'member', members: string[] = ['a_1']): void {
  server.use(
    http.get('/api/agent-groups/g_1', () => HttpResponse.json(GROUP)),
    http.get('/api/agent-groups/g_1/members', () => HttpResponse.json({ members })),
    http.get('/api/projects/proj_1/agents', () => HttpResponse.json(AGENTS)),
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([{ user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' }]),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

describe('AgentGroupDetailView', () => {
  it('resolves member agent names', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    expect(wrapper.text()).toContain('Research Team')
    expect(wrapper.text()).toContain('Alice')
  })

  it('shows member add form + privacy toggle to an owner', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    // The add-member SSelect (a native select) is present for owners.
    expect(wrapper.find('.s-select__native').exists()).toBe(true)
    // The privacy toggle (a switch/checkbox input) is present.
    expect(wrapper.find('.s-toggle, input[type="checkbox"]').exists()).toBe(true)
  })

  it('hides mutating controls from a non-owner', async () => {
    seed('member')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    expect(wrapper.text()).toContain('agentGroups.detail.ownerOnly')
    expect(wrapper.text()).toContain('agentGroups.detail.privacyOwnerOnly')
  })
})
