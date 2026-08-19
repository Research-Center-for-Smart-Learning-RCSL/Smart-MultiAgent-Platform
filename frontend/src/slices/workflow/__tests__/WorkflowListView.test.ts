import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, it, expect, vi } from 'vitest'
import { renderView } from '../../../../tests/utils'
import * as workflowApi from '../api'
import WorkflowListView from '../views/WorkflowListView.vue'

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))

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
  afterEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })
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
    // The name field is an SInput; empty submissions are guarded in onCreate
    // rather than by the native required attribute.
    expect(wrapper.find('input#workflow-name').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })

  it('keeps the list rendered when create fails', async () => {
    vi.spyOn(workflowApi, 'createWorkflow').mockRejectedValue(new Error('denied'))
    const wrapper = await renderView(WorkflowListView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    await wrapper.get('input#workflow-name').setValue('Denied workflow')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.findComponent({ name: 'STable' }).exists()).toBe(true)
  })
})
