import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import WorkspaceSettingsView from '../views/WorkspaceSettingsView.vue'

const routes = [
  {
    path: '/workspaces/:workspaceId/settings',
    name: 'conversation.workspace.settings',
    component: WorkspaceSettingsView,
  },
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

const WORKSPACE = {
  id: 'ws_1',
  project_id: 'proj_1',
  name: 'Research WS',
  concept_map_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
}

function signInAs(userId: string): void {
  const session = useSessionStore()
  session.me = { id: userId, email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
}

function seed(role: 'owner' | 'member'): void {
  server.use(
    http.get('/api/workspaces/:workspaceId', () => HttpResponse.json(WORKSPACE)),
    http.get('/api/projects/:projectId/graphrag-configs', () => HttpResponse.json([])),
    http.get('/api/projects/:projectId/key-groups', () => HttpResponse.json([])),
    http.get('/api/projects/:projectId/members', () =>
      HttpResponse.json([
        { user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
  )
}

describe('WorkspaceSettingsView', () => {
  it('shows the wide-layer privacy toggle to a project owner (AC-4)', async () => {
    seed('owner')
    const wrapper = await renderView(WorkspaceSettingsView, {
      routes,
      initialRoute: '/workspaces/ws_1/settings',
    })
    signInAs('u_1')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('Research WS')
    expect(wrapper.text()).toContain('conversation.conceptMap.workspacePrivacyLabel')
    // The toggle control is present for an owner.
    expect(wrapper.find('.s-toggle, input[type="checkbox"]').exists()).toBe(true)
  })

  it('hides the privacy toggle and shows a read-only note to a non-owner (AC-4)', async () => {
    seed('member')
    const wrapper = await renderView(WorkspaceSettingsView, {
      routes,
      initialRoute: '/workspaces/ws_1/settings',
    })
    signInAs('u_1')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('conversation.conceptMap.privacyOwnerOnly')
    expect(wrapper.find('.s-toggle, input[type="checkbox"]').exists()).toBe(false)
  })
})
