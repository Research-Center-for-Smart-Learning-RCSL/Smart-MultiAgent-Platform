import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomListView from '../views/ChatroomListView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: ChatroomListView,
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
  {
    path: '/chatrooms/:chatroomId/settings',
    name: 'conversation.chatroom.settings',
    component: { template: '<div />' },
  },
]

describe('ChatroomListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChatroomListView, {
      routes,
      initialRoute: '/workspaces/ws_1/chatrooms',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the create form with a submit button', async () => {
    const wrapper = await renderView(ChatroomListView, {
      routes,
      initialRoute: '/workspaces/ws_1/chatrooms',
    })
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(true)
  })
})
