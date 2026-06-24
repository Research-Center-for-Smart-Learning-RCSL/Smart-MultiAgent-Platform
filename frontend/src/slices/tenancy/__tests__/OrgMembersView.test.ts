import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import OrgMembersView from '../views/OrgMembersView.vue'

const routes = [
  { path: '/orgs/:id/members', name: 'tenancy.orgMembers', component: OrgMembersView },
  { path: '/orgs/:id', name: 'tenancy.orgDetail', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

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
})
