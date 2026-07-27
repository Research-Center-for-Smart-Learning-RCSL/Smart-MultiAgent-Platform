import { describe, it, expect, vi } from 'vitest'
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

// agentCreateSchema requires a uuid, so the create-mode test cannot reuse the
// 'kg_1' placeholder the edit-mode fixture carries (edit mode never submits).
const KEY_GROUP_ID = '11111111-1111-4111-8111-111111111111'

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

  // Create mode reaches the API through a different path than edit (POST to the
  // project, key group defaulted from the query rather than loaded from the
  // agent), and it is what the 04-agent-rag-flow E2E spec drives. Pin the exact
  // payload so a schema or default change that would only surface as an opaque
  // E2E timeout fails here instead.
  it('posts the assembled payload in create mode', async () => {
    const posted = vi.fn()
    server.use(
      http.get('/api/projects/proj_1/key-groups', () =>
        HttpResponse.json([
          { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/projects/proj_1/rag-configs', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1/knowmap-configs', () => HttpResponse.json([])),
      http.post('/api/projects/proj_1/agents', async ({ request }) => {
        posted(await request.json())
        return HttpResponse.json({ ...AGENT, id: 'agent_new' }, { status: 201 })
      }),
    )

    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/new?projectId=proj_1',
    })
    await settle(wrapper)

    // Name is the first text input; model provider the first select. The key
    // group is never touched — it must default to the project's only group, or
    // key_group_id fails its uuid() check and the submit is silently dropped.
    await wrapper.findAll('.s-input__field')[0]!.setValue('e2e-agent')
    await wrapper.findAll('.s-select__native')[0]!.setValue('openai')
    await settle(wrapper)

    await wrapper.findAll('button.s-btn--primary')[0]!.trigger('click')
    await settle(wrapper)

    expect(posted).toHaveBeenCalledTimes(1)
    expect(posted.mock.calls[0]![0]).toMatchObject({
      name: 'e2e-agent',
      model_hint: 'openai',
      key_group_id: KEY_GROUP_ID,
    })
  })

  // soft_bounds (R15.08) is admin-set through the API and has no editor control,
  // so an unrelated save from this view used to drop it from the payload — and
  // the backend wrote that payload into wakeup_authored_snapshot too, erasing the
  // designer's bound with no recovery path.
  it('keeps unmodelled wakeup_config keys in the patch payload', async () => {
    const patched = vi.fn()
    seed()
    server.use(
      // The shared fixture's 'kg_1' is not a uuid, so the schema rejects the
      // submit before it reaches the mutation (see the create-mode note above).
      http.get('/api/projects/proj_1/key-groups', () =>
        HttpResponse.json([
          { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          key_group_id: KEY_GROUP_ID,
          wakeup_config: {
            triggers: { every_n_messages: { enabled: true, n: 8 } },
            soft_bounds: { n_min: 5, n_max: 10 },
          },
        }),
      ),
      http.patch('/api/agents/agent_1', async ({ request }) => {
        patched(await request.json())
        return HttpResponse.json({ ...AGENT, version: 2 })
      }),
    )

    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    // Touch an unrelated field so the form is dirty, then save.
    await wrapper.findAll('.s-input__field')[0]!.setValue('Renamed Bot')
    await settle(wrapper)
    await wrapper.findAll('button.s-btn--primary')[0]!.trigger('click')
    await settle(wrapper)

    expect(patched).toHaveBeenCalledTimes(1)
    const body = patched.mock.calls[0]![0] as { wakeup_config: Record<string, unknown> }
    expect(body.wakeup_config.soft_bounds).toEqual({ n_min: 5, n_max: 10 })
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
