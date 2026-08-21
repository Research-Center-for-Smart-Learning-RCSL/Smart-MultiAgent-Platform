import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
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

  // F-26: the whole template sat behind the loading flag, page header included,
  // so a cold load painted nothing but a 24px spinner row at the top left. The
  // replacement is a header-shaped skeleton, not a header titled "Loading" —
  // the h1 on a detail page is the entity's name, and a status string in it is
  // both a false accessible name and a broken invariant (it took two e2e specs
  // down when this shipped that way).
  it('paints a header-shaped skeleton while the org is still loading', async () => {
    // Deliberately no flushPromises — the query is still in flight here.
    const wrapper = await renderView(OrgDetailView, {
      routes,
      initialRoute: '/orgs/org_1',
    })
    expect(wrapper.find('.s-spinner').exists()).toBe(true)
    expect(wrapper.find('.s-skeleton').exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(false)
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
