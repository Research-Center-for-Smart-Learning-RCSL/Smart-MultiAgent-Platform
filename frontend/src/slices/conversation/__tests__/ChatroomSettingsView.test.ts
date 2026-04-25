import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ChatroomSettingsView from '../views/ChatroomSettingsView.vue'

const routes = [
  {
    path: '/chatrooms/:chatroomId/settings',
    name: 'conversation.chatroom.settings',
    component: ChatroomSettingsView,
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: { template: '<div />' },
  },
]

describe('ChatroomSettingsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the settings section once chatroom data loads', async () => {
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
    })
    await flushPromises()
    await new Promise(r => setTimeout(r, 100))
    expect(wrapper.find('section').exists()).toBe(true)
  })
})
