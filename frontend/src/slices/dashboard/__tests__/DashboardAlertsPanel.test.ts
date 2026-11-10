// Dashboard analyst alerts ([R34.02], AC-5). The test i18n harness echoes keys.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import DashboardAlertsPanel from '../components/DashboardAlertsPanel.vue'
import type { DashboardAlert } from '../types'

const alertsMock = vi.hoisted(() => vi.fn())
const viewsMock = vi.hoisted(() => vi.fn(async () => undefined))

vi.mock('@shared/api-client', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    DashboardService: { dashboardAlertsApiV1WorkspacesWorkspaceIdDashboardAlertsGet: alertsMock },
    ObservationsService: { recordAlertViewsApiChatroomsChatroomIdObservationsViewedPost: viewsMock },
  }
})

function alert(over: Partial<DashboardAlert> = {}): DashboardAlert {
  return {
    observation_id: 'o1',
    room_id: 'r1',
    room_name: 'Room A',
    agent_name: 'AA',
    alert_reason: 'group B stuck',
    created_at: '2026-01-01T00:00:00Z',
    first_viewed_at: null,
    first_action_at: null,
    first_action_kind: null,
    ...over,
  }
}

function setVisibility(state: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('DashboardAlertsPanel', () => {
  beforeEach(() => {
    alertsMock.mockReset()
    viewsMock.mockClear()
  })
  afterEach(() => setVisibility('visible'))

  it('shows an empty state when there are no alerts', async () => {
    alertsMock.mockResolvedValue({ alerts: [] })
    const wrapper = await renderView(DashboardAlertsPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()
    expect(wrapper.find('section').text()).toContain('dashboard.alerts.empty')
    expect(wrapper.findAll('[data-testid="dashboard-alert"]')).toHaveLength(0)
    expect(viewsMock).not.toHaveBeenCalled()
  })

  it('lists alerts with their state and reports unseen ones per room via dashboard', async () => {
    alertsMock.mockResolvedValue({
      alerts: [
        alert(),
        alert({ observation_id: 'o2', room_id: 'r2', room_name: 'Room B' }),
        alert({ observation_id: 'o3', first_viewed_at: '2026-01-01T00:01:00Z' }),
        alert({
          observation_id: 'o4',
          first_viewed_at: '2026-01-01T00:01:00Z',
          first_action_at: '2026-01-01T00:02:00Z',
          first_action_kind: 'room_message',
        }),
      ],
    })
    const wrapper = await renderView(DashboardAlertsPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="dashboard-alert"]')
    expect(rows).toHaveLength(4)
    expect(rows[0]!.text()).toContain('dashboard.alerts.new')
    expect(rows[2]!.text()).toContain('dashboard.alerts.seen')
    expect(rows[3]!.text()).toContain('dashboard.alerts.action.room_message')

    expect(viewsMock).toHaveBeenCalledTimes(2)
    expect(viewsMock).toHaveBeenCalledWith({
      chatroomId: 'r1',
      requestBody: { observation_ids: ['o1'], via: 'dashboard' },
    })
    expect(viewsMock).toHaveBeenCalledWith({
      chatroomId: 'r2',
      requestBody: { observation_ids: ['o2'], via: 'dashboard' },
    })
  })

  it('does not report while the page is hidden', async () => {
    setVisibility('hidden')
    alertsMock.mockResolvedValue({ alerts: [alert()] })
    await renderView(DashboardAlertsPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()
    expect(viewsMock).not.toHaveBeenCalled()

    setVisibility('visible')
    await flushPromises()
    expect(viewsMock).toHaveBeenCalledTimes(1)
  })
})
