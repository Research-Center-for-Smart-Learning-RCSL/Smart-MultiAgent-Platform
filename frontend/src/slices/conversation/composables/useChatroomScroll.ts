// Composable: message-feed scroll behaviour (07-conversation section 3.11).
//
// Balances live auto-scroll against history review:
//   - pinned to bottom -> new messages scroll the feed down
//   - scrolled up -> new messages bump the "new messages" pill instead
//   - older messages prepended -> viewport position is preserved
//
// The view owns the feed element (passed as listRef) and drives this with the
// ordered feed item ids plus explicit prepend hooks around loadEarlier().
//
// This is the ONLY module in the slice that writes the feed's scroll position.
// It used to share that job with useChatroomMessages, which scrolled the
// element directly on send; with two writers and no owner neither contract was
// complete, which is how the prepend restore and the unseen counter drifted
// apart (F-49). Callers report events (onSent, maybeStick) rather than scroll.

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComputedRef, Ref } from 'vue'

const BOTTOM_THRESHOLD_PX = 80
const FLASH_MS = 1600
// 07-conversation.md:895 specifies the auto-load trigger as "within 100px of
// the top of the feed", which is a geometry question, so it is expressed as the
// observer's own root margin rather than recomputed on every scroll frame.
const TOP_TRIGGER_MARGIN = '100px 0px 0px 0px'

export function useChatroomScroll(
  listRef: Readonly<Ref<HTMLElement | null>>,
  // Ordered ids of everything rendered in the feed, messages and approval cards
  // alike. Ids rather than a count: a count cannot tell an arrival from a
  // prepend, which is the false premise the pill was built on (F-12).
  feedIds: ComputedRef<readonly string[]>,
) {
  const atBottom = ref(true)
  const newCount = ref(0)
  const showPill = computed(() => !atBottom.value && newCount.value > 0)

  // Search "jump to message": id of the message to flash-highlight, cleared
  // after the flash. The view binds this to each bubble's flash prop.
  const highlightId = ref<string | null>(null)
  let flashTimer: ReturnType<typeof setTimeout> | null = null

  function isAtBottom(): boolean {
    const el = listRef.value
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD_PX
  }

  // ---- unseen counter -------------------------------------------------------
  // Derived from the identity of the newest item the reader has acknowledged,
  // not from how much the list grew. A prepend cannot move this by
  // construction, because a prepend does not change the tail, which is what
  // makes the rule immune to a future second prepend path.
  const lastSeenId = ref<string | null>(null)

  function markAllSeen(): void {
    const ids = feedIds.value
    lastSeenId.value = ids.length > 0 ? ids[ids.length - 1]! : null
    newCount.value = 0
  }

  function unseenCount(): number {
    const ids = feedIds.value
    if (lastSeenId.value === null) return 0
    const at = ids.lastIndexOf(lastSeenId.value)
    if (at === -1) {
      // The acknowledged item is gone. Treating that as "everything is unseen"
      // reads well for a wholesale cache replacement and badly for the case
      // that actually happens: an optimistic send acknowledges the key
      // `pending-<uuid>`, the reader scrolls up, and the persisted id replaces
      // it -- which would announce the whole room as new. A deleted message and
      // a resolved approval do the same. Re-anchor to the tail and leave the
      // count alone: no invented arrivals, and the next real one still counts.
      lastSeenId.value = ids.length > 0 ? ids[ids.length - 1]! : null
      return newCount.value
    }
    return ids.length - 1 - at
  }

  function scrollToBottom(smooth = false): void {
    const el = listRef.value
    if (!el) return
    // jsdom (tests) has no Element.scrollTo, so fall back to scrollTop.
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    } else {
      el.scrollTop = el.scrollHeight
    }
    atBottom.value = true
    markAllSeen()
  }

  function onScroll(): void {
    atBottom.value = isAtBottom()
    if (atBottom.value) markAllSeen()
  }

  /** Re-pin to the bottom if the user was already there (streaming growth). */
  function maybeStick(): void {
    if (atBottom.value) scrollToBottom(false)
  }

  // ---- prepend restoration --------------------------------------------------
  // Both quantities are needed. The height delta says how much content was
  // added above; the saved scrollTop says where the reader was inside it.
  // Restoring from the delta alone puts every reader at the same place
  // regardless of where they were, which is the jump F-11 describes.
  let savedHeight = 0
  let savedScrollTop = 0
  // True from the capture until the restore's tick has run. Two things defer to
  // it: the arrival watch (the restore owns the position during this window)
  // and the top trigger (between the DOM insert and the restore the sentinel
  // sits momentarily at the top of a taller list, which would otherwise read as
  // a second reach and load another page).
  let prepending = false

  function captureBeforePrepend(): void {
    const el = listRef.value
    savedHeight = el?.scrollHeight ?? 0
    savedScrollTop = el?.scrollTop ?? 0
    prepending = true
  }

  function restoreAfterPrepend(): void {
    void nextTick(() => {
      const el = listRef.value
      if (el) el.scrollTop = Math.max(0, savedScrollTop + (el.scrollHeight - savedHeight))
      prepending = false
    })
  }

  // Feed changed: auto-scroll when pinned, otherwise raise the pill. Skipped
  // mid-prepend, where the restore owns the scroll position and the tail, and
  // therefore the unseen count, has not moved.
  //
  // Watched by identity rather than by length, because the two differ where it
  // matters: an optimistic send's `pending-<uuid>` key is replaced by its
  // persisted id at the same position, which changes the list without changing
  // its length.
  watch(
    feedIds,
    () => {
      if (prepending) return
      void nextTick(() => {
        if (atBottom.value) scrollToBottom(false)
        else newCount.value = unseenCount()
      })
    },
  )

  /** Scroll a loaded message into view and flash it. Returns false when the
   *  message is not in the currently-loaded page (older history not paginated
   *  in yet), so the caller can surface that to the user. */
  function scrollToMessage(id: string): boolean {
    const el = listRef.value
    if (!el) return false
    const selector =
      typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
        ? `#msg-${CSS.escape(id)}`
        : `[id="msg-${id}"]`
    const target = el.querySelector<HTMLElement>(selector)
    if (!target) return false
    // jsdom (tests) has no Element.scrollIntoView.
    if (typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    highlightId.value = id
    if (flashTimer !== null) clearTimeout(flashTimer)
    flashTimer = setTimeout(() => {
      highlightId.value = null
      flashTimer = null
    }, FLASH_MS)
    return true
  }

  // ---- growth after render --------------------------------------------------
  // The feed grows after it has been scrolled: the KaTeX/Mermaid/highlight pass
  // is asynchronous and mutates the DOM directly (so it triggers no update
  // hook), and an image has no intrinsic dimensions to reserve space with.
  // Observing the content is the only mechanism that covers both, plus web-font
  // swap and anything added later. maybeStick gates on atBottom, so a reader
  // who scrolled up is never yanked (F-13).
  let resizeObserver: ResizeObserver | null = null
  let topObserver: IntersectionObserver | null = null

  /** (Re-)observe the feed: its items AND its own box.
   *
   *  The items cover content that grows after render. The `<ol>` itself covers
   *  the scrollport SHRINKING, which is a different way for the newest message
   *  to leave the viewport: the composer auto-grows up to 192px in the grid row
   *  below, taking that height off the feed. No item resizes, and the browser
   *  fires no scroll event because scrollTop is not clamped, so without this a
   *  reader pinned to the bottom silently loses the last bubbles while typing.
   *
   *  Neither observation can loop: re-sticking assigns scrollTop, and scrolling
   *  changes no element's box.
   *
   *  The set is refreshed whenever the feed changes, because an item that
   *  arrives later is not covered by a mount-time sweep. */
  function observeFeedContent(): void {
    const el = listRef.value
    if (!el || typeof ResizeObserver === 'undefined') return
    resizeObserver ??= new ResizeObserver(() => maybeStick())
    resizeObserver.disconnect()
    resizeObserver.observe(el)
    for (const child of Array.from(el.children)) resizeObserver.observe(child)
  }

  // Identity, not length: a key swap replaces the element without changing the
  // count, and the old element would keep the observation while the new one
  // rendered unwatched.
  watch(feedIds, observeFeedContent, { flush: 'post' })

  /** Watch the load-earlier sentinel and call `onReach` when the reader gets
   *  within the spec's 100px of the top. Returns a disposer. */
  function observeTop(el: HTMLElement, onReach: () => void): () => void {
    // jsdom and SSR have no IntersectionObserver.
    if (typeof IntersectionObserver === 'undefined') return () => {}
    topObserver?.disconnect()
    const observer = new IntersectionObserver(
      (entries) => {
        if (prepending) return
        if (entries.some((e) => e.isIntersecting)) onReach()
      },
      { root: listRef.value, rootMargin: TOP_TRIGGER_MARGIN },
    )
    observer.observe(el)
    topObserver = observer
    return () => {
      observer.disconnect()
      if (topObserver === observer) topObserver = null
    }
  }

  onMounted(() => {
    listRef.value?.addEventListener('scroll', onScroll, { passive: true })
    observeFeedContent()
    void nextTick(() => scrollToBottom(false))
  })
  onBeforeUnmount(() => {
    listRef.value?.removeEventListener('scroll', onScroll)
    resizeObserver?.disconnect()
    resizeObserver = null
    topObserver?.disconnect()
    topObserver = null
    if (flashTimer !== null) clearTimeout(flashTimer)
  })

  return {
    atBottom,
    newCount,
    showPill,
    highlightId,
    scrollToBottom,
    scrollToMessage,
    maybeStick,
    captureBeforePrepend,
    restoreAfterPrepend,
    observeTop,
  }
}
