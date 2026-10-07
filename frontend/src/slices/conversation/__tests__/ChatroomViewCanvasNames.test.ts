// The room hands the canvas its names (docs/tasks/2026-10-07-canvas-awareness-names,
// AC-2, AC-4, Q-2): the canvas slice imports no other slice, so the viewer's own name
// for its cursor and the roster for everyone else's arrive as panel props.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import ChatroomView from '../views/ChatroomView.vue'
import { settle } from './kit'

vi.mock('@slices/canvas', async (importOriginal) => {
  const { defineComponent, h } = await import('vue')
  return {
    ...(await importOriginal<typeof import('@slices/canvas')>()),
    CanvasPanel: defineComponent({
      name: 'CanvasPanelStub',
      props: {
        chatroomId: String,
        chatroomName: String,
        isFullscreen: Boolean,
        viewerName: { type: String, default: null },
        participantNames: { type: Object, default: () => ({}) },
      },
      setup: () => () => h('div'),
    }),
  }
})

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]
const ME = 'u_1aaaaaaaa'

beforeEach(() => {
  window.innerWidth = 1440
  server.use(
    http.get('/api/chatrooms/cr_1/members', () =>
      HttpResponse.json([
        { user_id: ME, display_name: 'Me In Room', kind: 'member' },
        { user_id: 'u_2bbbbbbbb', display_name: 'Alice', kind: 'member' },
      ]),
    ),
  )
})

describe('ChatroomView canvas names', () => {
  it("passes the viewer's room name and the roster to the canvas panel", async () => {
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useSessionStore().me = {
      id: ME,
      email: 'me@smap.test',
      email_verified: true,
      is_admin: false,
      status: 'active',
      display_name: 'Account Name',
    }
    await settle()

    await wrapper.find('[data-testid="toggle-canvas"]').trigger('click')
    await settle()

    const panel = wrapper.findComponent({ name: 'CanvasPanelStub' })
    expect(panel.exists()).toBe(true)
    expect(panel.props('viewerName')).toBe('Me In Room')
    expect(panel.props('participantNames')).toMatchObject({ [ME]: 'Me In Room', u_2bbbbbbbb: 'Alice' })
  })
})
