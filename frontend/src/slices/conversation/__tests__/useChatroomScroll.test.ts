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
