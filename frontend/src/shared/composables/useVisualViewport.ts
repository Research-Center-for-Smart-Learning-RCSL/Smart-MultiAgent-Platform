import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * Tracks how far the on-screen (virtual) keyboard overlaps the layout viewport
 * so a fixed-bottom element (e.g. the chat composer) can be lifted above it.
 *
 * Returns 0 when the visualViewport API is unavailable or no keyboard is shown,
 * which makes any `calc(... - keyboardInset)` consumer a no-op on desktop.
 *
 * Pass an `enabled` getter (e.g. `() => isMobile.value`) to avoid attaching the
 * visualViewport listeners where the inset is never used — the composable
 * attaches only while enabled and detaches (resetting to 0) otherwise.
 */
export function useVisualViewport(enabled?: () => boolean) {
  const keyboardInset = ref(0)
  let rafId = 0
  let attached = false

  function update(): void {
    const vv = window.visualViewport
    if (!vv) {
      keyboardInset.value = 0
      return
    }
    // Space below the visible viewport that the layout viewport still occupies.
    //
    // The reference box is window.innerHeight, i.e. the LAYOUT viewport. That
    // makes this figure correct only for a consumer whose own height resolves
    // against the same box, which is why App.vue's .app-root is `100dvh` and
    // not `100vh`: `vh` denominates the LARGE viewport, so subtracting this
    // inset from a vh-sized element leaves the element too tall by the browser
    // toolbar height and puts the chat composer under the keyboard by exactly
    // that much. Nothing here can detect the mismatch - the arithmetic is the
    // same either way - so the coupling is recorded rather than guarded.
    const overlap = window.innerHeight - vv.height - vv.offsetTop
    keyboardInset.value = overlap > 0 ? Math.round(overlap) : 0
  }

  // visualViewport 'scroll'/'resize' can fire many times per frame during
  // pinch-zoom or keyboard animation; coalesce to one read per frame to avoid
  // forcing a chatroom relayout on every tick.
  function schedule(): void {
    if (rafId) return
    rafId = requestAnimationFrame(() => {
      rafId = 0
      update()
    })
  }

  function attach(): void {
    if (attached || typeof window === 'undefined' || !window.visualViewport) return
    attached = true
    window.visualViewport.addEventListener('resize', schedule)
    window.visualViewport.addEventListener('scroll', schedule)
    update()
  }

  function detach(): void {
    if (rafId) {
      cancelAnimationFrame(rafId)
      rafId = 0
    }
    if (!attached || typeof window === 'undefined' || !window.visualViewport) return
    attached = false
    window.visualViewport.removeEventListener('resize', schedule)
    window.visualViewport.removeEventListener('scroll', schedule)
    keyboardInset.value = 0
  }

  // Setup-scope watcher (auto-disposed on unmount) reacts to enable/disable.
  if (enabled) watch(enabled, (on) => (on ? attach() : detach()))

  onMounted(() => {
    if (!enabled || enabled()) attach()
  })
  onBeforeUnmount(detach)

  return { keyboardInset }
}
