import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import AdminOpsView from '../views/AdminOpsView.vue'

describe('AdminOpsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminOpsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders GraphRAG reset form and resource restore form', async () => {
    const wrapper = await renderView(AdminOpsView)
    const forms = wrapper.findAll('form')
    expect(forms.length).toBe(2)
    expect(wrapper.find('select').exists()).toBe(true)
    expect(wrapper.findAll('input').length).toBeGreaterThanOrEqual(2)
  })
})
