import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomView from '../views/ChatroomView.vue'

const routes = [
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: ChatroomView,
  },
]

describe('ChatroomView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the message list and composer form', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    expect(wrapper.find('.chatroom').exists()).toBe(true)
    expect(wrapper.find('form.composer').exists()).toBe(true)
    expect(wrapper.find('ol.messages').exists()).toBe(true)
  })
})
