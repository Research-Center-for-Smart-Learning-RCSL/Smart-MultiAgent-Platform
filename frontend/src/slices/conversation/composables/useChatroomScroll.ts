// Composable: message-feed scroll behaviour (07-conversation §3.11).
//
// Balances live auto-scroll against history review:
//   - pinned to bottom  → new messages scroll the feed down
//   - scrolled up        → new messages bump the "new messages" pill instead
//   - older messages prepended → viewport position is preserved
//
// The view owns the feed element (passed as listRef) and drives this with
// the reactive message count plus explicit prepend hooks around loadEarlier().

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComputedRef, Ref } from 'vue'

const BOTTOM_THRESHOLD_PX = 80

export function useChatroomScroll(
  listRef: Readonly<Ref<HTMLElement | null>>,
  messageCount: ComputedRef<number>,
) {
  const atBottom = ref(true)
  const newCount = ref(0)
  const showPill = computed(() => !atBottom.value && newCount.value > 0)

  function isAtBottom(): boolean {
    const el = listRef.value
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD_PX
  }

  function scrollToBottom(smooth = false): void {
    const el = listRef.value
    if (!el) return
    // jsdom (tests) has no Element.scrollTo — fall back to scrollTop.
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    } else {
      el.scrollTop = el.scrollHeight
    }
    newCount.value = 0
    atBottom.value = true
  }

  function onScroll(): void {
    atBottom.value = isAtBottom()
    if (atBottom.value) newCount.value = 0
  }

  /** Re-pin to the bottom if the user was already there (streaming growth). */
  function maybeStick(): void {
    if (atBottom.value) scrollToBottom(false)
  }

  // New messages: auto-scroll when pinned, otherwise grow the pill counter.
  watch(messageCount, (count, prev) => {
    const delta = count - (prev ?? 0)
    if (delta <= 0) return
    void nextTick(() => {
      if (atBottom.value) scrollToBottom(false)
      else newCount.value += delta
    })
  })

  // Preserve the topmost visible message when older history is prepended.
  let savedHeight = 0
  function captureBeforePrepend(): void {
    savedHeight = listRef.value?.scrollHeight ?? 0
  }
  function restoreAfterPrepend(): void {
    void nextTick(() => {
      const el = listRef.value
      if (el) el.scrollTop = el.scrollHeight - savedHeight
    })
  }

  onMounted(() => {
    listRef.value?.addEventListener('scroll', onScroll, { passive: true })
    void nextTick(() => scrollToBottom(false))
  })
  onBeforeUnmount(() => {
    listRef.value?.removeEventListener('scroll', onScroll)
  })

  return {
    atBottom,
    newCount,
    showPill,
    scrollToBottom,
    maybeStick,
    captureBeforePrepend,
    restoreAfterPrepend,
  }
}
