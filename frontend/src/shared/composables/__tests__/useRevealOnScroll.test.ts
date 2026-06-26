import { describe, it, expect, vi, afterEach } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useRevealOnScroll } from '../useRevealOnScroll'

const Harness = defineComponent({
  setup() {
    return useRevealOnScroll()
  },
  render() {
    return h('div', { ref: 'el' })
  },
})

const observe = vi.fn()
const disconnect = vi.fn()
let lastCallback: IntersectionObserverCallback | null = null

class MockIO {
  observe = observe
  disconnect = disconnect
  unobserve = vi.fn()
  takeRecords = vi.fn()
  constructor(cb: IntersectionObserverCallback) {
    lastCallback = cb
  }
}

describe('useRevealOnScroll', () => {
  const originalIO = globalThis.IntersectionObserver

  afterEach(() => {
    globalThis.IntersectionObserver = originalIO
    observe.mockClear()
    disconnect.mockClear()
    lastCallback = null
  })

  it('reveals immediately when IntersectionObserver is unavailable', () => {
    // @ts-expect-error force the graceful-degradation path
    globalThis.IntersectionObserver = undefined
    const wrapper = mount(Harness)
    expect((wrapper.vm as unknown as { revealed: boolean }).revealed).toBe(true)
  })

  it('stays hidden until intersection, then reveals and disconnects', () => {
    globalThis.IntersectionObserver = MockIO as unknown as typeof IntersectionObserver
    const wrapper = mount(Harness)
    const vm = wrapper.vm as unknown as { revealed: boolean }
    expect(observe).toHaveBeenCalledTimes(1)
    expect(vm.revealed).toBe(false)

    lastCallback!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
    expect(vm.revealed).toBe(true)
    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('disconnects the observer on unmount even if it never intersected', () => {
    globalThis.IntersectionObserver = MockIO as unknown as typeof IntersectionObserver
    const wrapper = mount(Harness)
    expect(observe).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledTimes(1)
  })
})
