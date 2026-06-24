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
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
]

describe('OrgDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('displays org name after loading', async () => {
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Test Org')
  })

  it('renders the settings card', async () => {
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    await flushPromises()
    expect(wrapper.find('.card-title').exists()).toBe(true)
  })
})
