import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import OrgMembersView from '../views/OrgMembersView.vue'

const routes = [
  { path: '/orgs/:id/members', name: 'tenancy.orgMembers', component: OrgMembersView },
  { path: '/orgs/:id', name: 'tenancy.orgDetail', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

const ACCEPT_URL = 'https://smap.example/?invite=1#token=org-token'

function render() {
  return renderView(OrgMembersView, { routes, initialRoute: '/orgs/org_1/members' })
}

// Org membership is not inherited from anywhere, so the member list *is* the
// authority here — unlike the project view.
function seedOwner(): void {
  server.use(
    http.get('/api/orgs/:orgId/members', () =>
      HttpResponse.json([
        {
          user_id: 'u_1',
          email: 'owner@example.com',
          role: 'owner',
          is_original_creator: true,
          joined_at: '2026-01-01T00:00:00Z',
        },
      ]),
    ),
  )
}

function signIn(): void {
  useSessionStore().me = {
    id: 'u_1',
    email: 'owner@example.com',
    email_verified: true,
    is_admin: false,
    status: 'active',
  }
}

describe('OrgMembersView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgMembersView, {
      routes,
      initialRoute: '/orgs/org_1/members',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the page header', async () => {
    const wrapper = await renderView(OrgMembersView, {
      routes,
      initialRoute: '/orgs/org_1/members',
    })
    expect(wrapper.find('h1').exists()).toBe(true)
  })

  // Q-6: an Org invite keeps the typed-address field — there is no pool that
  // could be picked from without disclosing the platform user directory.
  it('keeps the typed-address field and offers no picker', async () => {
    seedOwner()
    const wrapper = await render()
    signIn()
    await vi.waitFor(() => {
      if (!wrapper.find('#inviteEmail').exists()) throw new Error('no email field yet')
    })
    expect(wrapper.find('#invitePickedUser').exists()).toBe(false)
  })

  it('renders the returned accept link after inviting', async () => {
    seedOwner()
    server.use(
      http.post('/api/orgs/:orgId/invites', () =>
        HttpResponse.json(
          {
            id: 'inv_1',
            scope_id: 'org_1',
            scope_type: 'org',
            invitee_email: 'newcomer@example.com',
            role: 'member',
            state: 'pending',
            expires_at: '2026-09-01T00:00:00Z',
            accept_url: ACCEPT_URL,
          },
          { status: 201 },
        ),
      ),
    )
    const wrapper = await render()
    signIn()

    await vi.waitFor(() => {
      if (!wrapper.find('#inviteEmail').exists()) throw new Error('no email field yet')
    })
    await wrapper.find('#inviteEmail').setValue('newcomer@example.com')
    await wrapper.find('.invite-form').trigger('submit')

    await vi.waitFor(() => {
      if (!wrapper.find('.invite-link').exists()) throw new Error('no accept link yet')
    })
    const field = wrapper.find('.invite-link input')
    expect((field.element as HTMLInputElement).value).toBe(ACCEPT_URL)
  })
})
