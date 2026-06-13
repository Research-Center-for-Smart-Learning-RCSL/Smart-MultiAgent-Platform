import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentDetailView from '../views/AgentDetailView.vue'

const routes = [
  { path: '/agents/:agentId', name: 'agents.detail', component: AgentDetailView },
]

describe('AgentDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('populates form fields from fetched agent data', async () => {
    server.use(
      http.get('/api/agents/:agentId', () =>
        HttpResponse.json({
          id: 'agent_1',
          name: 'My Bot',
          project_id: 'proj_1',
          model_hint: 'openai',
          key_group_id: 'kg_1',
          system_prompt: 'You are helpful.',
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
        }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await new Promise((r) => setTimeout(r, 100))
    await wrapper.vm.$nextTick()
    const nameInput = wrapper.find('#name').element as HTMLInputElement
    expect(nameInput.value).toBe('My Bot')
    const modelHintSelect = wrapper.find('#model_hint').element as HTMLSelectElement
    expect(modelHintSelect.value).toBe('openai')
  })
})
