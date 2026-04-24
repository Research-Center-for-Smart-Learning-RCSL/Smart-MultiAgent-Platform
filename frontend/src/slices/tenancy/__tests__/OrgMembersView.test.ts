import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import OrgMembersView from '../views/OrgMembersView.vue'

const routes = [
  { path: '/orgs/:id/members', name: 'tenancy.orgMembers', component: OrgMembersView },
]

describe('OrgMembersView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgMembersView, {
      routes,
      initialRoute: '/orgs/org_1/members',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains an invite form with email input and role select', async () => {
    const wrapper = await renderView(OrgMembersView, {
      routes,
      initialRoute: '/orgs/org_1/members',
    })
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('select').exists()).toBe(true)
  })
})
