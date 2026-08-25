// Reporting a room's unsent text over the room socket ([R32.01]).
//
// Extracted rather than added to `ChatroomView`'s body, and the reason is §9 of
// the dossier: that view already keeps its typing timer as a module-level `let`,
// and a second timer beside the first is how a view body becomes untestable. The
// draft path is the third socket-send responsibility the view owns, so it gets a
// composable and `emitTyping` calls into it.
//
// **This module owns no policy.** It does not know whether any agent may read the
// room, and deliberately: the server drops a frame for a room with no grant
// before touching Redis (AC-1), so a client-side check here would be a second
// place for the same rule to be wrong. What the client owes the participant is
// the disclosure chip, which is driven by the room DTO rather than by this.

import { onBeforeUnmount } from 'vue'

/** Sends one frame on the room channel. Injected so this module never reaches
 *  the socket itself and stays unit-testable without one. */
export type DraftSend = (frame: Record<string, unknown>) => void

/** Matches the composer's existing typing debounce, so a worksheet and a chat
 *  message cost the room the same frame rate. The server applies its own 2s
 *  throttle on top; this is the client half of AC-3. */
export const DRAFT_THROTTLE_MS = 3000

export interface UseDraftReporting {
  /** Report the composer's current text. Call it as often as you like — the
   *  trailing-edge throttle inside decides what actually goes out. Empty text
   *  clears instead of reporting, so select-all-and-delete is a retraction. */
  reportComposer(text: string): void
  /** Retract the composer draft now: on a successful send, and on teardown. */
  clearComposer(): void
  /** Report an activity worksheet's contents, keyed by its activity type. The
   *  host throttles this one already (`ActivityHost` / `ActivityPanel`), so this
   *  passes it straight through rather than throttling twice and doubling the
   *  latency between a keystroke and the entry an agent can read. */
  reportActivity(key: string, payload: unknown): void
  /** Retract one worksheet's draft: on submit, on round change, on unmount. */
  clearActivity(key: string): void
}

export function useDraftReporting(send: DraftSend): UseDraftReporting {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending: string | null = null

  function flush(): void {
    timer = null
    if (pending === null) return
    const text = pending
    pending = null
    send({ type: 'draft.update', surface: 'composer', content: text })
  }

  function reportComposer(text: string): void {
    if (!text.trim()) {
      // An emptied composer is a retraction, not a draft. Reporting "" would be
      // stored as nothing by the server anyway, but sending the clear is what
      // makes the retraction immediate rather than throttled.
      clearComposer()
      return
    }
    pending = text
    // Trailing edge: what matters is the LATEST text. A leading-edge throttle
    // would report the first keystroke of a burst and then nothing until the
    // next burst began, which is the opposite of useful here.
    if (timer === null) timer = setTimeout(flush, DRAFT_THROTTLE_MS)
  }

  function clearComposer(): void {
    // Cancel first. Without this the pending timer fires after the clear and
    // re-reports text the participant has just sent — leaving a "draft" of an
    // already-sent message readable for a full TTL.
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    pending = null
    send({ type: 'draft.clear', surface: 'composer' })
  }

  function reportActivity(key: string, payload: unknown): void {
    send({ type: 'draft.update', surface: 'activity', key, content: serialise(payload) })
  }

  function clearActivity(key: string): void {
    send({ type: 'draft.clear', surface: 'activity', key })
  }

  onBeforeUnmount(() => {
    // The timer must not outlive the view; `clearComposer` cancels it and sends
    // the retraction in one step. The socket is still open at this point — the
    // teardown in `useChatroomSocket` closes it, and this composable is used from
    // the view, whose unmount hook runs first.
    clearComposer()
  })

  return { reportComposer, clearComposer, reportActivity, clearActivity }
}

/** A worksheet payload as text.
 *
 *  JSON rather than a rendered form: the agent-facing description says a
 *  worksheet draft is field values, and a stable machine shape is what a model
 *  can actually read a nine-cell grid out of. Total — a payload carrying
 *  something unserialisable (a cyclic structure from a third-party plugin)
 *  costs that report rather than the socket.
 */
function serialise(payload: unknown): string {
  if (typeof payload === 'string') return payload
  try {
    return JSON.stringify(payload ?? {})
  } catch {
    return ''
  }
}
