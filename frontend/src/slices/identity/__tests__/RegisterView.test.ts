import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import RegisterView from '../views/RegisterView.vue'

describe('RegisterView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows email, password, and captcha fields', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
    const inputs = wrapper.findAll('input')
    expect(inputs.length).toBeGreaterThanOrEqual(3)
  })

  it('has a link back to login', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.find('a').exists()).toBe(true)
  })
})
