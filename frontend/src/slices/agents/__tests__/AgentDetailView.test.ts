import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentDetailView from '../views/AgentDetailView.vue'

const routes = [
  { path: '/agents/:agentId', name: 'agents.detail', component: AgentDetailView },
  { path: '/projects/:projectId/agents', name: 'agents.list', component: { template: '<div />' } },
  { path: '/agents/:agentId/mcp', name: 'agents.mcp', component: { template: '<div />' } },
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: { template: '<div />' },
  },
  {
    path: '/projects/:projectId/rag-configs/:configId',
    name: 'agents.ragConfig',
    component: { template: '<div />' },
  },
]

const AGENT = {
  id: 'agent_1',
  project_id: 'proj_1',
  name: 'My Bot',
  model_hint: 'openai',
  model_id: null,
  effort: null,
  key_group_id: 'kg_1',
  system_prompt: 'You are helpful.',
  rag_config_id: null,
  context_mode: 'general',
  context_token_cap: null,
  temperature: null,
  top_p: null,
  seed: null,
  a2a_enabled: false,
  wakeup_config: {},
  workflow_capabilities: {},
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

function seed(): void {
  server.use(
    http.get('/api/agents/agent_1', () => HttpResponse.json(AGENT)),
    http.get('/api/projects/proj_1/key-groups', () =>
      HttpResponse.json([
        { id: 'kg_1', project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
    http.get('/api/projects/proj_1/rag-configs', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
    http.get('/api/agents/agent_1/mcp', () => HttpResponse.json([])),
    http.get('/api/agents/agent_1/concept-map-coverage', () =>
      HttpResponse.json({
        agent_id: 'agent_1',
        entries: [
          {
            config_id: 'gr_1',
            owner_kind: 'chatroom',
            owner_id: 'room_1',
            owner_name: 'General Room',
            active: true,
            last_build_state: 'idle',
            last_build_at: null,
            last_build_error: null,
          },
        ],
      }),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('AgentDetailView', () => {
  it('populates the form from the fetched agent', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    // Name is the first text input on the General tab.
    const nameInput = wrapper.find('.s-input__field').element as HTMLInputElement
    expect(nameInput.value).toBe('My Bot')
    // Model provider is the first select.
    const modelSelect = wrapper.find('.s-select__native').element as HTMLSelectElement
    expect(modelSelect.value).toBe('openai')
  })

  it('shows the tabbed configuration layout in edit mode', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('My Bot')
    // A delete button is present in edit mode (danger variant).
    expect(wrapper.find('button.s-btn--danger').exists()).toBe(true)
  })

  it('renders sampling controls and shows a pinned temperature of 0 (AC-1)', async () => {
    seed()
    // Later server.use wins: pin the reproducible-scoring config on the agent.
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, temperature: 0, top_p: 0.9, seed: 123 }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    // The sampling section and its OpenAI-only seed note render (i18n keys in test).
    expect(wrapper.text()).toContain('agents.form.samplingTitle')
    expect(wrapper.text()).toContain('agents.form.seedHelp')
    // temperature=0 must display as "0", not blank — 0 is a valid pinned value,
    // distinct from "unset" (provider default). top_p/seed round-trip too.
    const values = wrapper
      .findAll('.s-input__field')
      .map((i) => (i.element as HTMLInputElement).value)
    expect(values).toContain('0')
    expect(values).toContain('0.9')
    expect(values).toContain('123')
  })

  it('shows read-only Concept Map coverage on the Knowledge tab (AC-6)', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1?tab=knowledge',
    })
    await settle(wrapper)
    // The covering map's owner name renders (transparency), not an attach control.
    expect(wrapper.text()).toContain('General Room')
    expect(wrapper.text()).toContain('agents.graphragCoverage.active')
    // No Concept Map attach select was ever added — only the rag_config_id one.
    expect(wrapper.text()).not.toContain('agents.graphragForm.agent')
  })
})
