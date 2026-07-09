import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import ConceptMapPanel from '../components/ConceptMapPanel.vue'

const routes = [
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

const PANEL_PROPS = { projectId: 'proj_1', ownerKind: 'workspace' as const, ownerId: 'ws_1' }

const CFG = {
  id: 'gr_1',
  project_id: 'proj_1',
  owner_kind: 'workspace',
  owner_id: 'ws_1',
  owner_name: 'Main WS',
  agent_id: null,
  builder_key_group_id: 'kg_2',
  trigger_config: {},
  recency_half_life_days: null,
  last_build_state: 'idle',
  last_build_at: '2026-01-02T00:00:00Z',
  last_build_error: null,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

function signInAs(userId: string, isAdmin = false): void {
  const session = useSessionStore()
  session.me = { id: userId, email: 'u@smap.test', email_verified: true, is_admin: isAdmin, status: 'active' }
}

function seed(role: 'owner' | 'member'): void {
  server.use(
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([CFG])),
    http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([{ user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' }]),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

describe('ConceptMapPanel', () => {
  it('enables Build/Delete/Save for a real project owner once authorization resolves', async () => {
    seed('owner')
    const wrapper = await renderView(ConceptMapPanel, { routes, props: PANEL_PROPS })
    signInAs('u_1')
    await settle(wrapper)

    const buttons = wrapper.findAll('button').filter((b) => /Build|Delete/i.test(b.text()))
    expect(buttons.length).toBeGreaterThan(0)
    for (const b of buttons) {
      expect((b.element as HTMLButtonElement).disabled).toBe(false)
    }
  })

  it('renders but disables Build/Delete for a non-owner (never hard-hides them)', async () => {
    seed('member')
    const wrapper = await renderView(ConceptMapPanel, { routes, props: PANEL_PROPS })
    signInAs('u_1')
    await settle(wrapper)

    const buildBtn = wrapper.findAll('button').find((b) => /Build/i.test(b.text()))
    const deleteBtn = wrapper.findAll('button').find((b) => /Delete/i.test(b.text()))
    // Rendered (not v-if hidden) but inert — a non-owner never gets a live control.
    expect(buildBtn?.exists()).toBe(true)
    expect(deleteBtn?.exists()).toBe(true)
    expect((buildBtn!.element as HTMLButtonElement).disabled).toBe(true)
    expect((deleteBtn!.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('authorizes a platform admin who is not a project member row', async () => {
    // Admin has no membership row at all — useProjectRole must still resolve
    // isAuthorized=true without waiting on (or needing) the members fetch.
    server.use(
      http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([CFG])),
      http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1/members', () => HttpResponse.json([])),
    )
    const wrapper = await renderView(ConceptMapPanel, { routes, props: PANEL_PROPS })
    signInAs('admin_1', true)
    await settle(wrapper)

    const buildBtn = wrapper.findAll('button').find((b) => /Build/i.test(b.text()))
    expect(buildBtn?.exists()).toBe(true)
    expect((buildBtn!.element as HTMLButtonElement).disabled).toBe(false)
  })
})
