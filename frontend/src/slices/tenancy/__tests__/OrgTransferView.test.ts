import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import OrgTransferView from '../views/OrgTransferView.vue'

const routes = [
  { path: '/orgs/:id/transfer', name: 'tenancy.orgTransfer', component: OrgTransferView },
]

describe('OrgTransferView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgTransferView, {
      routes,
      initialRoute: '/orgs/org_1/transfer',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows initiate form when no pending transfer', async () => {
    const wrapper = await renderView(OrgTransferView, {
      routes,
      initialRoute: '/orgs/org_1/transfer',
    })
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(true)
  })
})
