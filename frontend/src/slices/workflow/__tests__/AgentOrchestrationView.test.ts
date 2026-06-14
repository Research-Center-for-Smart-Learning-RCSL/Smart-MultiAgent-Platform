import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentOrchestrationView from '../views/AgentOrchestrationView.vue'

const routes = [
  {
    path: '/agents/:agentId/orchestration',
    name: 'workflow.agentOrchestration',
    component: AgentOrchestrationView,
  },
]

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('AgentOrchestrationView', () => {
  it('default-fills an empty wakeup_config and renders the editor + DLQ viewer', async () => {
    // Most agents store wakeup_config = {}; the view must merge defaults so the
    // structured editor does not crash on the missing nested shape.
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ id: 'agent_1', wakeup_config: {} }),
      ),
    )
    const wrapper = await renderView(AgentOrchestrationView, {
      routes,
      initialRoute: '/agents/agent_1/orchestration',
    })
    await settle(wrapper)
    expect(wrapper.find('.wakeup-editor').exists()).toBe(true)
    expect(wrapper.find('.dlq-viewer').exists()).toBe(true)
  })
})
