import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import Landing from '../views/Landing.vue'

describe('Landing', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays a headline and subtitle', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.find('h1').exists()).toBe(true)
    expect(wrapper.find('p').exists()).toBe(true)
  })

  it('has links to register and login', async () => {
    const wrapper = await renderView(Landing)
    expect(wrapper.text()).toContain('app.landing.getStarted')
    expect(wrapper.text()).toContain('app.landing.logIn')
  })
})
