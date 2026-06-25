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

/**
 * Dialog focus management shared by SModal and SDrawer.
 *
 * While `isOpen()` is true it locks body scroll, moves focus into the panel,
 * and (via `trapTab`) keeps Tab/Shift+Tab cycling within it. On close it
 * restores focus to whatever was focused before opening. Escape handling is
 * left to the caller, since modals and drawers differ (persistent vs. always
 * closable).
 */
export function useFocusTrap(
  panelRef: Ref<HTMLElement | null>,
  isOpen: () => boolean,
) {
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

  watch(isOpen, async (open) => {
    if (open) {
      previouslyFocused = document.activeElement as HTMLElement | null
      acquireScrollLock()
      holdsLock = true
      await nextTick()
      const els = focusable()
      if (els.length > 0) els[0].focus()
      else panelRef.value?.focus()
    } else {
      if (holdsLock) {
        releaseScrollLock()
        holdsLock = false
      }
      if (previouslyFocused) {
        previouslyFocused.focus()
        previouslyFocused = null
      }
    }
  })

  onBeforeUnmount(() => {
    if (holdsLock) {
      releaseScrollLock()
      holdsLock = false
    }
  })

  return { trapTab }
}
