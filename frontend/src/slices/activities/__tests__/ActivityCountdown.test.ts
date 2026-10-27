// Remaining time on the activity panel when the running round has a deadline
// (phase 4 AC-8). The deadline comes from the existing active-activation read.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../tests/utils'
import ActivityCountdown from '../components/ActivityCountdown.vue'
import ActivityPanel from '../components/ActivityPanel.vue'

const getActiveActivationMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  getActiveActivation: getActiveActivationMock,
  listActivityTypes: vi.fn().mockResolvedValue([]),
  getRoomActivityType: vi.fn(),
  startActivation: vi.fn(),
  endActivation: vi.fn(),
  setActivationCompletion: vi.fn(),
  getActivationProgress: vi.fn().mockResolvedValue({ completed: 0, in_progress: 0 }),
  getOwnRoundSession: vi.fn().mockResolvedValue(null),
  submitActivity: vi.fn(),
  listGroupProposals: vi.fn().mockResolvedValue({ items: [], eligible_groups: [] }),
  createGroupProposal: vi.fn(),
  voteOnGroupProposal: vi.fn(),
  withdrawGroupProposal: vi.fn(),
}))

vi.mock('@shared/transport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  isGuestSession: { value: false },
  wsManager: {
    channel: () => ({ subscribe: () => () => {}, connect: () => {} }),
  },
}))

vi.mock('@shared/stores/session', () => ({
  useSessionStore: () => ({ me: { id: 'u_student' } }),
}))

const NOW = new Date('2026-10-27T10:00:00Z')

function at(offsetMs: number): string {
  return new Date(NOW.getTime() + offsetMs).toISOString()
}

beforeEach(() => {
  // Only the clock and the interval: faking every timer would also stall the
  // promise plumbing the panel's reads go through.
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
  getActiveActivationMock.mockReset()
})

function mountCountdown(deadlineAt: string) {
  return mount(ActivityCountdown, { props: { deadlineAt }, global: { plugins: [i18n] } })
}

describe('ActivityCountdown', () => {
  it('rounds the remaining time up to whole minutes', () => {
    const spy = vi.spyOn(i18n.global, 't')
    const wrapper = mountCountdown(at(2 * 60_000 + 30_000))
    expect(wrapper.text()).toContain('activities.countdown.minutesLeft')
    expect(spy).toHaveBeenCalledWith('activities.countdown.minutesLeft', { n: 3 })
    spy.mockRestore()
  })

  it('counts down and says time is up once the deadline passes', async () => {
    const wrapper = mountCountdown(at(61_000))
    expect(wrapper.text()).toContain('activities.countdown.minutesLeft')

    await vi.advanceTimersByTimeAsync(60_000)
    expect(wrapper.text()).toContain('activities.countdown.minutesLeft')

    await vi.advanceTimersByTimeAsync(2_000)
    expect(wrapper.text()).toContain('activities.countdown.timeUp')
    expect(wrapper.text()).not.toContain('activities.countdown.minutesLeft')
  })

  it('runs one interval, stops it at expiry and on unmount', async () => {
    const live = mountCountdown(at(5 * 60_000))
    expect(vi.getTimerCount()).toBe(1)
    live.unmount()
    expect(vi.getTimerCount()).toBe(0)

    mountCountdown(at(1_000))
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('starts no timer for a deadline already in the past', () => {
    const wrapper = mountCountdown(at(-1_000))
    expect(wrapper.text()).toContain('activities.countdown.timeUp')
    expect(vi.getTimerCount()).toBe(0)
  })
})

function activation(over: Record<string, unknown> = {}) {
  return {
    id: 'act_1',
    chatroom_id: 'c1',
    activity_type_id: 'at_1',
    started_by_user_id: 'u_teacher',
    status: 'active',
    created_at: null,
    ended_at: null,
    activity_type: {
      id: 'at_1',
      key: 'demo',
      name: 'Demo',
      payload_schema: { type: 'object', properties: { answer: { type: 'string' } } },
    },
    ...over,
  }
}

async function mountPanel(isCreator: boolean) {
  const wrapper = await renderView(ActivityPanel, {
    props: { chatroomId: 'c1', projectId: 'p1', isCreator },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('ActivityPanel remaining time', () => {
  it.each([false, true])('shows the countdown when the round has a deadline (creator=%s)', async (isCreator) => {
    getActiveActivationMock.mockResolvedValue(
      activation({ time_limit_minutes: 5, deadline_at: at(4 * 60_000) }),
    )
    const wrapper = await mountPanel(isCreator)
    expect(wrapper.find('[data-testid="activity-countdown"]').exists()).toBe(true)
    expect(getActiveActivationMock).toHaveBeenCalledTimes(1)
  })

  it('hides the countdown when the round has no deadline', async () => {
    getActiveActivationMock.mockResolvedValue(activation({ time_limit_minutes: null, deadline_at: null }))
    const wrapper = await mountPanel(false)
    expect(wrapper.find('[data-testid="activity-countdown"]').exists()).toBe(false)
  })

  it('says time is up on the panel after the deadline', async () => {
    getActiveActivationMock.mockResolvedValue(activation({ deadline_at: at(30_000) }))
    const wrapper = await mountPanel(false)
    expect(wrapper.find('[data-testid="activity-countdown"]').text()).toContain(
      'activities.countdown.minutesLeft',
    )
    await vi.advanceTimersByTimeAsync(31_000)
    expect(wrapper.find('[data-testid="activity-countdown"]').text()).toContain('activities.countdown.timeUp')
  })
})
