import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { defineComponent, h, ref, type Ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useResizablePanel, type ResizablePanel, type ResizablePanelOptions } from '../useResizablePanel'

const KEY = 'test-panel-w'
const BASE: ResizablePanelOptions = {
  storageKey: KEY,
  defaultWidth: 200,
  min: 200,
  max: 720,
  reserve: 590,
}

function setViewport(width: number): void {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true })
}

/** Mounts the composable so `onScopeDispose` has a scope. */
function mountPanel(over: Partial<ResizablePanelOptions> = {}): {
  panel: ResizablePanel
  unmount: () => void
} {
  let panel!: ResizablePanel
  const wrapper = mount(
    defineComponent({
      setup() {
        panel = useResizablePanel({ ...BASE, ...over })
        return () => h('div')
      },
    }),
  )
  return { panel, unmount: () => wrapper.unmount() }
}

// jsdom ships no ResizeObserver, so the container path needs one. Returns a
// `resize` helper that drives the observed element's reported width.
function stubResizeObserver(): { resize: (width: number) => void; restore: () => void } {
  const callbacks: ResizeObserverCallback[] = []
  class StubRO {
    constructor(cb: ResizeObserverCallback) { callbacks.push(cb) }
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  }
  const original = globalThis.ResizeObserver
  globalThis.ResizeObserver = StubRO as unknown as typeof ResizeObserver
  return {
    resize: (width: number) => {
      for (const cb of callbacks) {
        cb([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
      }
    },
    restore: () => { globalThis.ResizeObserver = original },
  }
}

function containerOf(width: number): Ref<HTMLElement | null> {
  const el = document.createElement('div')
  el.getBoundingClientRect = () => ({ width }) as DOMRect
  return ref(el)
}

describe('useResizablePanel', () => {
  const originalWidth = window.innerWidth

  beforeEach(() => {
    localStorage.clear()
    setViewport(2000)
  })

  afterEach(() => {
    setViewport(originalWidth)
    localStorage.clear()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('starts at the default width when nothing is stored', () => {
    const { panel } = mountPanel()
    expect(panel.width.value).toBe(200)
  })

  it('reads a previously chosen width', () => {
    localStorage.setItem(KEY, '540')
    const { panel } = mountPanel()
    expect(panel.width.value).toBe(540)
  })

  it('refuses a width below the minimum', () => {
    const { panel } = mountPanel()
    panel.setWidth(40)
    expect(panel.width.value).toBe(200)
  })

  it('refuses a width above the hard ceiling', () => {
    const { panel } = mountPanel()
    panel.setWidth(5000)
    expect(panel.width.value).toBe(720)
  })

  it('clamps an out-of-range stored value on read', () => {
    localStorage.setItem(KEY, '9999')
    const { panel } = mountPanel()
    expect(panel.width.value).toBe(720)
  })

  it('ignores a corrupt stored value', () => {
    localStorage.setItem(KEY, 'not-a-number')
    const { panel } = mountPanel()
    expect(panel.width.value).toBe(200)
  })

  it('ignores a non-finite width', () => {
    const { panel } = mountPanel()
    panel.setWidth(600)
    panel.setWidth(Number.NaN)
    expect(panel.width.value).toBe(600)
  })

  it('resets to the default width', () => {
    const { panel } = mountPanel()
    panel.setWidth(600)
    panel.reset()
    expect(panel.width.value).toBe(200)
  })

  // The ceiling has to come from the box the panel is a track of. Measuring the
  // viewport instead over-counts by whatever the app shell spends first, which
  // let the neighbouring column be squeezed past its floor.
  describe('ceiling derived from the container', () => {
    let ro: ReturnType<typeof stubResizeObserver>

    beforeEach(() => { ro = stubResizeObserver() })
    afterEach(() => { ro.restore() })

    it('reserves the neighbouring columns out of the container width', () => {
      const { panel } = mountPanel({ container: containerOf(1020) })
      // 1020 - 590 reserved = 430 of room, below the 720 hard ceiling.
      expect(panel.maxWidth.value).toBe(430)
      panel.setWidth(700)
      expect(panel.width.value).toBe(430)
    })

    it('still honours the hard ceiling when the container is roomy', () => {
      const { panel } = mountPanel({ container: containerOf(2000) })
      expect(panel.maxWidth.value).toBe(720)
    })

    it('never reports a ceiling below the minimum, however cramped', () => {
      const { panel } = mountPanel({ container: containerOf(400) })
      expect(panel.maxWidth.value).toBe(200)
    })

    // The app sidebar collapsing resizes .chatroom without firing a window
    // `resize`; only observing the element catches it.
    it('re-clamps when the container shrinks and restores the choice when it grows', () => {
      const { panel } = mountPanel({ container: containerOf(2000) })
      panel.setWidth(700)
      expect(panel.width.value).toBe(700)

      ro.resize(1020)
      expect(panel.width.value).toBe(430)
      expect(panel.maxWidth.value).toBe(430)

      ro.resize(2000)
      expect(panel.width.value).toBe(700)
    })

    it('nudges from the width actually in effect, not the stored one', () => {
      const { panel } = mountPanel({ container: containerOf(2000) })
      panel.setWidth(700)
      ro.resize(1020)

      panel.nudge(-16)
      expect(panel.width.value).toBe(414)
    })
  })

  it('falls back to the viewport when no container is given', () => {
    setViewport(1200)
    const { panel } = mountPanel()
    expect(panel.maxWidth.value).toBe(610)

    setViewport(2000)
    window.dispatchEvent(new Event('resize'))
    expect(panel.maxWidth.value).toBe(720)
  })

  describe('persistence', () => {
    it('persists an explicit choice', async () => {
      vi.useFakeTimers()
      const { panel } = mountPanel()
      panel.setWidth(480)
      vi.advanceTimersByTime(200)
      expect(localStorage.getItem(KEY)).toBe('480')
    })

    // A drag emits a value per pointermove. Writing each one put 60+ synchronous
    // storage writes per second on the main thread while the grid reflowed.
    it('writes once for a burst of changes, not once per change', () => {
      vi.useFakeTimers()
      const setItem = vi.spyOn(Storage.prototype, 'setItem')
      const { panel } = mountPanel()

      for (let px = 300; px < 400; px += 5) panel.setWidth(px)
      expect(setItem).not.toHaveBeenCalled()

      vi.advanceTimersByTime(200)
      expect(setItem).toHaveBeenCalledTimes(1)
      expect(localStorage.getItem(KEY)).toBe('395')
    })

    it('flushes a pending write when torn down mid-drag', () => {
      vi.useFakeTimers()
      const { panel, unmount } = mountPanel()
      panel.setWidth(500)
      unmount()
      expect(localStorage.getItem(KEY)).toBe('500')
    })

    it('degrades rather than throwing when storage is unavailable', () => {
      vi.useFakeTimers()
      const { panel } = mountPanel()
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('QuotaExceededError')
      })
      panel.setWidth(500)
      expect(() => vi.advanceTimersByTime(200)).not.toThrow()
      expect(panel.width.value).toBe(500)
    })
  })

  it('stops tracking the viewport once torn down', () => {
    const { panel, unmount } = mountPanel()
    panel.setWidth(700)
    unmount()

    setViewport(900)
    window.dispatchEvent(new Event('resize'))
    expect(panel.width.value).toBe(700)
  })
})
