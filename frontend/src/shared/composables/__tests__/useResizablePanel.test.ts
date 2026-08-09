import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useResizablePanel, type ResizablePanel } from '../useResizablePanel'

const KEY = 'test-panel-w'
const OPTS = {
  storageKey: KEY,
  defaultWidth: 200,
  min: 200,
  max: 720,
  maxViewportFraction: 0.45,
}

function setViewport(width: number): void {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true })
}

/** Mounts the composable so `onScopeDispose` has a scope, and returns its API
 *  plus the wrapper so a test can trigger teardown. */
function mountPanel(): { panel: ResizablePanel; unmount: () => void } {
  let panel!: ResizablePanel
  const wrapper = mount(
    defineComponent({
      setup() {
        panel = useResizablePanel(OPTS)
        return () => h('div')
      },
    }),
  )
  return { panel, unmount: () => wrapper.unmount() }
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

  // AC-6
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

  it('caps at the viewport fraction on a narrow window, below the hard ceiling', () => {
    setViewport(800)
    const { panel } = mountPanel()
    panel.setWidth(700)
    // 45% of 800 is 360, which binds before the 720 ceiling does.
    expect(panel.maxWidth.value).toBe(360)
    expect(panel.width.value).toBe(360)
  })

  // AC-7
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

  it('persists an explicit choice', () => {
    const { panel } = mountPanel()
    panel.setWidth(480)
    expect(localStorage.getItem(KEY)).toBe('480')
  })

  // AC-8 — the reason the stored choice and the applied width are separate
  // values. A shrunken window must not silently overwrite what the user picked.
  it('re-clamps when the window shrinks and restores the choice when it grows back', async () => {
    const { panel } = mountPanel()
    panel.setWidth(700)
    expect(panel.width.value).toBe(700)

    setViewport(1000)
    window.dispatchEvent(new Event('resize'))
    await Promise.resolve()
    expect(panel.width.value).toBe(450)
    expect(localStorage.getItem(KEY)).toBe('700')

    setViewport(2000)
    window.dispatchEvent(new Event('resize'))
    await Promise.resolve()
    expect(panel.width.value).toBe(700)
  })

  it('nudges from the width actually in effect, not the stored one', async () => {
    const { panel } = mountPanel()
    panel.setWidth(700)
    setViewport(1000)
    window.dispatchEvent(new Event('resize'))
    await Promise.resolve()

    panel.nudge(-16)
    expect(panel.width.value).toBe(434)
  })

  it('resets to the default width', () => {
    const { panel } = mountPanel()
    panel.setWidth(600)
    panel.reset()
    expect(panel.width.value).toBe(200)
  })

  it('ignores a non-finite width', () => {
    const { panel } = mountPanel()
    panel.setWidth(600)
    panel.setWidth(Number.NaN)
    expect(panel.width.value).toBe(600)
  })

  it('degrades rather than throwing when storage is unavailable', () => {
    const { panel } = mountPanel()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => panel.setWidth(500)).not.toThrow()
    expect(panel.width.value).toBe(500)
  })

  it('stops tracking the viewport once torn down', async () => {
    const { panel, unmount } = mountPanel()
    panel.setWidth(700)
    unmount()

    setViewport(1000)
    window.dispatchEvent(new Event('resize'))
    await Promise.resolve()
    expect(panel.width.value).toBe(700)
  })
})
