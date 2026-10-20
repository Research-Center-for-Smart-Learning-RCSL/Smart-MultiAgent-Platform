import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import DashboardView from '../views/DashboardView.vue'
import SAlert from '@shared/ui/SAlert.vue'

const summaryMock = vi.hoisted(() => vi.fn())
const summaryErrorMock = vi.hoisted(() => vi.fn(() => null))
const summaryLoadingMock = vi.hoisted(() => vi.fn(() => false))
const timeseriesMock = vi.hoisted(() => vi.fn())
const timeseriesErrorMock = vi.hoisted(() => vi.fn(() => null))
const timeseriesLoadingMock = vi.hoisted(() => vi.fn(() => false))
const watchlistMock = vi.hoisted(() => vi.fn())
const watchlistErrorMock = vi.hoisted(() => vi.fn(() => null))
const watchlistLoadingMock = vi.hoisted(() => vi.fn(() => false))
const roleScope = vi.hoisted(() => ({ projectId: '', authorized: false }))

vi.mock('../composables/useDashboardSummary', () => ({
  useDashboardSummary: () => ({
    data: ref(summaryMock()),
    isLoading: ref(summaryLoadingMock()),
    error: ref(summaryErrorMock()),
    dataUpdatedAt: ref(Date.now()),
    refetch: vi.fn(),
  }),
}))

vi.mock('../composables/useDashboardTimeseries', () => ({
  useDashboardTimeseries: () => ({
    data: ref(timeseriesMock()),
    isLoading: ref(timeseriesLoadingMock()),
    error: ref(timeseriesErrorMock()),
    refetch: vi.fn(),
  }),
}))

vi.mock('../composables/useDashboardWatchlist', () => ({
  useDashboardWatchlist: () => ({
    data: ref(watchlistMock()),
    isLoading: ref(watchlistLoadingMock()),
    error: ref(watchlistErrorMock()),
    refetch: vi.fn(),
  }),
}))

vi.mock('../composables/useDashboardSocket', () => ({
  useDashboardSocket: () => ({
    connected: ref(false),
    projectId: ref('project-from-workspace'),
    workspace: ref({ name: 'Classroom', project_id: 'project-from-workspace' }),
  }),
}))

vi.mock('../components/OutputChart.vue', () => ({
  default: {
    name: 'OutputChart',
    template: '<div data-testid="chart-stub" />',
    props: ['buckets', 'rooms', 'window', 'bucket'],
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
  useWorkspaceStore: () => ({ projectId: 'project-from-sidebar' }),
}))

const dashboardRoutes = [
  {
    path: '/workspaces/:workspaceId/dashboard',
    name: 'dashboard',
    component: DashboardView,
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
]

function room(status: 'green' | 'yellow' | 'red' | 'inactive', roomId: string) {
  return {
    room_id: roomId,
    room_name: `Room ${roomId}`,
    round: null,
    total_submissions: 10,
    valid_count: 8,
    last_valid_submission_at: new Date().toISOString(),
    status,
    online_count: 3,
    participant_count: 4,
    message_counts: { human: 6, agent: 2 },
  }
}

async function renderDashboard(initialRoute = '/workspaces/w1/dashboard') {
  const wrapper = await renderView(DashboardView, {
    routes: dashboardRoutes,
    initialRoute,
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  summaryErrorMock.mockReturnValue(null)
  summaryLoadingMock.mockReturnValue(false)
  timeseriesErrorMock.mockReturnValue(null)
  timeseriesLoadingMock.mockReturnValue(false)
  watchlistErrorMock.mockReturnValue(null)
  watchlistLoadingMock.mockReturnValue(false)
  roleScope.authorized = false
  summaryMock.mockReturnValue({
    rooms: [],
    thresholds: { green_minutes: 5, yellow_minutes: 15, idle_minutes: 10 },
    generated_at: new Date().toISOString(),
  })
  timeseriesMock.mockReturnValue({ buckets: [] })
  watchlistMock.mockReturnValue({ entries: [] })
})

describe('DashboardView', () => {
  it('renders the empty state when the successful summary has no rooms', async () => {
    const wrapper = await renderDashboard()
    expect(wrapper.text()).toContain('dashboard.noRooms')
  })

  it('shows a summary error instead of the no-rooms state', async () => {
    summaryErrorMock.mockReturnValue(new Error('summary failed'))
    const wrapper = await renderDashboard()
    expect(wrapper.text()).toContain('summary failed')
    expect(wrapper.text()).not.toContain('dashboard.noRooms')
  })

  it('shows an error with retry for a failed watchlist query', async () => {
    summaryMock.mockReturnValue({
      rooms: [room('green', 'r1')],
      thresholds: { green_minutes: 5, yellow_minutes: 15, idle_minutes: 10 },
    })
    watchlistErrorMock.mockReturnValue(new Error('watchlist failed'))
    const wrapper = await renderDashboard()
    expect(wrapper.text()).toContain('watchlist failed')
    expect(wrapper.text()).not.toContain('dashboard.watchlistEmpty')
    expect(wrapper.findAll('button').some((button) => button.text() === 'dashboard.retry')).toBe(true)
  })

  it('shows a timeseries error inside the trend card', async () => {
    summaryMock.mockReturnValue({
      rooms: [room('green', 'r1')],
      thresholds: { green_minutes: 5, yellow_minutes: 15, idle_minutes: 10 },
    })
    timeseriesErrorMock.mockReturnValue(new Error('timeseries failed'))
    const wrapper = await renderDashboard()
    expect(wrapper.text()).toContain('timeseries failed')
    expect(wrapper.find('[aria-labelledby="dashboard-trend-heading"]').exists()).toBe(true)
  })

  it('sorts cards red, yellow, green, inactive and links each to its room', async () => {
    summaryMock.mockReturnValue({
      rooms: [room('green', 'g'), room('inactive', 'i'), room('yellow', 'y'), room('red', 'r')],
      thresholds: { green_minutes: 5, yellow_minutes: 15, idle_minutes: 10 },
    })
    const wrapper = await renderDashboard()
    const cards = wrapper.findAll('.room-status-card')
    expect(cards.map((card) => card.text())).toEqual([
      expect.stringContaining('Room r'),
      expect.stringContaining('Room y'),
      expect.stringContaining('Room g'),
      expect.stringContaining('Room i'),
    ])
    expect(cards.every((card) => card.element.closest('a')?.getAttribute('href')?.includes('/chatrooms/'))).toBe(true)
  })

  it('renders one muted success line and no alert when no room is stalled', async () => {
    summaryMock.mockReturnValue({
      rooms: [room('green', 'g')],
      thresholds: { green_minutes: 5, yellow_minutes: 15, idle_minutes: 10 },
    })
    const wrapper = await renderDashboard()
    expect(wrapper.findComponent(SAlert).exists()).toBe(false)
    expect(wrapper.text().match(/dashboard\.noStalledRooms/g)).toHaveLength(1)
  })

  it("gates export using the viewed workspace project's role", async () => {
    roleScope.authorized = true
    const wrapper = await renderDashboard('/workspaces/w2/dashboard')
    expect(roleScope.projectId).toBe('project-from-workspace')
    expect(wrapper.find('[data-testid="research-export-stub"]').exists()).toBe(true)
    roleScope.authorized = false
  })
})
