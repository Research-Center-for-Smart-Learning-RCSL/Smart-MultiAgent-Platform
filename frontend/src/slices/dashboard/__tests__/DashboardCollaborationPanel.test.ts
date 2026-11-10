// Dashboard collaboration log ([R34.03], AC-11). The test i18n harness echoes keys.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import DashboardCollaborationPanel from '../components/DashboardCollaborationPanel.vue'
import type { NoticeEntry, PeerReviewEntry } from '../types'

const collaborationMock = vi.hoisted(() => vi.fn())

vi.mock('@shared/api-client', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    DashboardService: {
      dashboardCollaborationApiV1WorkspacesWorkspaceIdDashboardCollaborationGet: collaborationMock,
    },
  }
})

function review(over: Partial<PeerReviewEntry> = {}): PeerReviewEntry {
  return {
    kind: 'peer_review',
    id: 'p1',
    room_id: 'r1',
    room_name: 'Room A',
    from_agent_name: 'SA',
    to_agent_name: 'TA',
    draft: 'The answer is 12',
    created_at: '2026-01-01T00:00:00Z',
    status: 'pending',
    attempt: 1,
    reason: null,
    revised_text: null,
    expire_reason: null,
    decided_at: null,
    posted_message_id: null,
    ...over,
  }
}

function entry(over: Partial<NoticeEntry> = {}): NoticeEntry {
  return {
    kind: 'analyst_notice',
    id: 'n1',
    room_id: 'r1',
    room_name: 'Room A',
    from_agent_name: 'AA',
    to_agent_name: 'TA',
    error_kind: 'content',
    error: '0.5 is not larger than 0.75',
    suggestion: 'Correct the comparison',
    about_message_id: null,
    created_at: '2026-01-01T00:00:00Z',
    status: 'pending',
    decision: null,
    reason: null,
    responded_at: null,
    ...over,
  }
}

describe('DashboardCollaborationPanel', () => {
  beforeEach(() => collaborationMock.mockReset())

  it('shows an empty state when the log is empty', async () => {
    collaborationMock.mockResolvedValue({ entries: [] })
    const wrapper = await renderView(DashboardCollaborationPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()
    expect(wrapper.find('section').text()).toContain('dashboard.collaboration.empty')
    expect(wrapper.findAll('[data-testid="collaboration-entry"]')).toHaveLength(0)
  })

  it('shows error, suggestion, decision and reason per notice', async () => {
    collaborationMock.mockResolvedValue({
      entries: [
        entry({
          id: 'n2',
          error_kind: 'strategy',
          status: 'answered',
          decision: 'partly_accepted',
          reason: 'The group is close',
          responded_at: '2026-01-01T00:02:00Z',
        }),
        entry(),
        entry({ id: 'n3', status: 'unanswered' }),
      ],
    })
    const wrapper = await renderView(DashboardCollaborationPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()

    expect(collaborationMock).toHaveBeenCalledWith({ workspaceId: 'w1' })
    const rows = wrapper.findAll('[data-testid="collaboration-entry"]')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.text()).toContain('dashboard.collaboration.kind.strategy')
    expect(rows[0]!.text()).toContain('Correct the comparison')
    expect(rows[0]!.text()).toContain('dashboard.collaboration.decision.partly_accepted')
    expect(rows[0]!.find('[data-testid="collaboration-reason"]').text()).toContain('The group is close')
    expect(rows[0]!.text()).toContain('Room A')
    expect(rows[1]!.text()).toContain('dashboard.collaboration.status.pending')
    expect(rows[1]!.find('[data-testid="collaboration-reason"]').exists()).toBe(false)
    expect(rows[2]!.text()).toContain('dashboard.collaboration.status.unanswered')
  })

  it('shows each peer review with its draft, outcome and reason (AC-16)', async () => {
    collaborationMock.mockResolvedValue({
      entries: [
        review({
          status: 'modified',
          reason: 'Too direct',
          revised_text: 'What is 3 times 4?',
          decided_at: '2026-01-01T00:00:30Z',
        }),
        review({ id: 'p2', status: 'expired', expire_reason: 'no_teacher' }),
        entry(),
      ],
    })
    const wrapper = await renderView(DashboardCollaborationPanel, { props: { workspaceId: 'w1' } })
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="collaboration-entry"]')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.text()).toContain('dashboard.collaboration.peerReview.kind')
    expect(rows[0]!.text()).toContain('The answer is 12')
    expect(rows[0]!.find('[data-testid="collaboration-revised"]').text()).toContain('What is 3 times 4?')
    expect(rows[0]!.find('[data-testid="collaboration-reason"]').text()).toContain('Too direct')
    expect(rows[0]!.text()).toContain('dashboard.collaboration.peerReview.status.modified')
    expect(rows[0]!.text()).not.toContain('dashboard.collaboration.suggestion')
    expect(rows[1]!.find('[data-testid="collaboration-expired"]').text()).toContain(
      'dashboard.collaboration.peerReview.expire.no_teacher',
    )
    expect(rows[1]!.find('[data-testid="collaboration-revised"]').exists()).toBe(false)
    expect(rows[2]!.text()).toContain('dashboard.collaboration.kind.content')
  })
})
