// scrollToMessage: the search "jump to message" path. Verifies the loaded /
// not-loaded branch and the transient highlight lifecycle.
//
// Plus T-1/T-2/T-4/T-5 of docs/tasks/2026-08-19-chatroom-scroll-and-composer.
//
// jsdom performs no layout: scrollHeight, clientHeight and scrollTop are
// writable stubs that never reflect content, and neither observer exists. So
// the prepend cases below are pure arithmetic on those stubs, and the observer
// cases prove the wiring and the gates. Where the feed actually lands is AC-1
// and AC-13, both browser checks.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'

import { computed, defineComponent, nextTick, ref, h } from 'vue'

import { useChatroomScroll } from '../composables/useChatroomScroll'

type Scroll = ReturnType<typeof useChatroomScroll>

function mountScroll(messageIds: string[]): {
  wrapper: VueWrapper
  scroll: Scroll
  el: HTMLElement
  ids: { value: string[] }
} {
  let scroll!: Scroll
  const ids = ref<string[]>([...messageIds])
  let el!: HTMLElement
  const Host = defineComponent({
    setup() {
      const listRef = ref<HTMLElement | null>(null)
      scroll = useChatroomScroll(
        listRef,
        computed(() => ids.value),
      )
      return () => {
        const node = h(
          'ol',
          { ref: listRef },
          ids.value.map((id) => h('li', { id: `msg-${id}` }, id)),
        )
        return node
      }
    },
  })
  const wrapper = mount(Host, { attachTo: document.body })
  el = wrapper.element as HTMLElement
  return { wrapper, scroll, el, ids }
}

/** jsdom reports 0 for every geometry property; give the stub real numbers so
 *  the restore arithmetic has something to be right or wrong about. */
function setGeometry(el: HTMLElement, scrollHeight: number, scrollTop: number): void {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight })
  el.scrollTop = scrollTop
}

describe('useChatroomScroll scrollToMessage', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    vi.useFakeTimers()
    // jsdom has no scrollIntoView; the composable guards on it but provide a
    // spy so we can assert it is invoked for a loaded message.
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.useRealTimers()
  })

  it('scrolls to and flashes a loaded message, then clears the flash', () => {
    const mounted = mountScroll(['m_1', 'm_2'])
    wrapper = mounted.wrapper

    expect(mounted.scroll.scrollToMessage('m_2')).toBe(true)
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(mounted.scroll.highlightId.value).toBe('m_2')

    vi.advanceTimersByTime(1600)
    expect(mounted.scroll.highlightId.value).toBeNull()
  })

  it('returns false and does not flash when the message is not loaded', () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper

    expect(mounted.scroll.scrollToMessage('m_999')).toBe(false)
    expect(mounted.scroll.highlightId.value).toBeNull()
  })
})

// T-1 (F-11). The restore captured only scrollHeight and assigned
// `scrollHeight - savedHeight`, which places every reader at the height delta
// regardless of where they actually were -- so the message being read moved
// down out of view by however far they had scrolled.
describe('useChatroomScroll prepend restoration (F-11)', () => {
  let wrapper: VueWrapper | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('keeps the reader where they were inside the newly taller feed', async () => {
    const mounted = mountScroll(['m_1', 'm_2'])
    wrapper = mounted.wrapper
    await nextTick()

    setGeometry(mounted.el, 2000, 240)
    mounted.scroll.captureBeforePrepend()

    setGeometry(mounted.el, 3000, 240)
    mounted.scroll.restoreAfterPrepend()
    await nextTick()

    // savedScrollTop + (newHeight - savedHeight) = 240 + 1000.
    // The pre-fix expression gives 1000, losing the reader's 240px offset.
    expect(mounted.el.scrollTop).toBe(1240)
  })

  it('compounds correctly across two successive loads', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    setGeometry(mounted.el, 2000, 240)
    mounted.scroll.captureBeforePrepend()
    setGeometry(mounted.el, 3000, 240)
    mounted.scroll.restoreAfterPrepend()
    await nextTick()
    expect(mounted.el.scrollTop).toBe(1240)

    mounted.scroll.captureBeforePrepend()
    setGeometry(mounted.el, 4200, 1240)
    mounted.scroll.restoreAfterPrepend()
    await nextTick()
    expect(mounted.el.scrollTop).toBe(2440)
  })

  it('never restores to a negative offset', async () => {
    // A prepend that somehow shortens the feed (a dedupe dropping more than it
    // added) must not produce a negative scrollTop.
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    setGeometry(mounted.el, 2000, 10)
    mounted.scroll.captureBeforePrepend()
    setGeometry(mounted.el, 1000, 10)
    mounted.scroll.restoreAfterPrepend()
    await nextTick()

    expect(mounted.el.scrollTop).toBe(0)
  })
})

// T-2 (F-12). newCount was a raw delta of the list length, which reads "the
// list got longer" as "messages arrived" -- true only for an append-only list,
// and loadEarlierPage prepends a page of 100.
describe('useChatroomScroll unseen counter (F-12)', () => {
  let wrapper: VueWrapper | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  /** Put the reader in the scrolled-up state the pill exists for. */
  async function scrollUp(mounted: ReturnType<typeof mountScroll>): Promise<void> {
    await nextTick()
    setGeometry(mounted.el, 5000, 100)
    Object.defineProperty(mounted.el, 'clientHeight', { configurable: true, value: 500 })
    mounted.el.dispatchEvent(new Event('scroll'))
    await nextTick()
    expect(mounted.scroll.atBottom.value).toBe(false)
  }

  it('does not count a page of prepended history', async () => {
    const mounted = mountScroll(['m_10', 'm_11', 'm_12'])
    wrapper = mounted.wrapper
    await scrollUp(mounted)

    const older = Array.from({ length: 100 }, (_, i) => `old_${i}`)
    mounted.scroll.captureBeforePrepend()
    mounted.ids.value = [...older, ...mounted.ids.value]
    await nextTick()
    mounted.scroll.restoreAfterPrepend()
    await nextTick()
    await nextTick()

    expect(mounted.scroll.newCount.value).toBe(0)
    expect(mounted.scroll.showPill.value).toBe(false)
  })

  it('still counts genuinely new items after a prepend', async () => {
    const mounted = mountScroll(['m_10', 'm_11', 'm_12'])
    wrapper = mounted.wrapper
    await scrollUp(mounted)

    const older = Array.from({ length: 100 }, (_, i) => `old_${i}`)
    mounted.scroll.captureBeforePrepend()
    mounted.ids.value = [...older, ...mounted.ids.value]
    await nextTick()
    mounted.scroll.restoreAfterPrepend()
    await nextTick()
    await nextTick()

    mounted.ids.value = [...mounted.ids.value, 'new_1', 'new_2', 'new_3']
    await nextTick()
    await nextTick()

    expect(mounted.scroll.newCount.value).toBe(3)
    expect(mounted.scroll.showPill.value).toBe(true)
  })

  it('counts an approval card like any other feed item', async () => {
    // F-47's pill arm: approvals are in the same ordered list, so this falls
    // out of the same rule rather than needing its own counter.
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await scrollUp(mounted)

    mounted.ids.value = [...mounted.ids.value, 'approval-ap_1']
    await nextTick()
    await nextTick()

    expect(mounted.scroll.newCount.value).toBe(1)
    expect(mounted.scroll.showPill.value).toBe(true)
  })

  it('treats a wholesale replacement as all-unseen', async () => {
    const mounted = mountScroll(['m_1', 'm_2'])
    wrapper = mounted.wrapper
    await scrollUp(mounted)

    // The acknowledged item is gone entirely -- a cache replacement, not an
    // append. Everything rendered is genuinely unseen.
    mounted.ids.value = ['x_1', 'x_2', 'x_3']
    await nextTick()
    await nextTick()

    expect(mounted.scroll.newCount.value).toBe(3)
  })

  it('counts nothing before the reader has acknowledged anything', async () => {
    // Guard for the derivation's own edge: with no acknowledged tail, the count
    // is 0 rather than "everything", so a room's first render never opens with
    // a pill for messages the reader is looking straight at.
    const mounted = mountScroll([])
    wrapper = mounted.wrapper
    await nextTick()

    expect(mounted.scroll.newCount.value).toBe(0)
    expect(mounted.scroll.showPill.value).toBe(false)
  })

  it('clears the count when the reader returns to the bottom', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await scrollUp(mounted)

    mounted.ids.value = [...mounted.ids.value, 'new_1']
    await nextTick()
    await nextTick()
    expect(mounted.scroll.newCount.value).toBe(1)

    setGeometry(mounted.el, 5000, 4600)
    mounted.el.dispatchEvent(new Event('scroll'))
    await nextTick()

    expect(mounted.scroll.atBottom.value).toBe(true)
    expect(mounted.scroll.newCount.value).toBe(0)
    expect(mounted.scroll.showPill.value).toBe(false)
  })
})

// jsdom ships neither observer, so both stubs below follow the established
// precedent: useResizablePanel.test.ts for ResizeObserver and
// useRevealOnScroll.test.ts for IntersectionObserver.

function stubResizeObserver() {
  const callbacks: ResizeObserverCallback[] = []
  const observe = vi.fn()
  const disconnect = vi.fn()
  class StubRO {
    observe = observe
    unobserve = vi.fn()
    disconnect = disconnect
    constructor(cb: ResizeObserverCallback) {
      callbacks.push(cb)
    }
  }
  const original = globalThis.ResizeObserver
  globalThis.ResizeObserver = StubRO as unknown as typeof ResizeObserver
  return {
    observe,
    disconnect,
    /** Report that observed content grew. */
    fire: () => {
      for (const cb of callbacks) cb([] as unknown as ResizeObserverEntry[], {} as ResizeObserver)
    },
    restore: () => {
      globalThis.ResizeObserver = original
    },
  }
}

// T-4 (F-13). Nothing re-scrolled after the asynchronous KaTeX/Mermaid/highlight
// pass, which mutates the DOM directly and so triggers no update hook, and an
// image carries no intrinsic dimensions. The message was scrolled into place at
// its pre-enhancement height and then grew underneath the fold.
//
// This proves the wiring and the gate. That content stopped being pushed below
// the fold is AC-4, a browser check.
describe('useChatroomScroll growth after render (F-13)', () => {
  let wrapper: VueWrapper | null = null
  let ro: ReturnType<typeof stubResizeObserver> | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    ro?.restore()
    ro = null
  })

  it('observes the feed items, not the scrollport that cannot report growth', async () => {
    ro = stubResizeObserver()
    const mounted = mountScroll(['m_1', 'm_2'])
    wrapper = mounted.wrapper
    await nextTick()

    // One call per item. Observing the <ol> as well would be a call too many,
    // and it is the one that could feed a scroll back into the observer.
    expect(ro.observe).toHaveBeenCalledTimes(2)
    const observed = ro.observe.mock.calls.map((c) => (c[0] as HTMLElement).tagName)
    expect(observed).toEqual(['LI', 'LI'])
  })

  it('re-pins to the bottom when observed content grows', async () => {
    ro = stubResizeObserver()
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    setGeometry(mounted.el, 3000, 0)
    ro.fire()

    expect(mounted.el.scrollTop).toBe(3000)
  })

  it('leaves a reader who scrolled up exactly where they are', async () => {
    ro = stubResizeObserver()
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    setGeometry(mounted.el, 5000, 100)
    Object.defineProperty(mounted.el, 'clientHeight', { configurable: true, value: 500 })
    mounted.el.dispatchEvent(new Event('scroll'))
    await nextTick()
    expect(mounted.scroll.atBottom.value).toBe(false)

    ro.fire()

    expect(mounted.el.scrollTop).toBe(100)
  })

  it('picks up items that arrive after mount', async () => {
    ro = stubResizeObserver()
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()
    ro.observe.mockClear()

    mounted.ids.value = [...mounted.ids.value, 'm_2']
    await nextTick()
    await nextTick()

    // A mount-time-only sweep would never see m_2, which is the message whose
    // diagram is most likely to be the one still resolving.
    expect(ro.observe).toHaveBeenCalledTimes(2)
  })

  it('disconnects on unmount', async () => {
    ro = stubResizeObserver()
    const mounted = mountScroll(['m_1'])
    await nextTick()
    ro.disconnect.mockClear()

    mounted.wrapper.unmount()
    wrapper = null

    expect(ro.disconnect).toHaveBeenCalled()
  })

  it('does not construct an observer where the platform has none', async () => {
    const original = globalThis.ResizeObserver
    // @ts-expect-error force the graceful-degradation path (jsdom, SSR)
    globalThis.ResizeObserver = undefined
    try {
      const mounted = mountScroll(['m_1'])
      wrapper = mounted.wrapper
      await nextTick()
      expect(mounted.scroll.atBottom.value).toBe(true)
    } finally {
      globalThis.ResizeObserver = original
    }
  })
})

// T-5 (F-24). ChatroomLoadEarlier had no lifecycle at all: the button existed
// and the auto-trigger half of 07-conversation.md:895 was never built, so
// scrolling to the top of a long room stopped dead.
describe('useChatroomScroll top trigger (F-24)', () => {
  let wrapper: VueWrapper | null = null
  const originalIO = globalThis.IntersectionObserver
  let lastCallback: IntersectionObserverCallback | null = null
  let lastOptions: IntersectionObserverInit | undefined
  const observe = vi.fn()
  const disconnect = vi.fn()

  class StubIO {
    observe = observe
    unobserve = vi.fn()
    disconnect = disconnect
    takeRecords = vi.fn()
    constructor(cb: IntersectionObserverCallback, options?: IntersectionObserverInit) {
      lastCallback = cb
      lastOptions = options
    }
  }

  beforeEach(() => {
    globalThis.IntersectionObserver = StubIO as unknown as typeof IntersectionObserver
    lastCallback = null
    lastOptions = undefined
    observe.mockClear()
    disconnect.mockClear()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    globalThis.IntersectionObserver = originalIO
  })

  /** Drive the sentinel into (or out of) the trigger zone. */
  function reportIntersecting(isIntersecting: boolean): void {
    lastCallback?.(
      [{ isIntersecting } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
  }

  function armed(mounted: ReturnType<typeof mountScroll>) {
    const sentinel = document.createElement('li')
    const onReach = vi.fn()
    const dispose = mounted.scroll.observeTop(sentinel, onReach)
    return { sentinel, onReach, dispose }
  }

  it('expresses the 100px threshold as the root margin, against the feed', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()
    armed(mounted)

    expect(observe).toHaveBeenCalledTimes(1)
    expect(lastOptions?.rootMargin).toBe('100px 0px 0px 0px')
    // Rooted on the feed, not the viewport: the feed is the scroll container,
    // and a viewport-rooted observer would fire on page scroll instead.
    expect(lastOptions?.root).toBe(mounted.el)
  })

  it('calls the handler when the sentinel reaches the zone', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()
    const { onReach } = armed(mounted)

    reportIntersecting(true)

    expect(onReach).toHaveBeenCalledTimes(1)
  })

  it('ignores an entry that is not intersecting', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()
    const { onReach } = armed(mounted)

    reportIntersecting(false)

    expect(onReach).not.toHaveBeenCalled()
  })

  it('is disarmed across a prepend and rearmed after the restore', async () => {
    // Between the DOM insert and the restore the sentinel sits at the top of a
    // taller list. Without this the observer would read that as a second reach
    // and burn through the room's history in one scroll.
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()
    const { onReach } = armed(mounted)

    mounted.scroll.captureBeforePrepend()
    reportIntersecting(true)
    expect(onReach).not.toHaveBeenCalled()

    mounted.scroll.restoreAfterPrepend()
    reportIntersecting(true)
    // The restore runs on nextTick, so the window is still closed here.
    expect(onReach).not.toHaveBeenCalled()

    await nextTick()
    reportIntersecting(true)
    expect(onReach).toHaveBeenCalledTimes(1)
  })

  it('disposes the previous observer when the sentinel is re-observed', async () => {
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    armed(mounted)
    disconnect.mockClear()
    armed(mounted)

    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('disconnects on unmount', async () => {
    const mounted = mountScroll(['m_1'])
    await nextTick()
    armed(mounted)
    disconnect.mockClear()

    mounted.wrapper.unmount()
    wrapper = null

    expect(disconnect).toHaveBeenCalled()
  })

  it('degrades to the button alone where the platform has no observer', async () => {
    // @ts-expect-error force the graceful-degradation path
    globalThis.IntersectionObserver = undefined
    const mounted = mountScroll(['m_1'])
    wrapper = mounted.wrapper
    await nextTick()

    const dispose = mounted.scroll.observeTop(document.createElement('li'), vi.fn())

    expect(observe).not.toHaveBeenCalled()
    expect(() => dispose()).not.toThrow()
  })
})
