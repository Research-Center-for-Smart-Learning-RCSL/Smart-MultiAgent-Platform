import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderView } from '../../../tests/utils'
import LandingIntro from '../components/LandingIntro.vue'

// jsdom has no layout, so getBoundingClientRect is all zeros: the dock
// measurement fails and the component takes its plain-fade exit path here.
describe('LandingIntro', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('renders a decorative overlay hidden from assistive tech', async () => {
    const wrapper = await renderView(LandingIntro)
    const overlay = wrapper.find('.intro')
    expect(overlay.exists()).toBe(true)
    expect(overlay.attributes('aria-hidden')).toBe('true')
    expect(overlay.attributes('role')).toBe('presentation')
  })

  it('draws the seven balls (six satellites + hub) and their links', async () => {
    const wrapper = await renderView(LandingIntro)
    expect(wrapper.findAll('.intro-node')).toHaveLength(7)
    expect(wrapper.findAll('.intro-edge')).toHaveLength(6)
  })

  it('surfaces the skip hint partway through the timeline', async () => {
    const wrapper = await renderView(LandingIntro)
    expect(wrapper.find('.intro__skip--on').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(550)
    expect(wrapper.find('.intro__skip--on').exists()).toBe(true)
  })

  it('lifts away and emits done once the timeline finishes', async () => {
    const wrapper = await renderView(LandingIntro)
    expect(wrapper.find('.intro--leaving').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(1200)
    expect(wrapper.find('.intro--leaving').exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.emitted('done')).toHaveLength(1)
  })

  it('fast-forwards to the lift on user input', async () => {
    const wrapper = await renderView(LandingIntro)

    window.dispatchEvent(new Event('pointerdown'))
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.find('.intro--leaving').exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.emitted('done')).toHaveLength(1)
  })
})
