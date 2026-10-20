import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const summaryServiceMock = vi.hoisted(() => vi.fn())

vi.mock('@shared/api-client', () => ({
  DashboardService: {
    dashboardSummaryApiV1WorkspacesWorkspaceIdDashboardSummaryGet: summaryServiceMock,
  },
}))

import { useDashboardSummary } from '../composables/useDashboardSummary'

describe('useDashboardSummary polling', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('refreshes the room status within the 30-second polling interval', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-20T10:00:00.000Z'))
    summaryServiceMock
      .mockResolvedValueOnce({ rooms: [{ room_id: 'r1', status: 'green' }] })
      .mockResolvedValueOnce({ rooms: [{ room_id: 'r1', status: 'yellow' }] })

    const Harness = defineComponent({
      setup() {
        const query = useDashboardSummary(() => 'workspace-1')
        return () => h('div', query.data.value?.rooms[0]?.status ?? 'loading')
      },
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const wrapper = mount(Harness, {
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    })

    await flushPromises()
    expect(wrapper.text()).toBe('green')
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()

    expect(summaryServiceMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toBe('yellow')
    wrapper.unmount()
    queryClient.clear()
  })
})
