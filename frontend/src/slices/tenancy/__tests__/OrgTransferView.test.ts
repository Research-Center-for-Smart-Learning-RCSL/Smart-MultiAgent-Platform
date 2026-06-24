import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import OrgTransferView from '../views/OrgTransferView.vue'

const routes = [
  { path: '/orgs/:id/transfer', name: 'tenancy.orgTransfer', component: OrgTransferView },
  { path: '/orgs/:id', name: 'tenancy.orgDetail', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

describe('OrgTransferView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgTransferView, {
      routes,
      initialRoute: '/orgs/org_1/transfer',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('hides initiate form when user is not original creator', async () => {
    const wrapper = await renderView(OrgTransferView, {
      routes,
      initialRoute: '/orgs/org_1/transfer',
    })
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
  })
})
