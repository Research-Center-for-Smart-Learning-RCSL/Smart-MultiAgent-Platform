import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowRunView from '../views/WorkflowRunView.vue'

const routes = [
  {
    path: '/workflow-runs/:runId',
    name: 'workflow.run',
    component: WorkflowRunView,
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

describe('WorkflowRunView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowRunView, {
      routes,
      initialRoute: '/workflow-runs/run_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the run detail section', async () => {
    const wrapper = await renderView(WorkflowRunView, {
      routes,
      initialRoute: '/workflow-runs/run_1',
    })
    expect(wrapper.find('.workflow-run').exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(true)
  })
})
