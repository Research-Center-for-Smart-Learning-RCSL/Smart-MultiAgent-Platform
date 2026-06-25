import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import { renderView } from '../../../../tests/utils'
import ChatroomListView from '../views/ChatroomListView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: ChatroomListView,
  },
  {
    path: '/projects/:projectId/workspaces',
    name: 'conversation.workspaces',
    component: { template: '<div />' },
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

  it('opens the create modal with a name input and access toggles', async () => {
    const wrapper = await renderView(ChatroomListView, {
      routes,
      initialRoute: '/workspaces/ws_1/chatrooms',
    })
    const trigger = wrapper.find('[data-testid="create-chatroom"]')
    expect(trigger.exists()).toBe(true)

    await trigger.trigger('click')
    await nextTick()

    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('form input').exists()).toBe(true)
    // Access flags render as toggle switches inside the same form.
    expect(wrapper.find('form [role="switch"]').exists()).toBe(true)
  })
})
