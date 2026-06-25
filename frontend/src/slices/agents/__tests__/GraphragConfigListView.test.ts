import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import GraphragConfigListView from '../views/GraphragConfigListView.vue'

const routes = [
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: GraphragConfigListView,
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

const KEY_GROUPS = [
  { id: 'kg_1', project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
  { id: 'kg_2', project_id: 'proj_1', name: 'Builder', created_at: '2026-01-01T00:00:00Z' },
]

function seed(opts: { configs?: unknown[]; agents?: unknown[] } = {}): void {
  server.use(
    http.get('/api/projects/proj_1/graphrag-configs', () =>
      HttpResponse.json(opts.configs ?? []),
    ),
    http.get('/api/projects/proj_1/agents', () => HttpResponse.json(opts.agents ?? [AGENT])),
    http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json(KEY_GROUPS)),
  )
}

const CONFIG = {
  id: 'gr_1',
  project_id: 'proj_1',
  agent_id: 'agent_1',
  builder_key_group_id: 'kg_2',
  trigger_config: {},
  last_build_state: 'succeeded',
  last_build_at: '2026-01-02T00:00:00Z',
  last_build_error: null,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('GraphragConfigListView', () => {
  it('lists configs resolving the agent name and builder key-group name', async () => {
    seed({ configs: [CONFIG] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('Researcher')
    expect(wrapper.text()).toContain('Builder')
  })

  it('enables create when an unconfigured agent and a key group exist', async () => {
    seed({ configs: [] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    const createBtn = wrapper.find('button.s-btn--primary')
    expect(createBtn.attributes('disabled')).toBeUndefined()
  })

  it('disables create when every agent already has a config', async () => {
    seed({ configs: [CONFIG] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('button.s-btn--primary').attributes('disabled')).toBeDefined()
  })

  it('flags a built config as unbound when its agent does not point back at it', async () => {
    // AGENT.graphrag_config_id is null, so gr_1 is built-but-inert.
    seed({ configs: [CONFIG] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('.s-badge--warning').exists()).toBe(true)
  })

  it('flags a config as active when its agent points back at it', async () => {
    seed({ configs: [CONFIG], agents: [{ ...AGENT, graphrag_config_id: 'gr_1' }] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('.s-badge--warning').exists()).toBe(false)
    expect(wrapper.find('.s-badge--success').exists()).toBe(true)
  })
})
