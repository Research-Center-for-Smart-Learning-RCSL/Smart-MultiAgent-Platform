import { describe, it, expect, vi, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { http, HttpResponse } from 'msw'
import { ElMessage } from 'element-plus'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ChatroomView from '../views/ChatroomView.vue'
import { useConversationStore } from '../stores/conversation'
import { useSessionStore } from '@slices/identity'

const routes = [
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: ChatroomView,
  },
]

function signInAs(userId: string, isAdmin = false): void {
  const session = useSessionStore()
  session.me = {
    id: userId,
    email: 'u@smap.test',
    email_verified: true,
    is_admin: isAdmin,
    status: 'active',
  }
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await nextTick()
}

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

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows edit/delete affordances on the user\'s own recent message', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_1',
            content_md: 'hello',
            metadata: {},
            version: 1,
            created_at: new Date(Date.now() - 1000).toISOString(),
            edited_at: null,
            deleted_at: null,
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()
    // Two link buttons (Edit + Delete) on the own, within-window message.
    expect(wrapper.findAll('.link-btn').length).toBe(2)
  })

  it('surfaces export status after triggering an export', async () => {
    server.use(
      http.post('/api/chatrooms/cr_1/export', () =>
        HttpResponse.json({ job_id: 'job_1', status: 'queued' }),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    await wrapper.find('header button').trigger('click')
    await settle()
    expect(wrapper.find('.export-status').exists()).toBe(true)
  })

  it('renders the streaming draft bubble while agent tokens accumulate', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    const store = useConversationStore()
    expect(wrapper.find('[data-testid="streaming-draft"]').exists()).toBe(false)

    store.appendAgentToken('cr_1', 'Hello **wor')
    store.appendAgentToken('cr_1', 'ld**')
    await nextTick()
    const bubble = wrapper.find('[data-testid="streaming-draft"]')
    expect(bubble.exists()).toBe(true)
    expect(bubble.find('.md').html()).toContain('<strong>world</strong>')

    // Cleared when the persisted message arrives (socket calls this).
    store.clearAgentStream('cr_1')
    await nextTick()
    expect(wrapper.find('[data-testid="streaming-draft"]').exists()).toBe(false)
  })

  it('toasts and clears the store flag when an agent error is surfaced', async () => {
    const errorSpy = vi.spyOn(ElMessage, 'error').mockReturnValue(undefined as never)
    await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    const store = useConversationStore()

    store.setAgentError('cr_1', 'provider_error')
    await nextTick()
    expect(errorSpy).toHaveBeenCalledTimes(1)
    expect(store.agentError['cr_1']).toBeNull()

    store.setAgentError('cr_1', 'timeout')
    await nextTick()
    expect(errorSpy).toHaveBeenCalledTimes(2)
    expect(store.agentError['cr_1']).toBeNull()
  })
})
