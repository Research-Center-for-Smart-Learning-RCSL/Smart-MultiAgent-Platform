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
          model_provider: 'openai',
          model_name: 'gpt-4o',
          system_prompt: 'You are helpful.',
          temperature: 0.5,
          max_tokens: 2048,
          version: 1,
          rag_config_id: null,
          mcp_server_ids: [],
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
    const tempInput = wrapper.find('#temperature').element as HTMLInputElement
    expect(Number(tempInput.value)).toBe(0.5)
  })
})
