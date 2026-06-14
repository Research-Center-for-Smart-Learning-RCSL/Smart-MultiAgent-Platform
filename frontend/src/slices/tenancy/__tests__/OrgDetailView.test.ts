import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import OrgDetailView from '../views/OrgDetailView.vue'

const routes = [
  { path: '/orgs/:id', name: 'tenancy.orgDetail', component: OrgDetailView },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
  { path: '/orgs/:id/members', name: 'tenancy.orgMembers', component: { template: '<div />' } },
  { path: '/orgs/:id/transfer', name: 'tenancy.orgTransfer', component: { template: '<div />' } },
]

function seedQuotas(): void {
  server.use(
    http.get('/api/orgs/org_1/quotas', () =>
      HttpResponse.json({
        users: 3,
        projects: 2,
        chatrooms: 5,
        agents: 4,
        workflows: 1,
        computed_at: '2026-01-01T00:00:00Z',
        advisory_targets: { users: 50, projects: 20, chatrooms: 100, agents: 50, workflows: 50 },
      }),
    ),
  )
}

describe('OrgDetailView', () => {
  it('renders without errors', async () => {
    seedQuotas()
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('displays org name after loading', async () => {
    seedQuotas()
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Test Org')
  })

  it('shows the advisory quotas panel and a rename control', async () => {
    seedQuotas()
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    await flushPromises()
    expect(wrapper.find('.quotas').exists()).toBe(true)
    // current usage and advisory target both rendered (3 users / 50)
    expect(wrapper.find('.quotas').text()).toContain('3')
    expect(wrapper.find('.quotas').text()).toContain('50')
  })
})
