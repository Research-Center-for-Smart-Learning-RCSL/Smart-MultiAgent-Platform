// scrollToMessage: the search "jump to message" path. Verifies the loaded /
// not-loaded branch and the transient highlight lifecycle.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, ref, h } from 'vue'

import { useChatroomScroll } from '../composables/useChatroomScroll'

type Scroll = ReturnType<typeof useChatroomScroll>

function mountScroll(messageIds: string[]): {
  wrapper: VueWrapper
  scroll: Scroll
} {
  let scroll!: Scroll
  const Host = defineComponent({
    setup() {
      const listRef = ref<HTMLElement | null>(null)
      scroll = useChatroomScroll(listRef, computed(() => messageIds.length))
      return () =>
        h(
          'ol',
          { ref: listRef },
          messageIds.map((id) => h('li', { id: `msg-${id}` }, id)),
        )
    },
  })
  const wrapper = mount(Host, { attachTo: document.body })
  return { wrapper, scroll }
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
