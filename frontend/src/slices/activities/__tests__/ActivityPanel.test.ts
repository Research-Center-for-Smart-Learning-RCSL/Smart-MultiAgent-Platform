// Q-1/AC-2: the participant surface (Join button / ActivityHost) must not
// depend on the project-scoped `listActivityTypes` list, which a guest or
// non-owner cannot always reach. The activation read/broadcast now embeds
// the rendering contract directly; a room-scoped fetch covers the case
// where it doesn't (missed broadcast, store reset).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { buildGroupProposal, renderView } from '../../../../tests/utils'
import ActivityHost from '../components/ActivityHost.vue'
import ActivityPanel from '../components/ActivityPanel.vue'
import { useActivitiesStore } from '../stores/activities'
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

// The panel subscribes to the facilitator's own user channel for progress
// updates ([R30.22]); a per-event handler map lets a test fire one.
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

function publicType(over: Partial<ActivityTypePublic> = {}): ActivityTypePublic {
  return {
    id: 'at_1',
    key: 'demo',
    name: 'Demo',
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
  sessionMe.value = null
  getOwnRoundSessionMock.mockResolvedValue(null)
  getActivationProgressMock.mockResolvedValue({ completed: 0, in_progress: 0 })
  listGroupProposalsMock.mockResolvedValue({ items: [], eligible_groups: [] })
})

afterEach(() => {
  getActiveActivationMock.mockReset()
  listActivityTypesMock.mockReset()
  getRoomActivityTypeMock.mockReset()
  startActivationMock.mockReset()
  endActivationMock.mockReset()
  setActivationCompletionMock.mockReset()
  getActivationProgressMock.mockReset()
  getOwnRoundSessionMock.mockReset()
  submitActivityMock.mockReset()
  listGroupProposalsMock.mockReset()
  createGroupProposalMock.mockReset()
  voteOnGroupProposalMock.mockReset()
  withdrawGroupProposalMock.mockReset()
  for (const key of Object.keys(wsHandlers)) delete wsHandlers[key]
})

describe('ActivityPanel — group mode ([R30.40], [R30.41])', () => {
  const GROUP_CONFIG = { consent: { numerator: 2, denominator: 3 } }

  async function panel(over: {
    groupConfig?: Record<string, unknown> | null
    eligible?: Array<{ id: string; name: string }>
    items?: unknown[]
  } = {}) {
    sessionMe.value = { id: 'u_bob' }
    getActiveActivationMock.mockResolvedValue(
      activeActivation({
        // `??` would fold an explicit null back to the default, which is the
        // exact case these tests exist to distinguish.
        activity_type: publicType({
          group_config: 'groupConfig' in over ? over.groupConfig : GROUP_CONFIG,
        }),
      }),
    )
    listActivityTypesMock.mockResolvedValue([])
    listGroupProposalsMock.mockResolvedValue({
      items: over.items ?? [],
      eligible_groups: over.eligible ?? [{ id: 'g1', name: 'Group A' }],
    })
    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()
    return wrapper
  }

  it('offers the group form when the type declares a fraction and the caller has a group', async () => {
    const wrapper = await panel()

    expect(wrapper.text()).toContain('activities.group.intro')
    expect(wrapper.find('form').exists()).toBe(true)
    // No personal "I am finished": the answer belongs to a subject this
    // participant is only part of ([R30.39]).
    expect(wrapper.text()).not.toContain('activities.panel.markDone')
  })

  it('stays on the individual path for a type with no fraction', async () => {
    // AC-2 in the panel: an individual-only type behaves exactly as before, and
    // the group read is not even issued.
    const wrapper = await panel({ groupConfig: null })

    expect(wrapper.text()).not.toContain('activities.group.intro')
    expect(wrapper.text()).toContain('activities.panel.markDone')
    expect(listGroupProposalsMock).not.toHaveBeenCalled()
  })

  it('stays on the individual path for a caller the server put in no group', async () => {
    // A guest can never belong to a Member Group (OQ-1), and a student in a
    // group-submission room who somehow is not grouped must still be able to
    // answer rather than face a picker with nothing in it.
    const wrapper = await panel({ eligible: [] })

    expect(wrapper.text()).not.toContain('activities.group.intro')
    expect(wrapper.text()).toContain('activities.panel.markDone')
  })

  it('ignores a group_config whose shape this client does not understand', async () => {
    const wrapper = await panel({ groupConfig: { consent: { numerator: 'two' } } })

    expect(wrapper.text()).not.toContain('activities.group.intro')
    expect(listGroupProposalsMock).not.toHaveBeenCalled()
  })

  it('preselects the single group rather than asking for a click that carries no decision', async () => {
    const wrapper = await panel()

    expect(wrapper.findAll('select')).toHaveLength(0)
    expect(wrapper.find('form button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('asks which group when the caller belongs to more than one', async () => {
    const wrapper = await panel({
      eligible: [
        { id: 'g1', name: 'Group A' },
        { id: 'g2', name: 'Group B' },
      ],
    })

    expect(wrapper.findAll('select')).toHaveLength(1)
    // Nothing is preselected, so the form cannot post to an arbitrary group.
    expect(wrapper.find('form button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('proposes for the preselected group', async () => {
    createGroupProposalMock.mockResolvedValue({})
    const wrapper = await panel()

    await wrapper.find('form input').setValue('our answer')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(createGroupProposalMock).toHaveBeenCalledWith('c1', {
      activity_type_id: 'at_1',
      member_group_id: 'g1',
      payload: { answer: 'our answer' },
    })
    expect(submitActivityMock).not.toHaveBeenCalled()
  })

  it('shows the card instead of the form once a proposal is open', async () => {
    const wrapper = await panel({ items: [buildGroupProposal()] })

    expect(wrapper.find('[data-testid="group-proposal-card"]').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('shows neither worksheet until the round read has answered', async () => {
    // `canPropose` is false both for a caller in no group AND while the read is
    // in flight. A panel that treats those the same shows the INDIVIDUAL
    // worksheet to a group participant for as long as the request takes — long
    // enough to type an answer into a surface about to be replaced.
    const { deferred } = await import('../../../../tests/utils')
    const gate = deferred<{ items: unknown[]; eligible_groups: unknown[] }>()
    sessionMe.value = { id: 'u_bob' }
    getActiveActivationMock.mockResolvedValue(
      activeActivation({ activity_type: publicType({ group_config: GROUP_CONFIG }) }),
    )
    listActivityTypesMock.mockResolvedValue([])
    listGroupProposalsMock.mockReturnValue(gate.promise)

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('activities.panel.markDone')

    gate.resolve({ items: [], eligible_groups: [{ id: 'g1', name: 'Group A' }] })
    await flushPromises()

    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.text()).toContain('activities.group.intro')
  })

  it('offers a retry rather than spinning forever when the round read fails', async () => {
    const { ApiError } = await import('@shared/errors')
    sessionMe.value = { id: 'u_bob' }
    getActiveActivationMock.mockResolvedValue(
      activeActivation({ activity_type: publicType({ group_config: GROUP_CONFIG }) }),
    )
    listActivityTypesMock.mockResolvedValue([])
    listGroupProposalsMock.mockRejectedValueOnce(
      new ApiError({ type: 'about:blank', title: 'boom', status: 500 }),
    )

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    listGroupProposalsMock.mockResolvedValue({
      items: [],
      eligible_groups: [{ id: 'g1', name: 'Group A' }],
    })

    await wrapper.find('[data-testid="group-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('activities.group.intro')
  })
})

describe('ActivityPanel — platform policy refusal (AC-14)', () => {
  async function refuseWith(extra: Record<string, unknown>) {
    const { ApiError } = await import('@shared/errors')
    getActiveActivationMock.mockResolvedValue(null)
    listActivityTypesMock.mockResolvedValue([publicType()])
    startActivationMock.mockRejectedValue(
      new ApiError({
        type: 'https://smap.invalid/problems/activities/type-violates-policy',
        title: 'This activity type conflicts with the platform activity policy',
        status: 409,
        detail: 'platform policy requires expose_payload_to_agent to be false',
        ...extra,
      }),
    )
    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()
    wrapper.findAll('select')[0]?.setValue('at_1')
    await flushPromises()
    const start = wrapper.findAll('button').find((b) => b.text().includes('startForRoom'))
    await start?.trigger('click')
    await flushPromises()
    return wrapper
  }

  it('names the offending field and says a project owner must fix it', async () => {
    // "Could not start" is not enough for someone standing in front of a class.
    const wrapper = await refuseWith({ field: 'expose_payload_to_agent' })

    expect(wrapper.text()).toContain('activities.panel.policyRefusedField')
    expect(wrapper.text()).not.toContain('activities.panel.startFailed')
  })

  it('still explains itself when the field is absent', async () => {
    const wrapper = await refuseWith({})

    expect(wrapper.text()).toContain('activities.panel.policyRefused')
  })

  it('never shows the raw backend field name to a facilitator', async () => {
    // `expose_payload_to_agent` inside a zh-TW sentence is the one word the
    // reader most needs and the only one left untranslated.
    const wrapper = await refuseWith({ field: 'expose_payload_to_agent' })

    expect(wrapper.text()).not.toContain('expose_payload_to_agent')
  })

  it('falls back to the field-less message for a field it cannot translate', async () => {
    const wrapper = await refuseWith({ field: 'some_future_field' })

    expect(wrapper.text()).not.toContain('some_future_field')
    expect(wrapper.text()).not.toContain('activities.panel.policyRefusedField')
    expect(wrapper.text()).toContain('activities.panel.policyRefused')
  })

  it('leaves an unrelated failure on the generic message', async () => {
    const { ApiError } = await import('@shared/errors')
    getActiveActivationMock.mockResolvedValue(null)
    listActivityTypesMock.mockResolvedValue([publicType()])
    startActivationMock.mockRejectedValue(
      new ApiError({
        type: 'https://smap.invalid/problems/activities/already-active',
        title: 'A different activity is already active',
        status: 409,
      }),
    )
    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()
    wrapper.findAll('select')[0]?.setValue('at_1')
    await flushPromises()
    const start = wrapper.findAll('button').find((b) => b.text().includes('startForRoom'))
    await start?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('activities.panel.policyRefused')
  })
})

describe('ActivityPanel — participant surface (Q-1, AC-2)', () => {
  it('renders participant surface when listActivityTypes rejects', async () => {
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockRejectedValue(new Error('forbidden'))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    // AC-1: the worksheet, not a button to reveal it.
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('falls back to getRoomActivityType when activation lacks the type', async () => {
    getActiveActivationMock.mockResolvedValue(activeActivation({ activity_type: null }))
    listActivityTypesMock.mockResolvedValue([])
    getRoomActivityTypeMock.mockResolvedValue(publicType({ name: 'Fetched Type' }))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(getRoomActivityTypeMock).toHaveBeenCalledWith('c1', 'at_1')
    expect(wrapper.text()).toContain('Fetched Type')
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('surfaces a getRoomActivityType fallback failure instead of silently stalling', async () => {
    getActiveActivationMock.mockResolvedValue({
      id: 'act_1',
      chatroom_id: 'c1',
      activity_type_id: 'at_1',
      started_by_user_id: 'u1',
      status: 'active',
      created_at: null,
      ended_at: null,
      activity_type: null,
    })
    listActivityTypesMock.mockResolvedValue([])
    getRoomActivityTypeMock.mockRejectedValue(new Error('network error'))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  })

  it('clears the error once the fallback fetch recovers on a later activation', async () => {
    getActiveActivationMock.mockResolvedValue({
      id: 'act_1',
      chatroom_id: 'c1',
      activity_type_id: 'at_1',
      started_by_user_id: 'u1',
      status: 'active',
      created_at: null,
      ended_at: null,
      activity_type: null,
    })
    listActivityTypesMock.mockResolvedValue([])
    getRoomActivityTypeMock.mockRejectedValueOnce(new Error('network error'))
    getRoomActivityTypeMock.mockResolvedValueOnce(publicType({ id: 'at_2', name: 'Recovered' }))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)

    const store = useActivitiesStore()
    store.setActivation('c1', {
      id: 'act_2',
      activityTypeId: 'at_2',
      startedByUserId: 'u1',
      activityType: null,
    })
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Recovered')
  })

  it('surfaces a listActivityTypes failure for the facilitator (dropdown), unlike the participant', async () => {
    getActiveActivationMock.mockResolvedValue(null)
    listActivityTypesMock.mockRejectedValue(new Error('forbidden'))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  })
})

describe('ActivityPanel — delegated round attribution (AC-17, [R30.37])', () => {
  it('names the agent that started an agent-started round, to everyone', async () => {
    // Participant-visible on purpose: an agent bound to a room is already named
    // on every message it sends, so this discloses nothing the class cannot see.
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(
      activeActivation({ started_by_agent_id: 'ag_1', started_by_agent_name: 'TA' }),
    )
    listActivityTypesMock.mockResolvedValue([])

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('activities.panel.startedByAgent')
  })

  it('shows nothing extra for a human-started round', async () => {
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('activities.panel.startedByAgent')
  })

  it('shows nothing when the starting agent has since been deleted', async () => {
    // The id survives the agent; the name does not. Two fields rather than one is
    // what lets the panel stay quiet instead of rendering a blank attribution.
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(
      activeActivation({ started_by_agent_id: 'ag_gone', started_by_agent_name: null }),
    )
    listActivityTypesMock.mockResolvedValue([])

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('activities.panel.startedByAgent')
  })
})

describe('ActivityPanel — completion declaration (AC-1, AC-5)', () => {
  function doneButton(wrapper: { findAll: (s: string) => Array<{ text: () => string }> }) {
    return wrapper
      .findAll('button')
      .find((b) => b.text().includes('activities.panel.markDone'))
  }

  it('offers no start button, only the worksheet and the done toggle', async () => {
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    // The two removed keys must not come back under any label.
    expect(wrapper.text()).not.toContain('activities.panel.join')
    expect(wrapper.text()).not.toContain('activities.panel.finish')
    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDone')
  })

  it('seeds the toggle from the round read so a reload is not misread', async () => {
    // The client holds no session id, so without this read a participant who
    // had already declared themselves finished sees the toggle un-set.
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])
    getOwnRoundSessionMock.mockResolvedValue({ completed_at: '2026-08-17T00:00:00Z' })

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(getOwnRoundSessionMock).toHaveBeenCalledWith('c1', 'act_1')
    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDoneUndo')
  })

  it('is reversible', async () => {
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])
    setActivationCompletionMock.mockResolvedValue({ completed_at: '2026-08-17T00:00:00Z' })

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()
    await doneButton(wrapper)?.trigger('click')
    await flushPromises()

    expect(setActivationCompletionMock).toHaveBeenLastCalledWith('c1', 'act_1', true)
    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDoneUndo')

    setActivationCompletionMock.mockResolvedValue({ completed_at: null })
    await doneButton(wrapper)?.trigger('click')
    await flushPromises()

    expect(setActivationCompletionMock).toHaveBeenLastCalledWith('c1', 'act_1', false)
    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDone')
  })

  it('follows the server when answering again retracts the declaration', async () => {
    // The server clears `completed_at` on any submission ([R30.22]). If the
    // toggle did not follow, it would read "Keep working" for someone the server
    // considers unfinished — and the next click would send `false`, a no-op, so
    // re-declaring would cost two clicks with the wrong label in between.
    sessionMe.value = { id: 'u2' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])
    getOwnRoundSessionMock.mockResolvedValue({ completed_at: '2026-08-17T00:00:00Z' })

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()
    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDoneUndo')

    wrapper.findComponent(ActivityHost).vm.$emit('submitted')
    await flushPromises()

    expect(doneButton(wrapper)?.text()).toContain('activities.panel.markDone')
  })
})

describe('ActivityPanel — facilitator progress (AC-6)', () => {
  it('shows counts to the facilitator and never to a participant', async () => {
    sessionMe.value = { id: 'u1' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])
    getActivationProgressMock.mockResolvedValue({ completed: 3, in_progress: 5 })

    const facilitator = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()
    expect(facilitator.text()).toContain('activities.panel.progress')

    sessionMe.value = { id: 'u2' }
    const participant = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()
    expect(participant.text()).not.toContain('activities.panel.progress')
    // The participant must not even ask: the endpoint is room-creator gated.
    expect(getActivationProgressMock).toHaveBeenCalledTimes(1)
  })

  it('updates from the completion event without a refetch', async () => {
    sessionMe.value = { id: 'u1' } // the activation's starter
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])
    getActivationProgressMock.mockResolvedValue({ completed: 0, in_progress: 4 })

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()
    getActivationProgressMock.mockClear()

    for (const handler of wsHandlers['activity.session.completion'] ?? []) {
      handler({ chatroom_id: 'c1', activation_id: 'act_1', completed: 2, in_progress: 2 })
    }
    await flushPromises()

    expect(getActivationProgressMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('activities.panel.progress')
  })

  it('ignores a completion event for another room or round', async () => {
    sessionMe.value = { id: 'u1' }
    getActiveActivationMock.mockResolvedValue(activeActivation())
    listActivityTypesMock.mockResolvedValue([])

    await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()

    const before = wsHandlers['activity.session.completion']?.length ?? 0
    expect(before).toBeGreaterThan(0)
    for (const handler of wsHandlers['activity.session.completion'] ?? []) {
      handler({ chatroom_id: 'other', activation_id: 'act_1', completed: 9, in_progress: 9 })
      handler({ chatroom_id: 'c1', activation_id: 'act_other', completed: 9, in_progress: 9 })
    }
    await flushPromises()

    // Nothing to assert on the rendered count (the harness renders raw keys), so
    // the claim is that neither event threw and neither triggered a refetch.
    expect(getActivationProgressMock).toHaveBeenCalledTimes(1)
  })
})

describe('ActivityPanel — cross-scope key collision (AC-5)', () => {
  // A project's usable set can hold its own type and an opted-in platform type
  // under one key ([R30.02]). The two are then identical in this picker unless
  // it says which is which — the worst case being identical names too, which is
  // exactly what an owner copying a shipped example produces.
  const collidingPair = [
    {
      id: 'at_project',
      key: 'mandala-9grid',
      name: 'Mandala',
      scope: 'project',
      payload_schema: { type: 'object', properties: {} },
    },
    {
      id: 'at_platform',
      key: 'mandala-9grid',
      name: 'Mandala',
      scope: 'platform',
      payload_schema: { type: 'object', properties: {} },
    },
  ]

  it('distinguishes the platform row from the project row of the same key and name', async () => {
    getActiveActivationMock.mockResolvedValue(null)
    listActivityTypesMock.mockResolvedValue(collidingPair)

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()

    const labels = wrapper.findAll('option').map((o) => o.text())
    expect(labels).toContain('Mandala')
    expect(labels).toContain('activities.panel.platformTypeOption')
    // The ids stay the values, so starting the intended one was always possible;
    // what was missing was any way to tell them apart.
    const values = wrapper.findAll('option').map((o) => o.attributes('value'))
    expect(values).toContain('at_project')
    expect(values).toContain('at_platform')
  })

  it('leaves an ordinary project-only list unmarked', async () => {
    getActiveActivationMock.mockResolvedValue(null)
    listActivityTypesMock.mockResolvedValue([collidingPair[0]])

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: true },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('activities.panel.platformTypeOption')
  })
})
