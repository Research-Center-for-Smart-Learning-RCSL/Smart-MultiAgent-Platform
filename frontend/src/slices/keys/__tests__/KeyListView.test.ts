import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import KeyListView from '../views/KeyListView.vue'

describe('KeyListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(KeyListView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders page header and upload button', async () => {
    const wrapper = await renderView(KeyListView)
    const text = wrapper.text()
    expect(text).toContain('keys.list.title')
    expect(text).toContain('keys.form.submit')
  })
})
