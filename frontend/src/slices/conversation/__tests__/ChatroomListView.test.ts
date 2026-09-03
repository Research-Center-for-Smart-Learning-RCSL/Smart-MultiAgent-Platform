import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import { QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { flushPromises } from '@vue/test-utils'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import SDropdown from '@shared/ui/SDropdown.vue'
import { convKeys } from '../queries'
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

  // F-4. `convKeys.chatrooms(wsId)` is `[...,'chatrooms', wsId]` and
  // `convKeys.recentChatrooms(pid)` is `[...,'chatrooms','recent', pid]` — element
  // [2] differs, so invalidating the former never matched the latter. The recent
  // rail is the one query in the slice that opts out of both safety nets (a 60s
  // staleTime, in a sidebar that never unmounts), so it kept a deleted room on
  // screen and routed the user to a room the server no longer serves.
  describe('the recent-chatrooms rail (F-4)', () => {
    const ROOM = {
      id: 'cr_1',
      workspace_id: 'ws_1',
      name: 'Room One',
      created_at: '2024-01-01T00:00:00.000Z',
    }

    function seededClient(): QueryClient {
      // `gcTime` is deliberately NOT 0 here, unlike the other helpers in this
      // slice's tests: the seeded rail has no observer, so a zero gcTime evicts
      // it before the mutation runs and `getQueryState` then returns undefined —
      // which reads as "not invalidated" and would make these tests pass against
      // the bug and fail against the fix.
      const qc = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      })
      // A populated recent rail, as the sidebar would have left it.
      qc.setQueryData(convKeys.recentChatrooms('p1'), [ROOM])
      return qc
    }

    function recentIsStale(qc: QueryClient): boolean {
      return qc.getQueryState(convKeys.recentChatrooms('p1'))?.isInvalidated === true
    }

    it('is invalidated when a room is deleted', async () => {
      server.use(
        http.get('/api/workspaces/:workspaceId/chatrooms', () => HttpResponse.json([ROOM])),
        http.delete('/api/chatrooms/:chatroomId', () => new HttpResponse(null, { status: 204 })),
      )
      const qc = seededClient()
      const wrapper = await renderView(ChatroomListView, {
        routes,
        initialRoute: '/workspaces/ws_1/chatrooms',
        queryClient: qc,
      })
      await flushPromises()
      expect(recentIsStale(qc)).toBe(false)

      wrapper.findComponent(SDropdown).vm.$emit('select', 'delete')
      await flushPromises()
      // The row action opens a confirm dialog; resolve it the way a click would.
      useConfirmDialog().handleConfirm()
      await flushPromises()

      expect(recentIsStale(qc)).toBe(true)
    })

    it('is invalidated when a room is created', async () => {
      server.use(
        http.get('/api/workspaces/:workspaceId/chatrooms', () => HttpResponse.json([])),
        http.post('/api/workspaces/:workspaceId/chatrooms', () =>
          HttpResponse.json({ ...ROOM, id: 'cr_new', name: 'Made here' }, { status: 201 }),
        ),
      )
      const qc = seededClient()
      const wrapper = await renderView(ChatroomListView, {
        routes,
        initialRoute: '/workspaces/ws_1/chatrooms',
        queryClient: qc,
      })
      await flushPromises()

      await wrapper.find('[data-testid="create-chatroom"]').trigger('click')
      await nextTick()
      await wrapper.find('form input').setValue('Made here')
      await wrapper.find('form').trigger('submit')
      await flushPromises()

      expect(recentIsStale(qc)).toBe(true)
    })
  })
})
