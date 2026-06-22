import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentMcpView from '../views/AgentMcpView.vue'

const routes = [
  {
    path: '/agents/:agentId/mcp',
    name: 'agents.mcp',
    component: AgentMcpView,
  },
  {
    path: '/projects/:projectId/mcp/egress-allowlist',
    name: 'agents.egressAllowlist',
    component: { template: '<div />' },
  },
]

const AGENT = {
  id: 'agent_1',
  project_id: 'proj_1',
  name: 'Researcher',
  model_hint: 'claude',
  key_group_id: 'kg_1',
  system_prompt: '',
  prompt_strategy: 'full',
  rag_config_id: null,
  graphrag_config_id: null,
  context_mode: 'general',
  context_token_cap: null,
  a2a_enabled: false,
  wakeup_config: {},
  workflow_capabilities: {},
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

function seed(bindings: unknown[]): void {
  server.use(
    http.get('/api/agents/agent_1', () => HttpResponse.json(AGENT)),
    http.get('/api/agents/agent_1/mcp', () => HttpResponse.json(bindings)),
  )
}

const BINDING = {
  id: 'mcp_1',
  agent_id: 'agent_1',
  source: 'url',
  reference: 'https://mcp.example.com/sse',
  allowed_tools: ['search', 'fetch'],
  config: {},
  created_at: '2026-01-01T00:00:00Z',
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('AgentMcpView', () => {
  it('lists the agent MCP bindings fetched from the backend', async () => {
    seed([BINDING])
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('https://mcp.example.com/sse')
    expect(wrapper.text()).toContain('search, fetch')
  })

  it('renders the empty state when the agent has no bindings', async () => {
    seed([])
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    expect(wrapper.find('.table').exists()).toBe(true)
    expect(wrapper.find('header .btn-primary').exists()).toBe(true)
  })
})
