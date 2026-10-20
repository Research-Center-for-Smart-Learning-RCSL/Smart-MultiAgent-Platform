import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import DashboardView from '../views/DashboardView.vue'

const summaryMock = vi.hoisted(() => vi.fn())
const timeseriesMock = vi.hoisted(() => vi.fn())
const watchlistMock = vi.hoisted(() => vi.fn())
const roleScope = vi.hoisted(() => ({ projectId: '', authorized: false }))

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

vi.mock('../composables/useDashboardSocket', () => ({
  useDashboardSocket: () => ({
    connected: { value: false },
    projectId: { value: 'project-from-workspace' },
  }),
}))

vi.mock('../components/OutputChart.vue', () => ({
  default: {
    name: 'OutputChart',
    template: '<div data-testid="chart-stub" />',
    props: ['buckets', 'rooms'],
  },
}))

vi.mock('../components/ResearchExportButton.vue', () => ({
  default: {
    name: 'ResearchExportButton',
    template: '<button data-testid="research-export-stub" />',
    props: ['workspaceId'],
  },
}))

vi.mock('@slices/tenancy', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useProjectRole: (getProjectId: () => string | undefined) => {
      roleScope.projectId = getProjectId() ?? ''
      return {
      isAuthorized: ref(roleScope.authorized),
      decided: ref(true),
      isAdmin: ref(false),
      isOwner: ref(false),
      }
    },
  }
})

vi.mock('@shared/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    projectId: 'project-from-sidebar',
  }),
}))

describe('DashboardView', () => {
  it('renders empty state when no rooms', async () => {
    summaryMock.mockReturnValue({ rooms: [] })
    timeseriesMock.mockReturnValue({ buckets: [] })
    watchlistMock.mockReturnValue({ entries: [] })

    const wrapper = await renderView(DashboardView, {
      routes: [
        {
          path: '/workspaces/:workspaceId/dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: { requiresAuth: true, requiresVerifiedEmail: true },
        },
      ],
      initialRoute: '/workspaces/w1/dashboard',
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
          path: '/workspaces/:workspaceId/dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: { requiresAuth: true, requiresVerifiedEmail: true },
        },
      ],
      initialRoute: '/workspaces/w1/dashboard',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Room A')
  })

  it('gates export using the viewed workspace project, not the sidebar project', async () => {
    roleScope.authorized = true
    summaryMock.mockReturnValue({ rooms: [] })
    timeseriesMock.mockReturnValue({ buckets: [] })
    watchlistMock.mockReturnValue({ entries: [] })

    const wrapper = await renderView(DashboardView, {
      routes: [
        {
          path: '/workspaces/:workspaceId/dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: { requiresAuth: true, requiresVerifiedEmail: true },
        },
      ],
      initialRoute: '/workspaces/w2/dashboard',
    })
    await flushPromises()

    expect(roleScope.projectId).toBe('project-from-workspace')
    expect(wrapper.find('[data-testid="research-export-stub"]').exists()).toBe(true)
    roleScope.authorized = false
  })
})
