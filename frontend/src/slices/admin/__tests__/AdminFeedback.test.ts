import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type * as SharedComposables from '@shared/composables'

import { ApiError } from '@shared/errors'
import { i18n } from '@shared/i18n'
import { setAccessToken } from '@shared/transport'
import { renderView } from '../../../../tests/utils'
import { adminApi } from '../api/admin'
import { useAdminActions } from '../composables/useAdminActions'
import AdminAdminsView from '../views/AdminAdminsView.vue'
import AdminImpersonateLauncher from '../views/AdminImpersonateLauncher.vue'
import AdminOpsView from '../views/AdminOpsView.vue'

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))
vi.mock('@shared/composables', async () => {
  const actual = await vi.importActual<typeof SharedComposables>('@shared/composables')
  return {
    ...actual,
    useConfirmDialog: () => ({
      confirm: vi.fn().mockResolvedValue(true),
      prompt: vi.fn(),
    }),
  }
})

function problem(type: string): ApiError {
  return new ApiError({
    type: `https://smap.local/problems/${type}`,
    title: 'Request failed',
    status: 409,
  })
}

afterEach(() => {
  setAccessToken(null)
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

  it('uses a success toast and no standing alert for GraphRAG reset', async () => {
    vi.spyOn(adminApi, 'resetGraphrag').mockResolvedValue(undefined)
    const wrapper = await renderView(AdminOpsView)
    const resetForm = wrapper.findAll('form')[0]
    await resetForm.get('input').setValue('cfg_1')
    await resetForm.trigger('submit')
    await flushPromises()

    expect(sonner.success).toHaveBeenCalledWith(
      'admin.ops.graphragResetSuccess',
      expect.any(Object),
    )
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('leaves impersonation failure feedback to its composable', async () => {
    vi.spyOn(adminApi, 'impersonate').mockRejectedValue(problem('admin/impersonation-failed'))
    const wrapper = await renderView(AdminImpersonateLauncher)
    await wrapper.get('form input').setValue('u_1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('leaves end-impersonation failure feedback to its composable', async () => {
    const claims = btoa(JSON.stringify({ sub: 'u_1', impersonated_by: 'admin_1' }))
    setAccessToken(`header.${claims}.signature`)
    vi.spyOn(adminApi, 'endImpersonate').mockRejectedValue(problem('admin/impersonation-failed'))
    const wrapper = await renderView(AdminImpersonateLauncher)
    await wrapper.get('.admin-impersonate__active button').trigger('click')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it.each([
    ['reset', 'resetGraphrag', 0],
    ['restore', 'restoreResource', 1],
  ] as const)('keeps %s failures in one toast with no standing alert', async (_name, method, formIndex) => {
    vi.spyOn(adminApi, method).mockRejectedValue(problem(`admin/${method}-failed`))
    const wrapper = await renderView(AdminOpsView)
    const form = wrapper.findAll('form')[formIndex]
    await form.get('input').setValue('resource_1')
    await form.trigger('submit')
    await flushPromises()

    expect(sonner.error).toHaveBeenCalledOnce()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
})
