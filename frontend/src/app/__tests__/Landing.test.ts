import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import Landing from '../views/Landing.vue'

describe('Landing', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays the i18n title and message', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.find('h1').exists()).toBe(true)
    expect(wrapper.find('p').exists()).toBe(true)
  })
})
