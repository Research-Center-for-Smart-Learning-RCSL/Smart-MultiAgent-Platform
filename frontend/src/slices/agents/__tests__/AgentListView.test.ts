import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import AgentListView from '../views/AgentListView.vue'

const routes = [
  { path: '/projects/:projectId/agents', name: 'agents.list', component: AgentListView },
  { path: '/agents/:agentId', name: 'agents.detail', component: { template: '<div />' } },
]

describe('AgentListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AgentListView, {
      routes,
      initialRoute: '/projects/proj_1/agents',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('toggles create form on button click', async () => {
    const wrapper = await renderView(AgentListView, {
      routes,
      initialRoute: '/projects/proj_1/agents',
    })
    // Form should be hidden initially
    expect(wrapper.find('.agent-list__form').exists()).toBe(false)
    // Click the create/toggle button
    await wrapper.find('.btn.btn-primary').trigger('click')
    expect(wrapper.find('.agent-list__form').exists()).toBe(true)
    // The form should contain the real backend-contract fields.
    expect(wrapper.find('#name').exists()).toBe(true)
    expect(wrapper.find('#model_hint').exists()).toBe(true)
    expect(wrapper.find('#key_group_id').exists()).toBe(true)
  })
})
