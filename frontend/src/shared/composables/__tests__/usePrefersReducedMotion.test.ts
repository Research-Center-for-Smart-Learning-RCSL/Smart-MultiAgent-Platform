import { describe, it, expect, vi, afterEach } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { usePrefersReducedMotion } from '../usePrefersReducedMotion'

const Harness = defineComponent({
  setup: () => ({ reduced: usePrefersReducedMotion() }),
  render: () => null,
})

describe('usePrefersReducedMotion', () => {
  const original = window.matchMedia

  afterEach(() => {
    window.matchMedia = original
    vi.restoreAllMocks()
  })

  it('is false when matchMedia is unavailable', () => {
    // @ts-expect-error simulate an environment without matchMedia
    window.matchMedia = undefined
    const wrapper = mount(Harness)
    expect((wrapper.vm as unknown as { reduced: boolean }).reduced).toBe(false)
  })

  it('reflects the initial match and live change events', async () => {
    const listeners: Array<() => void> = []
    let matches = true
    window.matchMedia = vi.fn().mockReturnValue({
      get matches() {
        return matches
      },
      addEventListener: (_: string, cb: () => void) => listeners.push(cb),
      removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia

    const wrapper = mount(Harness)
    const vm = wrapper.vm as unknown as { reduced: boolean }
    expect(vm.reduced).toBe(true)

    matches = false
    listeners.forEach((cb) => cb())
    await nextTick()
    expect(vm.reduced).toBe(false)
  })
})
