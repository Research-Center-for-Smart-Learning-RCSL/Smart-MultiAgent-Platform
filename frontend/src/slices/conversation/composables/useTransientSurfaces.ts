import { computed, ref, type ComputedRef, type Ref } from 'vue'

/** The three chatroom surfaces that can cover, or sit beside, the feed. */
export type ChatroomSurface = 'search' | 'agents' | 'people'

export interface TransientSurfaces {
  active: ComputedRef<ChatroomSurface | null>
  isOpen: (surface: ChatroomSurface) => boolean
  open: (surface: ChatroomSurface) => void
  close: (surface?: ChatroomSurface) => void
  toggle: (surface: ChatroomSurface) => void
  reset: () => void
}

/**
 * Single-owner coordinator for the chatroom's transient surfaces.
 *
 * Search, the agent panel and the people/observer panel are three different
 * shapes in three different bands — a drawer below 1024, an in-chat overlay at
 * 1024-1279, a persistent rail at 1280 and up — but wherever more than one of
 * them is transient they compete for the same feed area, the same backdrop and
 * the same focus trap. Holding one `active` value rather than three booleans is
 * what makes "at most one" true by construction instead of by three watchers
 * agreeing with each other. The bands need no special-casing here: a surface
 * that is persistent in a band has no control that can open it, so it simply
 * never becomes active there.
 *
 * The coordinator also owns focus restoration, because only it can tell a
 * normal close (put focus back on the control the user pressed) from a hand-off
 * to another surface (do not — the user is going somewhere, not coming back)
 * and from a layout-band change (the recorded control may no longer exist).
 */
export function useTransientSurfaces(
  /**
   * Where focus goes when the recorded opener will not take it. Give this a
   * `tabindex="-1"` container that survives every surface: the point is that
   * the keyboard user resumes inside the chatroom rather than at the top of the
   * document.
   */
  fallbackFocus?: Readonly<Ref<HTMLElement | null>>,
): TransientSurfaces {
  const active = ref<ChatroomSurface | null>(null)
  // The control that opened `active.value`. Not a ref: nothing renders from it,
  // and keeping a DOM node out of the reactive graph avoids Vue deep-tracking
  // an element subtree.
  let opener: HTMLElement | null = null

  function captureOpener(): void {
    const el = document.activeElement
    // `document.body` is what an unfocused page reports; focusing it back is a
    // no-op that reads as a focus dead-end to a keyboard user.
    opener = el instanceof HTMLElement && el !== document.body ? el : null
  }

  function open(surface: ChatroomSurface): void {
    if (active.value === surface) return
    // A hand-off records the NEW opener and drops the old one. Restoring the
    // old one here would move focus out of the surface being opened, and
    // remembering it for later would eventually restore focus to a control the
    // user pressed two surfaces ago.
    captureOpener()
    active.value = surface
  }

  function close(surface?: ChatroomSurface): void {
    // Guarded so a stale close (an SDrawer emitting `close` as it unmounts, a
    // search result selected after the panel already handed off) cannot shut a
    // different surface that has since become active.
    if (surface !== undefined && active.value !== surface) return
    active.value = null
    const target = opener
    opener = null
    target?.focus()
    // Restoration can fail silently, and the failure looks exactly like the
    // dead end this composable exists to prevent. `captureOpener` records
    // whatever held focus, which after a keyboard hand-off (Ctrl+K out of an
    // open rail overlay) is a control INSIDE the surface being handed off from
    // — and that surface is `visibility: hidden` by the time this runs.
    // `focus()` on a hidden or detached node is a no-op, so focus stays on a
    // node that is about to disappear and lands on <body>. Ask whether the move
    // actually happened rather than testing for the conditions that prevent it:
    // one check covers hidden, detached, disabled and inert alike.
    if (target && document.activeElement !== target) fallbackFocus?.value?.focus()
  }

  function toggle(surface: ChatroomSurface): void {
    if (active.value === surface) close(surface)
    else open(surface)
  }

  /**
   * Drop all surface state without restoring focus. For a layout-band change,
   * where the recorded control may have been unmounted by the same resize —
   * focusing a detached node silently moves focus to `<body>`, which is the
   * dead-end this composable otherwise exists to prevent.
   */
  function reset(): void {
    active.value = null
    opener = null
  }

  return {
    active: computed(() => active.value),
    isOpen: (surface) => active.value === surface,
    open,
    close,
    toggle,
    reset,
  }
}
