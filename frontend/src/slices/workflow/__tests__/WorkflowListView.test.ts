import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowListView from '../views/WorkflowListView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows',
    name: 'workflow.list',
    component: WorkflowListView,
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/edit',
    name: 'workflow.editor',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

describe('WorkflowListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowListView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the create form with a name input', async () => {
    const wrapper = await renderView(WorkflowListView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input[required]').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})
