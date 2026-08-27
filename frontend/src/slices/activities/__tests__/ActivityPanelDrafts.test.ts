// AC-4 / AC-11 — the panel's half of draft reporting and disclosure (§32).
//
// Two things live here that `ActivityHostDrafts.test.ts` cannot cover:
//
// **The group-proposal form is a second worksheet surface**, added by
// `2026-08-24-group-activity-submissions` after this dossier's Q-1 named its two
// surfaces. It renders `SchemaForm` directly rather than through `ActivityHost`,
// so it needs its own throttle and its own clears — and it is reported under the
// activity TYPE key, so the read-time consent gate ([R32.04]) applies to it
// unchanged.
//
// **The disclosure chip is driven by a prop, never by a read.** The activities
// slice must not reach chatroom state (gate #1's SLICE_DEPS), so the panel is told
// whether drafts are readable rather than asking.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ActivityPanel from '../components/ActivityPanel.vue'
import type { ActivityTypePublic } from '../types'

const getActiveActivationMock = vi.hoisted(() => vi.fn())
const listActivityTypesMock = vi.hoisted(() => vi.fn())
const getRoomActivityTypeMock = vi.hoisted(() => vi.fn())
const startActivationMock = vi.hoisted(() => vi.fn())
const endActivationMock = vi.hoisted(() => vi.fn())
const setActivationCompletionMock = vi.hoisted(() => vi.fn())
const getActivationProgressMock = vi.hoisted(() => vi.fn())
const getOwnRoundSessionMock = vi.hoisted(() => vi.fn())
const submitActivityMock = vi.hoisted(() => vi.fn())
const listGroupProposalsMock = vi.hoisted(() => vi.fn())
const createGroupProposalMock = vi.hoisted(() => vi.fn())
const voteOnGroupProposalMock = vi.hoisted(() => vi.fn())
const withdrawGroupProposalMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  getActiveActivation: getActiveActivationMock,
  listActivityTypes: listActivityTypesMock,
  getRoomActivityType: getRoomActivityTypeMock,
  startActivation: startActivationMock,
  endActivation: endActivationMock,
  setActivationCompletion: setActivationCompletionMock,
  getActivationProgress: getActivationProgressMock,
  getOwnRoundSession: getOwnRoundSessionMock,
  submitActivity: submitActivityMock,
  listGroupProposals: listGroupProposalsMock,
  createGroupProposal: createGroupProposalMock,
  voteOnGroupProposal: voteOnGroupProposalMock,
  withdrawGroupProposal: withdrawGroupProposalMock,
}))

const wsHandlers = vi.hoisted(
  () => ({}) as Record<string, Array<(ev: Record<string, unknown>) => void>>,
)
vi.mock('@shared/transport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  wsManager: {
    channel: () => ({
      subscribe: (name: string, handler: (ev: Record<string, unknown>) => void) => {
        ;(wsHandlers[name] ??= []).push(handler)
        return () => {
          wsHandlers[name] = (wsHandlers[name] ?? []).filter((h) => h !== handler)
        }
      },
      connect: () => {},
    }),
  },
}))

const sessionMe = vi.hoisted(() => ({ value: null as { id: string } | null }))
vi.mock('@shared/stores/session', () => ({
  useSessionStore: () => ({
    get me() {
      return sessionMe.value
    },
  }),
}))

const THROTTLE_MS = 3000
const GROUP_CONFIG = { consent: { numerator: 2, denominator: 3 } }

function publicType(over: Partial<ActivityTypePublic> = {}): ActivityTypePublic {
  return {
    id: 'at_1',
    key: 'six-hats-shared-case',
    name: 'Shared case',
    payload_schema: { type: 'object', properties: { answer: { type: 'string' } } },
    ...over,
  }
}

function activeActivation(over: Record<string, unknown> = {}) {
  return {
    id: 'act_1',
    chatroom_id: 'c1',
    activity_type_id: 'at_1',
    started_by_user_id: 'u1',
    status: 'active',
    created_at: null,
    ended_at: null,
    activity_type: publicType(),
    ...over,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  sessionMe.value = { id: 'u_bob' }
  getOwnRoundSessionMock.mockResolvedValue(null)
  getActivationProgressMock.mockResolvedValue({ completed: 0, in_progress: 0 })
  listActivityTypesMock.mockResolvedValue([])
  listGroupProposalsMock.mockResolvedValue({
    items: [],
    eligible_groups: [{ id: 'g1', name: 'Group A' }],
  })
})

afterEach(() => {
  vi.useRealTimers()
  for (const mock of [
    getActiveActivationMock,
    listActivityTypesMock,
    getRoomActivityTypeMock,
    startActivationMock,
    endActivationMock,
    setActivationCompletionMock,
    getActivationProgressMock,
    getOwnRoundSessionMock,
    submitActivityMock,
    listGroupProposalsMock,
    createGroupProposalMock,
    voteOnGroupProposalMock,
    withdrawGroupProposalMock,
  ]) {
    mock.mockReset()
  }
  for (const key of Object.keys(wsHandlers)) delete wsHandlers[key]
})

async function groupPanel(props: Record<string, unknown> = {}) {
  getActiveActivationMock.mockResolvedValue(
    activeActivation({ activity_type: publicType({ group_config: GROUP_CONFIG }) }),
  )
  const wrapper = await renderView(ActivityPanel, {
    props: { chatroomId: 'c1', projectId: 'p1', isCreator: false, ...props },
  })
  await flushPromises()
  return wrapper
}

describe('ActivityPanel — the group-proposal form reports its draft too', () => {
  it('emits draft under the activity TYPE key, on the throttle', async () => {
    // The type key is what carries the read-time consent gate ([R32.04]): a group
    // task whose type agents may not see must have no readable draft either. Any
    // other key would make this surface the one path around that gate.
    const wrapper = await groupPanel()

    await wrapper.find('input').setValue('our shared answer')
    expect(wrapper.emitted('draft')).toBeUndefined()

    vi.advanceTimersByTime(THROTTLE_MS)
    await flushPromises()

    const emitted = wrapper.emitted('draft')
    expect(emitted).toHaveLength(1)
    expect(emitted![0]![0]).toBe('six-hats-shared-case')
    expect(emitted![0]![1]).toMatchObject({ answer: 'our shared answer' })
  })

  it('clears when the proposal is submitted, cancelling anything pending', async () => {
    // A proposal that has gone to the group is no longer unsent, and a pending
    // timer firing afterwards would re-report it for a full TTL.
    createGroupProposalMock.mockResolvedValue(
      { id: 'gp_1', member_group_id: 'g1', status: 'open', votes: [] },
    )
    const wrapper = await groupPanel()

    await wrapper.find('input').setValue('our shared answer')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    vi.advanceTimersByTime(THROTTLE_MS * 2)
    await flushPromises()

    expect(wrapper.emitted('draftClear')).toHaveLength(1)
    expect(wrapper.emitted('draftClear')![0]![0]).toBe('six-hats-shared-case')
    expect(wrapper.emitted('draft')).toBeUndefined()
  })

  it('clears on unmount and leaves no timer behind', async () => {
    const wrapper = await groupPanel()
    await wrapper.find('input').setValue('left on the tab')

    wrapper.unmount()
    vi.advanceTimersByTime(THROTTLE_MS * 2)

    expect(wrapper.emitted('draftClear')).toHaveLength(1)
    expect(wrapper.emitted('draft')).toBeUndefined()
  })

  // NOT covered here: the round-change interaction. `cancelGroupDraft()` in the
  // activation watcher is what stops one round's pending values going out under
  // the next round's key (and so under the next type's consent gate). Two attempts
  // at a component-level test for it passed with the cancel deliberately removed —
  // vacuous both times, once because the wire-shaped activation never reached the
  // store and once for a reason not yet established. A test that cannot fail is
  // worse than none, so it is not here; the cancel's own behaviour is pinned in
  // `useDraftThrottle.test.ts`, and FU-4 of the dossier records the gap.
})

describe('ActivityPanel — the disclosure chip (AC-11)', () => {
  it('is shown when the host says drafts are readable', async () => {
    const wrapper = await groupPanel({ draftsReadable: true })

    expect(wrapper.text()).toContain('shared.draftDisclosure.chip')
  })

  it('opens its tooltip downward, away from the panel top edge', async () => {
    // UX pin, not a clipping fix: the teleported tooltip clips nowhere, but
    // the chip sits near the panel's top edge and an upward bubble would
    // float over the chatroom header rather than over the worksheet.
    // STooltip teleports the bubble to body in production, but renderView
    // stubs Teleport, so the bubble stays inside the wrapper here.
    const wrapper = await groupPanel({ draftsReadable: true })

    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.classes()).toContain('s-tooltip--bottom')
  })

  it('is absent when they are not', async () => {
    const wrapper = await groupPanel({ draftsReadable: false })

    expect(wrapper.text()).not.toContain('shared.draftDisclosure.chip')
  })

  it('is absent when the host says nothing at all', async () => {
    // The prop is optional, and its absence must read as "no" rather than as
    // "unknown, so show it anyway" — a chip in a room with no reader teaches
    // participants to ignore it in the rooms where it matters.
    const wrapper = await groupPanel()

    expect(wrapper.text()).not.toContain('shared.draftDisclosure.chip')
  })
})
