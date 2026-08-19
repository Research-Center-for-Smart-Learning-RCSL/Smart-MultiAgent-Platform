import { flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderView } from '../../../tests/utils'
import * as workflowApi from '../../slices/workflow/api'
import WorkflowListView from '../../slices/workflow/views/WorkflowListView.vue'
import ErrorBoundary from '../ErrorBoundary.vue'

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('workflow feedback containment', () => {
  it('keeps the workflow list mounted inside the app error boundary', async () => {
    vi.spyOn(workflowApi, 'createWorkflow').mockRejectedValue(new Error('denied'))
    const BoundaryHost = defineComponent({
      components: { ErrorBoundary, WorkflowListView },
      template: '<ErrorBoundary><WorkflowListView /></ErrorBoundary>',
    })
    const wrapper = await renderView(BoundaryHost, {
      routes: [{
        path: '/workspaces/:workspaceId/workflows',
        component: BoundaryHost,
      }],
      initialRoute: '/workspaces/ws_1/workflows',
    })
    await wrapper.get('input#workflow-name').setValue('Denied workflow')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.findComponent(WorkflowListView).exists()).toBe(true)
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
  })
})
