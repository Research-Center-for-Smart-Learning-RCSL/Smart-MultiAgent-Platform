import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import WorkflowBackstageView from '../views/WorkflowBackstageView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/backstage',
    name: 'workflow.backstage',
    component: WorkflowBackstageView,
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

// The view redirects to `root` once it decides the visitor is not authorized.
// Left unauthorized, that navigation resolves after the file's tests finish and
// jsdom is torn down, where vue-router's `history.state` read throws an
// unhandled ReferenceError and fails the run. Sign in as the project owner so
// the backstage renders for real and no navigation is left in flight.
function signInAsOwner(): void {
  const session = useSessionStore()
  session.me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
  server.use(
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([
        { user_id: 'u_1', email: 'u@smap.test', role: 'owner', joined_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

describe('WorkflowBackstageView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowBackstageView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/backstage',
    })
    signInAsOwner()
    await settle(wrapper)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the run selector dropdown', async () => {
    const wrapper = await renderView(WorkflowBackstageView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/backstage',
    })
    signInAsOwner()
    await settle(wrapper)
    expect(wrapper.find('.workflow-backstage').exists()).toBe(true)
    expect(wrapper.find('select').exists()).toBe(true)
  })
})
