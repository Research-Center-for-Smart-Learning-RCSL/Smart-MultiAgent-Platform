import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import DashboardView from '../views/DashboardView.vue'

const summaryMock = vi.hoisted(() => vi.fn())
const timeseriesMock = vi.hoisted(() => vi.fn())
const watchlistMock = vi.hoisted(() => vi.fn())

vi.mock('../composables/useDashboardSummary', () => ({
  dashboardKeys: {
    all: ['dashboard'] as const,
    summary: (id: string) => ['dashboard', 'summary', id],
  },
  useDashboardSummary: () => ({
    data: ref(summaryMock()),
    isLoading: ref(false),
    error: ref(null),
  }),
}))

vi.mock('../composables/useDashboardTimeseries', () => ({
  useDashboardTimeseries: () => ({
    data: ref(timeseriesMock()),
    isLoading: ref(false),
  }),
}))

vi.mock('../composables/useDashboardWatchlist', () => ({
  useDashboardWatchlist: () => ({
    data: ref(watchlistMock()),
    isLoading: ref(false),
  }),
}))

vi.mock('../composables/useProjectSocket', () => ({
  useProjectSocket: () => ({ connected: { value: false } }),
}))

vi.mock('../components/OutputChart.vue', () => ({
  default: {
    name: 'OutputChart',
    template: '<div data-testid="chart-stub" />',
    props: ['buckets', 'rooms'],
  },
}))

describe('DashboardView', () => {
  it('renders empty state when no rooms', async () => {
    summaryMock.mockReturnValue({ rooms: [] })
    timeseriesMock.mockReturnValue({ buckets: [] })
    watchlistMock.mockReturnValue({ entries: [] })

    const wrapper = await renderView(DashboardView, {
      routes: [
        {
          path: '/projects/:projectId/dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: { requiresAuth: true, requiresVerifiedEmail: true },
        },
      ],
      initialRoute: '/projects/p1/dashboard',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('dashboard.noRooms')
  })

  it('renders room cards when rooms exist', async () => {
    summaryMock.mockReturnValue({
      rooms: [
        {
          room_id: 'r1',
          room_name: 'Room A',
          total_submissions: 10,
          valid_count: 8,
          last_submission_at: new Date().toISOString(),
          status: 'green',
        },
      ],
    })
    timeseriesMock.mockReturnValue({ buckets: [] })
    watchlistMock.mockReturnValue({ entries: [] })

    const wrapper = await renderView(DashboardView, {
      routes: [
        {
          path: '/projects/:projectId/dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: { requiresAuth: true, requiresVerifiedEmail: true },
        },
      ],
      initialRoute: '/projects/p1/dashboard',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Room A')
  })
})
