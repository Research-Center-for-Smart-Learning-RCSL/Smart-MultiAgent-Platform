import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowBackstageView from '../views/WorkflowBackstageView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/backstage',
    name: 'workflow.backstage',
    component: WorkflowBackstageView,
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

describe('WorkflowBackstageView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowBackstageView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/backstage',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the run selector dropdown', async () => {
    const wrapper = await renderView(WorkflowBackstageView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/backstage',
    })
    expect(wrapper.find('.workflow-backstage').exists()).toBe(true)
    expect(wrapper.find('select').exists()).toBe(true)
  })
})
