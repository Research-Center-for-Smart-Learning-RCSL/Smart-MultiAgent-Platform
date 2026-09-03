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
import WorkspaceListView from '../views/WorkspaceListView.vue'

const routes = [
  {
    path: '/projects/:projectId/workspaces',
    name: 'conversation.workspaces',
    component: WorkspaceListView,
  },
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: { template: '<div />' },
  },
]

describe('WorkspaceListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkspaceListView, {
      routes,
      initialRoute: '/projects/proj_1/workspaces',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('opens the create modal with a name input', async () => {
    const wrapper = await renderView(WorkspaceListView, {
      routes,
      initialRoute: '/projects/proj_1/workspaces',
    })
    const trigger = wrapper.find('[data-testid="create-workspace"]')
    expect(trigger.exists()).toBe(true)

    await trigger.trigger('click')
    await nextTick()

    // The create form lives in a modal; opening it reveals the name input.
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('form input').exists()).toBe(true)
  })

  // F-4. Deleting a workspace cascades its rooms, and creating one ships a
  // default chatroom, so both move the recent rail. Unlike the chatroom list,
  // these two sites name `convKeys.workspaces`, which the chatrooms prefix does
  // NOT match — so the fix has to ADD an invalidation here rather than widen the
  // existing one. The second assertion in each case is what pins that: replacing
  // instead of adding would leave the workspace list itself stale, a worse defect
  // than the one being fixed, while the first assertion still went green.
  describe('the recent-chatrooms rail (F-4)', () => {
    const WS = { id: 'ws_1', project_id: 'proj_1', name: 'Space One' }

    function seededClient(): QueryClient {
      // Not `gcTime: 0` — an unobserved seeded entry would be evicted before the
      // mutation runs, and a missing entry reads as "not invalidated", which
      // would invert what these tests prove.
      const qc = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      })
      qc.setQueryData(convKeys.recentChatrooms('p1'), [])
      qc.setQueryData(convKeys.workspaces('proj_1'), [WS])
      return qc
    }

    // The rail has no observer, so an invalidation leaves it flagged and that
    // flag is the observable. The workspace list *is* observed, so invalidating
    // it refetches and clears the same flag before any assertion could see it —
    // its observable is the extra GET, which is also the thing that matters.
    const railIsStale = (qc: QueryClient): boolean =>
      qc.getQueryState(convKeys.recentChatrooms('p1'))?.isInvalidated === true

    it('and the workspace list are both refreshed on delete', async () => {
      let listReads = 0
      server.use(
        http.get('/api/projects/:projectId/workspaces', () => {
          listReads += 1
          return HttpResponse.json([WS])
        }),
        http.delete('/api/workspaces/:workspaceId', () => new HttpResponse(null, { status: 204 })),
      )
      const qc = seededClient()
      const wrapper = await renderView(WorkspaceListView, {
        routes,
        initialRoute: '/projects/proj_1/workspaces',
        queryClient: qc,
      })
      await flushPromises()
      const readsBefore = listReads

      wrapper.findComponent(SDropdown).vm.$emit('select', 'delete')
      await flushPromises()
      useConfirmDialog().handleConfirm()
      await flushPromises()

      expect(railIsStale(qc)).toBe(true)
      expect(listReads).toBeGreaterThan(readsBefore)
    })

    it('and the workspace list are both refreshed on create', async () => {
      let listReads = 0
      server.use(
        http.get('/api/projects/:projectId/workspaces', () => {
          listReads += 1
          return HttpResponse.json([])
        }),
        http.post('/api/projects/:projectId/workspaces', () =>
          HttpResponse.json({ ...WS, id: 'ws_new', default_chatroom_id: 'cr_new' }, { status: 201 }),
        ),
      )
      const qc = seededClient()
      const wrapper = await renderView(WorkspaceListView, {
        routes,
        initialRoute: '/projects/proj_1/workspaces',
        queryClient: qc,
      })
      await flushPromises()
      const readsBefore = listReads

      await wrapper.find('[data-testid="create-workspace"]').trigger('click')
      await nextTick()
      await wrapper.find('form input').setValue('Made here')
      await wrapper.find('form').trigger('submit')
      await flushPromises()

      expect(railIsStale(qc)).toBe(true)
      expect(listReads).toBeGreaterThan(readsBefore)
    })
  })
})
