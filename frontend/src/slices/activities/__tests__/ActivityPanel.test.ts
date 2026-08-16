// Q-1/AC-2: the participant surface (Join button / ActivityHost) must not
// depend on the project-scoped `listActivityTypes` list, which a guest or
// non-owner cannot always reach. The activation read/broadcast now embeds
// the rendering contract directly; a room-scoped fetch covers the case
// where it doesn't (missed broadcast, store reset).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ActivityPanel from '../components/ActivityPanel.vue'
import { useActivitiesStore } from '../stores/activities'
import type { ActivityTypePublic } from '../types'

const getActiveActivationMock = vi.hoisted(() => vi.fn())
const listActivityTypesMock = vi.hoisted(() => vi.fn())
const getRoomActivityTypeMock = vi.hoisted(() => vi.fn())
const startActivationMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  getActiveActivation: getActiveActivationMock,
  listActivityTypes: listActivityTypesMock,
  getRoomActivityType: getRoomActivityTypeMock,
  startActivation: startActivationMock,
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

afterEach(() => {
  getActiveActivationMock.mockReset()
  listActivityTypesMock.mockReset()
  getRoomActivityTypeMock.mockReset()
  startActivationMock.mockReset()
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
    getActiveActivationMock.mockResolvedValue({
      id: 'act_1',
      chatroom_id: 'c1',
      activity_type_id: 'at_1',
      started_by_user_id: 'u1',
      status: 'active',
      created_at: null,
      ended_at: null,
      activity_type: publicType(),
    })
    listActivityTypesMock.mockRejectedValue(new Error('forbidden'))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('activities.panel.join')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('falls back to getRoomActivityType when activation lacks the type', async () => {
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
    getRoomActivityTypeMock.mockResolvedValue(publicType({ name: 'Fetched Type' }))

    const wrapper = await renderView(ActivityPanel, {
      props: { chatroomId: 'c1', projectId: 'p1', isCreator: false },
    })
    await flushPromises()

    expect(getRoomActivityTypeMock).toHaveBeenCalledWith('c1', 'at_1')
    expect(wrapper.text()).toContain('Fetched Type')
    expect(wrapper.text()).toContain('activities.panel.join')
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
