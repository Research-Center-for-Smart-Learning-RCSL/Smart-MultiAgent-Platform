import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkflowEditorView from '../views/WorkflowEditorView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/edit',
    name: 'workflow.editor',
    component: WorkflowEditorView,
  },
  {
    path: '/workspaces/:workspaceId/workflows',
    name: 'workflow.list',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

describe('WorkflowEditorView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowEditorView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the toolbar header', async () => {
    const wrapper = await renderView(WorkflowEditorView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
    })
    // The editor toolbar is inside a <header> with the back-link and action buttons.
    expect(wrapper.find('.workflow-editor header').exists()).toBe(true)
  })
})
