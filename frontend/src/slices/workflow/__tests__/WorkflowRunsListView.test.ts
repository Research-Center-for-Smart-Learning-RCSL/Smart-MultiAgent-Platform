import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowRunsListView from '../views/WorkflowRunsListView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: WorkflowRunsListView,
  },
  {
    path: '/workflow-runs/:runId',
    name: 'workflow.run',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/workflows',
    name: 'workflow.list',
    component: { template: '<div />' },
  },
]

describe('WorkflowRunsListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowRunsListView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the trigger button', async () => {
    const wrapper = await renderView(WorkflowRunsListView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs',
    })
    expect(wrapper.find('.workflow-runs').exists()).toBe(true)
    // The manual trigger button is always visible in the header.
    const buttons = wrapper.findAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(1)
  })
})
