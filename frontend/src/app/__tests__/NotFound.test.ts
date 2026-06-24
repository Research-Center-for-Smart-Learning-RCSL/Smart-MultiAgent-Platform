import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import NotFound from '../views/NotFound.vue'

describe('NotFound', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(NotFound)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays a not-found message and a link home', async () => {
    const wrapper = await renderView(NotFound)
    expect(wrapper.text()).toContain('app.notFoundTitle')
    expect(wrapper.text()).toContain('app.backToHome')
  })
})
