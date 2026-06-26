import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import McpEgressAllowlistView from '../views/McpEgressAllowlistView.vue'

const routes = [
  {
    path: '/projects/:projectId/mcp/egress-allowlist',
    name: 'agents.egressAllowlist',
    component: McpEgressAllowlistView,
  },
]

function signInAs(userId: string): void {
  const session = useSessionStore()
  session.me = {
    id: userId,
    email: 'u@smap.test',
    email_verified: true,
    is_admin: false,
    status: 'active',
  }
}

function seedOwner(userId: string): void {
  server.use(
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([
        { user_id: userId, email: 'u@smap.test', role: 'owner', joined_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
  )
}

function seed(entries: unknown[]): void {
  server.use(
    http.get('/api/projects/proj_1/mcp/egress-allowlist', () => HttpResponse.json(entries)),
  )
}

const ENTRY = {
  id: 'al_1',
  project_id: 'proj_1',
  hostname: 'api.example.com',
  added_by_user_id: 'user_1',
  added_at: '2026-01-01T00:00:00Z',
  note: 'weather API',
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('McpEgressAllowlistView', () => {
  it('lists allowlisted hostnames fetched from the backend', async () => {
    seed([ENTRY])
    const wrapper = await renderView(McpEgressAllowlistView, {
      routes,
      initialRoute: '/projects/proj_1/mcp/egress-allowlist',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('api.example.com')
    expect(wrapper.text()).toContain('weather API')
  })

  it('renders the add form for project owners', async () => {
    seed([])
    seedOwner('u_test')
    const wrapper = await renderView(McpEgressAllowlistView, {
      routes,
      initialRoute: '/projects/proj_1/mcp/egress-allowlist',
    })
    signInAs('u_test')
    await settle(wrapper)
    expect(wrapper.find('table.s-table').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('hides the add form for non-owners', async () => {
    seed([])
    server.use(
      http.get('/api/projects/proj_1/members', () =>
        HttpResponse.json([
          { user_id: 'u_member', email: 'member@smap.test', role: 'member', joined_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(McpEgressAllowlistView, {
      routes,
      initialRoute: '/projects/proj_1/mcp/egress-allowlist',
    })
    signInAs('u_member')
    await settle(wrapper)
    expect(wrapper.find('table.s-table').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(false)
  })
})
