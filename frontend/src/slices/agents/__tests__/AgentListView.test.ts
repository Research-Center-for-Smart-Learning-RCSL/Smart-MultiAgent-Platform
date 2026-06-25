import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentListView from '../views/AgentListView.vue'

const routes = [
  { path: '/projects/:projectId/agents', name: 'agents.list', component: AgentListView },
  { path: '/agents/:agentId', name: 'agents.detail', component: { template: '<div />' } },
]

const AGENT = {
  id: 'agent_1',
  project_id: 'proj_1',
  name: 'Researcher',
  model_hint: 'claude',
  model_id: null,
  key_group_id: 'kg_1',
  system_prompt: '',
  prompt_strategy: 'full',
  rag_config_id: null,
  graphrag_config_id: null,
  context_mode: 'general',
  context_token_cap: null,
  a2a_enabled: true,
  wakeup_config: {},
  workflow_capabilities: {},
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

function seed(agents: unknown[]): void {
  server.use(
    http.get('/api/projects/proj_1/agents', () => HttpResponse.json(agents)),
    http.get('/api/projects/proj_1/key-groups', () =>
      HttpResponse.json([
        { id: 'kg_1', project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
    http.get('/api/projects/proj_1/rag-configs', () => HttpResponse.json([])),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('AgentListView', () => {
  it('lists the project agents fetched from the backend', async () => {
    seed([AGENT])
    const wrapper = await renderView(AgentListView, {
      routes,
      initialRoute: '/projects/proj_1/agents',
    })
    await settle(wrapper)
    expect(wrapper.find('table.s-table').exists()).toBe(true)
    expect(wrapper.text()).toContain('Researcher')
  })

  it('renders an empty state and an enabled create button when there are no agents', async () => {
    seed([])
    const wrapper = await renderView(AgentListView, {
      routes,
      initialRoute: '/projects/proj_1/agents',
    })
    await settle(wrapper)
    // The header create action is the sole primary button on the list.
    const createBtn = wrapper.find('button.s-btn--primary')
    expect(createBtn.exists()).toBe(true)
    expect(createBtn.attributes('disabled')).toBeUndefined()
  })
})
