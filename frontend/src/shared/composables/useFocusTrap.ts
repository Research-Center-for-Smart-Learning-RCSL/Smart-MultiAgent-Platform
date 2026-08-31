import { nextTick, onBeforeUnmount, watch, type Ref } from 'vue'

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Ref-counted body scroll lock shared across all dialog instances, so a stacked
// dialog (e.g. an SModal over an SDrawer) only releases the lock when the LAST
// open dialog closes — not when whichever happens to close first does.
let scrollLockCount = 0

function acquireScrollLock(): void {
  if (scrollLockCount === 0) document.body.style.overflow = 'hidden'
  scrollLockCount++
}

function releaseScrollLock(): void {
  scrollLockCount = Math.max(0, scrollLockCount - 1)
  if (scrollLockCount === 0) document.body.style.overflow = ''
}

export interface FocusTrapOptions {
  /**
   * Set false for a panel that shares the page rather than covering it. A
   * document-modal dialog must not leave the page behind it scrollable; an
   * in-page overlay sits inside its own scroll container and freezing the
   * document would strand the content it does not cover.
   */
  lockScroll?: boolean
  /**
   * Set false when a coordinator above this panel owns restoration — one that
   * can tell a normal close from a hand-off to a sibling surface, which this
   * composable cannot see.
   */
  restoreFocus?: boolean
}

/**
 * Dialog focus management shared by SModal and SDrawer.
 *
 * While `isOpen()` is true it locks body scroll, moves focus into the panel,
 * and (via `trapTab`) keeps Tab/Shift+Tab cycling within it. On close it
 * restores focus to whatever was focused before opening. Escape handling is
 * left to the caller, since modals and drawers differ (persistent vs. always
 * closable).
 *
 * The defaults are the modal contract; both may be waived individually for a
 * non-modal in-page panel (see `FocusTrapOptions`).
 */
export function useFocusTrap(
  panelRef: Readonly<Ref<HTMLElement | null>>,
  isOpen: () => boolean,
  options: FocusTrapOptions = {},
) {
  const { lockScroll = true, restoreFocus = true } = options
  let previouslyFocused: HTMLElement | null = null
  // Whether THIS instance currently holds the shared lock, so close/unmount
  // never double-releases.
  let holdsLock = false

  function focusable(): HTMLElement[] {
    if (!panelRef.value) return []
    return Array.from(panelRef.value.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
  }

  function trapTab(e: KeyboardEvent): void {
    if (e.key !== 'Tab') return
    const els = focusable()
    if (els.length === 0) {
      e.preventDefault()
      return
    }
    const first = els[0]
    const last = els[els.length - 1]
    if (!first || !last) return
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault()
        last.focus()
      }
    } else if (document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  // `immediate` because a dialog can be mounted already open — the common shape
  // is `v-if="result"` on the wrapper with a constant `:open="true"` inside, and
  // for that source the watcher would otherwise never fire at all: focus would
  // stay outside the panel (so Tab walks the page behind it), body scroll would
  // stay unlocked, and nothing would be recorded to restore focus to on close.
  // Harmless for the usual `open=false` mount: the else branch is a no-op until
  // something has actually been opened.
  watch(isOpen, async (open) => {
    if (open) {
      previouslyFocused = document.activeElement as HTMLElement | null
      if (lockScroll) {
        acquireScrollLock()
        holdsLock = true
      }
      await nextTick()
      const els = focusable()
      if (els[0]) els[0].focus()
      else panelRef.value?.focus()
    } else {
      if (holdsLock) {
        releaseScrollLock()
        holdsLock = false
      }
      // Released either way: holding the node when restoration is delegated
      // would keep a detached element alive for the life of the component.
      if (restoreFocus) previouslyFocused?.focus()
      previouslyFocused = null
    }
  }, { immediate: true })

  onBeforeUnmount(() => {
    if (holdsLock) {
      releaseScrollLock()
      holdsLock = false
    }
  })

  return { trapTab }
}
