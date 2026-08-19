/* eslint-disable vue/one-component-per-file -- Inline harnesses expose composables through a real Vue setup context. */
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@shared/errors'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../tests/utils'
import { adminApi } from '../api/admin'
import { useAdminActions } from '../composables/useAdminActions'
import { useImpersonation } from '../composables/useImpersonation'
import AdminAdminsView from '../views/AdminAdminsView.vue'
import AdminOpsView from '../views/AdminOpsView.vue'

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))

function problem(type: string): ApiError {
  return new ApiError({
    type: `https://smap.local/problems/${type}`,
    title: 'Request failed',
    status: 409,
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('admin feedback ownership', () => {
  it('uses one problem-aware toast for last-admin demotion', async () => {
    vi.spyOn(adminApi, 'demoteAdmin').mockRejectedValue(problem('admin/last-admin'))
    let actions!: ReturnType<typeof useAdminActions>
    const Harness = defineComponent({
      setup() {
        actions = useAdminActions()
        return () => h('div')
      },
    })
    mount(Harness, {
      global: {
        plugins: [
          i18n,
          createRouter({
            history: createMemoryHistory(),
            routes: [{ path: '/', component: { template: '<div />' } }],
          }),
          [VueQueryPlugin, { queryClient: new QueryClient() }],
        ],
      },
    })

    await expect(actions.demoteAdmin.mutateAsync('u_1')).rejects.toBeInstanceOf(ApiError)
    expect(sonner.error).toHaveBeenCalledOnce()
    expect(sonner.error).toHaveBeenCalledWith('admin.users.lastAdminDemote', expect.any(Object))
  })

  it('does not add a standing promote error beside the mutation toast', async () => {
    vi.spyOn(adminApi, 'promoteAdmin').mockRejectedValue(problem('admin/promote-failed'))
    const wrapper = await renderView(AdminAdminsView)
    await wrapper.get('form input').setValue('u_missing')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('uses a success toast and no standing alert for resource restore', async () => {
    vi.spyOn(adminApi, 'restoreResource').mockResolvedValue({ restored: true })
    const wrapper = await renderView(AdminOpsView)
    const restoreForm = wrapper.findAll('form')[1]
    await restoreForm.get('input').setValue('org_1')
    await restoreForm.trigger('submit')
    await flushPromises()

    expect(sonner.success).toHaveBeenCalledWith('admin.ops.restoreSuccess', expect.any(Object))
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('leaves impersonation failure feedback to its composable', async () => {
    vi.spyOn(adminApi, 'impersonate').mockRejectedValue(problem('admin/impersonation-failed'))
    let impersonation!: ReturnType<typeof useImpersonation>
    const Harness = defineComponent({
      setup() {
        impersonation = useImpersonation()
        return () => h('div')
      },
    })
    mount(Harness, {
      global: {
        plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
      },
    })

    await expect(impersonation.startImpersonation.mutateAsync('u_1')).rejects.toBeInstanceOf(ApiError)
    expect(sonner.error).toHaveBeenCalledOnce()
  })
})
