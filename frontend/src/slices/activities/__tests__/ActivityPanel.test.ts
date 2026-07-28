// Q-1/AC-2: the participant surface (Join button / ActivityHost) must not
// depend on the project-scoped `listActivityTypes` list, which a guest or
// non-owner cannot always reach. The activation read/broadcast now embeds
// the rendering contract directly; a room-scoped fetch covers the case
// where it doesn't (missed broadcast, store reset).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ActivityPanel from '../components/ActivityPanel.vue'
import type { ActivityTypePublic } from '../types'

const getActiveActivationMock = vi.hoisted(() => vi.fn())
const listActivityTypesMock = vi.hoisted(() => vi.fn())
const getRoomActivityTypeMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  getActiveActivation: getActiveActivationMock,
  listActivityTypes: listActivityTypesMock,
  getRoomActivityType: getRoomActivityTypeMock,
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
