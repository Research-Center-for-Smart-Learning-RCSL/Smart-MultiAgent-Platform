import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowRunView from '../views/WorkflowRunView.vue'

describe('WorkflowRunView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowRunView, {
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs/run_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the run detail section', async () => {
    const wrapper = await renderView(WorkflowRunView, {
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs/run_1',
    })
    expect(wrapper.find('h1').exists()).toBe(true)
  })
})
