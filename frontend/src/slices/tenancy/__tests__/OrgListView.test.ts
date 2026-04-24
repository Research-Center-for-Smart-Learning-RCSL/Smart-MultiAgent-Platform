import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import OrgListView from '../views/OrgListView.vue'

describe('OrgListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(OrgListView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows a create form with a submit button', async () => {
    const wrapper = await renderView(OrgListView)
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})
