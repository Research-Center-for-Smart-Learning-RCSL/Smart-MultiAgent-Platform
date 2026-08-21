import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ProjectMembersView from '../views/ProjectMembersView.vue'

const routes = [
  { path: '/projects/:id/members', name: 'tenancy.projectMembers', component: ProjectMembersView },
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
  {
    path: '/projects/:id/member-groups',
    name: 'tenancy.projectMemberGroups',
    component: { template: '<div />' },
  },
  { path: '/projects', name: 'tenancy.projectList', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

const ACCEPT_URL = 'https://smap.example/?invite=1#token=abc123'

function render() {
  return renderView(ProjectMembersView, { routes, initialRoute: '/projects/proj_1/members' })
}

// The caller's authorization comes from `ProjectOut.is_moderator`, never from
// the member list: project ownership is inherited, so an Org Owner manages this
// project while holding no `project_members` row.
function asModerator(): void {
  server.use(
    http.get('/api/projects/:projectId', () =>
      HttpResponse.json({
        id: 'proj_1',
        name: 'Test Project',
        owner_type: 'org',
        owner_id: 'org_1',
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        is_moderator: true,
      }),
    ),
  )
}

function withPool(members: Array<{ user_id: string; email: string }>): void {
  server.use(
    http.get('/api/projects/:projectId/invitable-members', () => HttpResponse.json(members)),
  )
}

describe('ProjectMembersView', () => {
  it('renders without errors', async () => {
    const wrapper = await render()
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the page header', async () => {
    const wrapper = await render()
    expect(wrapper.find('h1').exists()).toBe(true)
  })

  it('hides the invite form from a caller the server does not treat as a moderator', async () => {
    const wrapper = await render()
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.find('.invite-form').exists()).toBe(false)
  })

  it('shows the invite form to an inherited owner who holds no project_members row', async () => {
    asModerator()
    const wrapper = await render()
    await vi.waitFor(() => {
      if (!wrapper.find('.invite-form').exists()) throw new Error('no invite form yet')
    })
  })

  it('offers the parent-Org pool as a picker instead of a typed address', async () => {
    asModerator()
    withPool([{ user_id: 'u_2', email: 'student@example.com' }])
    const wrapper = await render()

    await vi.waitFor(() => {
      const options = wrapper.findAll('#invitePickedUser option')
      if (options.length === 0) throw new Error('picker not populated yet')
    })
    const labels = wrapper.findAll('#invitePickedUser option').map(o => o.text())
    expect(labels).toContain('student@example.com')
    // The picker replaces the email field rather than sitting beside it.
    expect(wrapper.find('#inviteEmail').exists()).toBe(false)
  })

  it('falls back to the email field when the pool is empty', async () => {
    asModerator()
    withPool([])
    const wrapper = await render()
    await vi.waitFor(() => {
      if (!wrapper.find('#inviteEmail').exists()) throw new Error('no email field yet')
    })
    expect(wrapper.find('#invitePickedUser').exists()).toBe(false)
  })

  it('renders the returned accept link after inviting a picked member', async () => {
    asModerator()
    withPool([{ user_id: 'u_2', email: 'student@example.com' }])
    server.use(
      http.post('/api/projects/:projectId/invites', () =>
        HttpResponse.json(
          {
            id: 'inv_1',
            scope_id: 'proj_1',
            invitee_email: 'student@example.com',
            role: 'member',
            expires_at: '2026-09-01T00:00:00Z',
            accept_url: ACCEPT_URL,
          },
          { status: 201 },
        ),
      ),
    )
    const wrapper = await render()

    await vi.waitFor(() => {
      if (wrapper.findAll('#invitePickedUser option').length === 0) {
        throw new Error('picker not populated yet')
      }
    })
    await wrapper.find('#invitePickedUser').setValue('u_2')
    await wrapper.find('.invite-form').trigger('submit')

    await vi.waitFor(() => {
      if (!wrapper.find('.invite-link').exists()) throw new Error('no accept link yet')
    })
    const field = wrapper.find('.invite-link input')
    expect((field.element as HTMLInputElement).value).toBe(ACCEPT_URL)
    expect(wrapper.find('.invite-link').text()).toContain('student@example.com')
  })

  // AC-3's client-side half: a create response with no `accept_url` must not
  // leave a stale link on screen, and a read path never carries one.
  it('shows no accept link when the response omits it', async () => {
    asModerator()
    withPool([])
    server.use(
      http.post('/api/projects/:projectId/invites', () =>
        HttpResponse.json(
          {
            id: 'inv_1',
            scope_id: 'proj_1',
            invitee_email: 'outsider@example.com',
            role: 'member',
            expires_at: '2026-09-01T00:00:00Z',
          },
          { status: 201 },
        ),
      ),
    )
    const wrapper = await render()

    await vi.waitFor(() => {
      if (!wrapper.find('#inviteEmail').exists()) throw new Error('no email field yet')
    })
    await wrapper.find('#inviteEmail').setValue('outsider@example.com')
    await wrapper.find('.invite-form').trigger('submit')
    await new Promise(r => setTimeout(r, 50))

    expect(wrapper.find('.invite-link').exists()).toBe(false)
  })
})
