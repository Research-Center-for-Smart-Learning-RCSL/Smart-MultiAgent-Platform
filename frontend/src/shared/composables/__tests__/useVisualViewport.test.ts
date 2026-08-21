import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { defineComponent, h, nextTick, ref, type Ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useVisualViewport } from '../useVisualViewport'

// T-2 of docs/tasks/2026-08-19-mobile-viewport-and-breakpoints (F-46).
//
// A characterization test, and deliberately one that passed before the fix as
// well as after: the dossier's Q-3 decided the arithmetic here is CORRECT and
// that the defect lives in the CSS it is subtracted from. What made the
// composer sit under the keyboard was the shell resolving against the large
// viewport while this formula measures the layout viewport (§5's derivation).
//
// So this pins the formula rather than proving a fix. Its value is that the
// obvious "repair" — compensating for the toolbar here — would now turn a test
// red instead of silently double-counting once the shell is in dvh.

interface StubViewport {
  height: number
  offsetTop: number
  listeners: Record<string, Array<() => void>>
  fire: (event: 'resize' | 'scroll') => void
}

function stubVisualViewport(height: number, offsetTop = 0): StubViewport {
  const listeners: Record<string, Array<() => void>> = { resize: [], scroll: [] }
  const vv = {
    height,
    offsetTop,
    addEventListener: (e: string, cb: () => void) => listeners[e]?.push(cb),
    removeEventListener: (e: string, cb: () => void) => {
      listeners[e] = (listeners[e] ?? []).filter((f) => f !== cb)
    },
  }
  Object.defineProperty(window, 'visualViewport', { value: vv, configurable: true, writable: true })
  return {
    get height() { return vv.height },
    set height(v: number) { vv.height = v },
    get offsetTop() { return vv.offsetTop },
    set offsetTop(v: number) { vv.offsetTop = v },
    listeners,
    fire: (event) => listeners[event]?.forEach((cb) => cb()),
  }
}

function removeVisualViewport(): void {
  Object.defineProperty(window, 'visualViewport', {
    value: undefined,
    configurable: true,
    writable: true,
  })
}

function setInnerHeight(px: number): void {
  Object.defineProperty(window, 'innerHeight', { value: px, configurable: true, writable: true })
}

/** Mounts the composable so its onMounted/onBeforeUnmount hooks have a host. */
function mountViewport(enabled?: Ref<boolean>): {
  keyboardInset: Ref<number>
  unmount: () => void
} {
  let keyboardInset!: Ref<number>
  const wrapper = mount(
    defineComponent({
      setup() {
        ;({ keyboardInset } = useVisualViewport(enabled ? () => enabled.value : undefined))
        return () => h('div')
      },
    }),
  )
  return { keyboardInset, unmount: () => wrapper.unmount() }
}

beforeEach(() => {
  // jsdom has no rAF budget of its own here; run the coalesced read inline so
  // a test can assert without awaiting a frame.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  setInnerHeight(800)
})

afterEach(() => {
  vi.unstubAllGlobals()
  removeVisualViewport()
})

describe('useVisualViewport', () => {
  it('reports no inset where the API is unavailable', () => {
    removeVisualViewport()
    const { keyboardInset, unmount } = mountViewport()

    expect(keyboardInset.value).toBe(0)
    unmount()
  })

  it('reports no inset while the two viewports agree', () => {
    stubVisualViewport(800)
    const { keyboardInset, unmount } = mountViewport()

    expect(keyboardInset.value).toBe(0)
    unmount()
  })

  it('reports the shortfall when the keyboard shrinks the visual viewport', () => {
    const vv = stubVisualViewport(800)
    const { keyboardInset, unmount } = mountViewport()

    vv.height = 500
    vv.fire('resize')

    expect(keyboardInset.value).toBe(300)
    unmount()
  })

  // offsetTop is the visual viewport's own scroll offset within the layout
  // viewport (pinch-zoom, or iOS scrolling the page under a focused field).
  // Space above the visual viewport is not keyboard, so it must not be counted
  // as inset — dropping this term over-lifts the composer by that offset.
  it('subtracts the visual viewport offset rather than counting it as keyboard', () => {
    const vv = stubVisualViewport(500, 120)
    const { keyboardInset, unmount } = mountViewport()

    expect(keyboardInset.value).toBe(180)

    vv.offsetTop = 0
    vv.fire('scroll')
    expect(keyboardInset.value).toBe(300)

    unmount()
  })

  // A visual viewport TALLER than the layout viewport is reachable during URL
  // bar collapse. Without the clamp the inset goes negative and the consuming
  // `calc(100% - var(--kb-inset))` grows the chatroom past its container.
  it('clamps a negative overlap to zero', () => {
    stubVisualViewport(900)
    const { keyboardInset, unmount } = mountViewport()

    expect(keyboardInset.value).toBe(0)
    unmount()
  })

  it('rounds to whole pixels', () => {
    stubVisualViewport(500.4)
    const { keyboardInset, unmount } = mountViewport()

    expect(keyboardInset.value).toBe(300)
    unmount()
  })

  it('attaches only while enabled and resets to zero on detach', async () => {
    const vv = stubVisualViewport(500)
    const enabled = ref(false)
    const { keyboardInset, unmount } = mountViewport(enabled)

    // finally, not a trailing call: a failed assertion below would otherwise
    // leave the component mounted with its listeners still on the stub.
    try {
      // Never attached: the desktop case, where the inset is never consumed.
      expect(keyboardInset.value).toBe(0)
      expect(vv.listeners.resize).toHaveLength(0)

      enabled.value = true
      await nextTick()
      expect(vv.listeners.resize).toHaveLength(1)
      expect(keyboardInset.value).toBe(300)

      enabled.value = false
      await nextTick()
      expect(vv.listeners.resize).toHaveLength(0)
      // Leaving a stale inset behind would shrink the chatroom on a viewport
      // that no longer has a keyboard.
      expect(keyboardInset.value).toBe(0)
    } finally {
      unmount()
    }
  })
})
