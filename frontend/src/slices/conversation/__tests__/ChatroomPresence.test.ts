// The participant list's own contract (docs/tasks/2026-10-05-guest-room-read-and-identity).
// The view decides who "you" are and whether you are a guest; this pins what the
// list does with that, so a regression is located here rather than in the view.

import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import { SAvatar } from '@shared/ui'

const editKey = 'conversation.guest.editName'

function props(over: Record<string, unknown> = {}) {
  return {
    onlineUsers: [
      { id: 'g_1', isYou: true, displayName: 'Alice' },
      { id: 'u_2', isYou: false, displayName: 'Carol' },
      { id: 'u_3abcdefghij', isYou: false, displayName: null },
    ],
    agents: [],
    ...over,
  }
}

describe('ChatroomPresence', () => {
  it('lets a guest rename itself from its own row', async () => {
    const wrapper = await renderView(ChatroomPresence, {
      props: props({ viewerIsGuest: true, viewerName: 'Alice' }),
    })
    expect(wrapper.find(`[aria-label="${editKey}"]`).exists()).toBe(true)
    expect(wrapper.text()).toContain('conversation.chatroom.you')

    await wrapper.find(`[aria-label="${editKey}"]`).trigger('click')
    await wrapper.find('form.presence-user__edit input').setValue('  Bob  ')
    await wrapper.find('form.presence-user__edit').trigger('submit')

    expect(wrapper.emitted('update-display-name')).toEqual([['Bob']])
  })

  it('offers no rename to a signed-in member, who is still marked as you', async () => {
    const wrapper = await renderView(ChatroomPresence, { props: props() })
    expect(wrapper.find(`[aria-label="${editKey}"]`).exists()).toBe(false)
    expect(wrapper.text()).toContain('conversation.chatroom.you')
  })

  it('names other participants, falling back to a short id', async () => {
    const wrapper = await renderView(ChatroomPresence, { props: props() })
    expect(wrapper.text()).toContain('Carol')
    expect(wrapper.text()).toContain('u_3abcde')
  })

  it('draws avatar initials from the name, not the id', async () => {
    const wrapper = await renderView(ChatroomPresence, { props: props() })
    const names = wrapper.findAllComponents(SAvatar).map((a) => a.props('name'))
    expect(names).toEqual(['Alice', 'Carol', 'u_3abcdefghij'])
  })
})
