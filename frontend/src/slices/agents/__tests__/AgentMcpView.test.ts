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
  model_id: null,
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

function seed(
  bindings: unknown[],
  builtins: string[] = ['code_exec', 'web_search', 'file'],
): void {
  server.use(
    http.get('/api/agents/agent_1', () => HttpResponse.json(AGENT)),
    http.get('/api/agents/agent_1/mcp', () => HttpResponse.json(bindings)),
    http.get('/api/agents/agent_1/builtin-tools', () => HttpResponse.json({ enabled: builtins })),
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
  })

  it('renders the table and an add button when the agent has no bindings', async () => {
    seed([])
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    expect(wrapper.find('table.s-table').exists()).toBe(true)
    expect(wrapper.find('button.s-btn--primary').exists()).toBe(true)
  })

  it('reflects the server-reported enabled built-in tools', async () => {
    seed([], ['code_exec', 'web_search', 'file'])
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    for (const tool of ['code_exec', 'web_search', 'file']) {
      expect(wrapper.find(`#builtin-${tool}`).attributes('aria-checked')).toBe('true')
    }
  })

  it('shows only the server-enabled built-ins as on', async () => {
    seed([], ['web_search'])
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    expect(wrapper.find('#builtin-web_search').attributes('aria-checked')).toBe('true')
    expect(wrapper.find('#builtin-code_exec').attributes('aria-checked')).toBe('false')
    expect(wrapper.find('#builtin-file').attributes('aria-checked')).toBe('false')
  })

  it('PUTs the new enabled set when a built-in is toggled off', async () => {
    seed([], ['code_exec', 'web_search', 'file'])
    let putBody: { enabled: string[] } | null = null
    server.use(
      http.put('/api/agents/agent_1/builtin-tools', async ({ request }) => {
        putBody = (await request.json()) as { enabled: string[] }
        return HttpResponse.json({ enabled: putBody.enabled })
      }),
    )
    const wrapper = await renderView(AgentMcpView, {
      routes,
      initialRoute: '/agents/agent_1/mcp',
    })
    await settle(wrapper)
    // All on; turning code_exec off PUTs the remaining two — the server owns
    // how that maps to bindings.
    await wrapper.find('#builtin-code_exec').trigger('click')
    await settle(wrapper)
    expect(putBody).not.toBeNull()
    expect([...(putBody!.enabled)].sort()).toEqual(['file', 'web_search'])
  })
})
