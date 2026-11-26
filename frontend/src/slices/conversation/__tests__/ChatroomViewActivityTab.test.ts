// The right rail follows the round (docs/tasks/2026-11-10-crat-lab-trial-fixes).
//
// The Activity tab appeared when a round started but the rail stayed on People, so
// every participant had to find the form by hand. A tab picked afterwards must
// stand until the next round, or the rail would fight the user.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { renderView } from '../../../../tests/utils'
import { setAccessToken } from '@shared/transport'
import { useActivitiesStore } from '@slices/activities'
import ChatroomView from '../views/ChatroomView.vue'
import { settle, unsignedToken } from './kit'

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]
const ROOM = 'cr_1'
const ACTIVITY_TAB = 'activities.panel.tab'
const PEOPLE_TAB = 'conversation.chatroom.people'

function round(id: string) {
  return {
    id,
    activityTypeId: 'type_1',
    startedByUserId: null,
    activityType: null,
    startedByAgentId: null,
    startedByAgentName: null,
    deadlineAt: null,
    brief: null,
  }
}

let width = 0
beforeEach(() => {
  width = window.innerWidth
  window.innerWidth = 1440
  setAccessToken(
    unsignedToken({ sub: 'g_1', token_use: 'guest_access', display_name: 'Alice', chatroom_id: ROOM }),
  )
})
afterEach(() => {
  setAccessToken(null)
  window.innerWidth = width
})

async function mount() {
  const wrapper = await renderView(ChatroomView, { routes, initialRoute: `/chatrooms/${ROOM}` })
  await settle()
  const tab = (label: string) => wrapper.findAll('[role="tab"]').find((t) => t.text() === label)
  const selected = () =>
    wrapper
      .findAll('[role="tab"]')
      .filter((t) => t.attributes('aria-selected') === 'true')
      .map((t) => t.text())
  return { wrapper, tab, selected }
}

describe('ChatroomView right rail', () => {
  it('stays on People while no round is running', async () => {
    const { selected } = await mount()
    expect(selected()).toEqual([])
  })

  it('switches to the Activity tab when a round starts', async () => {
    const { selected, tab } = await mount()
    expect(tab(ACTIVITY_TAB)).toBeUndefined()

    useActivitiesStore().setActivation(ROOM, round('act_1'))
    await settle()

    expect(selected()).toEqual([ACTIVITY_TAB])
  })

  it('leaves a tab the user picked alone while the same round runs', async () => {
    const { selected, tab } = await mount()
    useActivitiesStore().setActivation(ROOM, round('act_1'))
    await settle()

    await tab(PEOPLE_TAB)?.trigger('click')
    await settle()
    // A deadline or brief update re-sets the same activation.
    useActivitiesStore().setActivation(ROOM, { ...round('act_1'), deadlineAt: '2026-11-26T10:00:00Z' })
    await settle()

    expect(selected()).toEqual([PEOPLE_TAB])
  })

  it('switches again when the next round starts', async () => {
    const { selected, tab } = await mount()
    useActivitiesStore().setActivation(ROOM, round('act_1'))
    await settle()
    await tab(PEOPLE_TAB)?.trigger('click')
    await settle()

    useActivitiesStore().setActivation(ROOM, round('act_2'))
    await settle()

    expect(selected()).toEqual([ACTIVITY_TAB])
  })
})
