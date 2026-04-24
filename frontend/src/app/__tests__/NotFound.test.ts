import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import NotFound from '../views/NotFound.vue'

describe('NotFound', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(NotFound)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays 404 heading and a link back to home', async () => {
    const wrapper = await renderView(NotFound)
    expect(wrapper.text()).toContain('404')
    const homeLink = wrapper.find('a[href="/"]')
    expect(homeLink.exists()).toBe(true)
  })
})
