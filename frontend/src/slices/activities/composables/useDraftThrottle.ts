// The trailing-edge throttle both worksheet surfaces report on ([R32.01]).
//
// Two surfaces need it and they are not the same component: `ActivityHost` owns the
// individual worksheet (schema form and plugin alike), and `ActivityPanel` owns the
// group-proposal form, which renders `SchemaForm` directly and never passes through
// the host. Written twice they drifted immediately — same constant, same three
// pieces of state, two names for each.
//
// It lives in `slices/activities` rather than beside `useDraftReporting`: that one
// is in `slices/conversation` and owns the socket, and gate #1's SLICE_DEPS forbids
// activities importing conversation back. What is shared here is the *timing*, not
// the transport.

import { onBeforeUnmount } from 'vue'

/** Matches the composer's typing debounce, so a worksheet and a chat message cost
 *  the room the same frame rate. The server applies its own 2s throttle on top. */
export const DRAFT_THROTTLE_MS = 3000

export interface UseDraftThrottle<T> {
  /** Record the latest value. Call it on every change; one report goes out per
   *  window, carrying whatever the value was when the window closed. */
  report(value: T): void
  /** Drop anything pending without emitting.
   *
   *  Always called before a clear, and that ordering is the point: without it the
   *  pending timer fires *after* the retraction and re-reports text that has just
   *  been submitted, leaving a draft of an already-sent answer readable for a full
   *  TTL. */
  cancel(): void
}

/**
 * `emit` runs at most once per window, with the most recent value.
 *
 * Trailing edge, not leading: what an agent needs is the current state of the
 * worksheet. A leading edge would report the first keystroke of a burst and then
 * nothing until the next burst began, which is the opposite of useful.
 *
 * Cancels itself on unmount — a timer that outlives its component emits into a
 * panel that is gone, and with fake timers in a test that failure is silent.
 */
export function useDraftThrottle<T>(emit: (value: T) => void): UseDraftThrottle<T> {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending: T | null = null

  function report(value: T): void {
    pending = value
    if (timer !== null) return
    timer = setTimeout(() => {
      timer = null
      if (pending !== null) emit(pending)
    }, DRAFT_THROTTLE_MS)
  }

  function cancel(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    pending = null
  }

  onBeforeUnmount(cancel)

  return { report, cancel }
}
