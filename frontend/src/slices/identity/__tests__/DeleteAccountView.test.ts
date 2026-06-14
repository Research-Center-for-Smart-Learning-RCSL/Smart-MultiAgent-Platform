import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import DeleteAccountView from '../views/DeleteAccountView.vue'

describe('DeleteAccountView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(DeleteAccountView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows a password field and a confirmation checkbox', async () => {
    const wrapper = await renderView(DeleteAccountView)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
  })

  it('keeps the delete button disabled until the action is confirmed', async () => {
    const wrapper = await renderView(DeleteAccountView)
    const button = wrapper.find('button[type="submit"]')
    expect(button.exists()).toBe(true)
    expect(button.attributes('disabled')).toBeDefined()

    await wrapper.find('input[type="checkbox"]').setValue(true)
    expect(button.attributes('disabled')).toBeUndefined()
  })
})
