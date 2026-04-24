import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import AdminImpersonateLauncher from '../views/AdminImpersonateLauncher.vue'

describe('AdminImpersonateLauncher', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminImpersonateLauncher)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders user ID input and start button', async () => {
    const wrapper = await renderView(AdminImpersonateLauncher)
    const form = wrapper.find('form')
    expect(form.exists()).toBe(true)
    expect(form.find('input').exists()).toBe(true)
    expect(form.find('button[type="submit"]').exists()).toBe(true)
  })
})
