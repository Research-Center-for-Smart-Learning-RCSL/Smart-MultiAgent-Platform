import { computed, onScopeDispose, ref, toValue, watch, type ComputedRef, type MaybeRefOrGetter } from 'vue'

export interface ResizablePanelOptions {
  /** localStorage key. Namespaced by the caller, e.g. `smap-chatroom-rail-w`. */
  storageKey: string
  defaultWidth: number
  min: number
  /** Hard ceiling in px, whatever the available space. */
  max: number
  /** Element whose inline size bounds the panel — normally the layout the panel
   *  is a track of. Measured with a ResizeObserver, so a change that fires no
   *  window `resize` (a collapsing app sidebar, a devtools split) still
   *  re-clamps. Falls back to the viewport when absent or unsupported, which
   *  over-estimates the room by whatever else shares the viewport. */
  container?: MaybeRefOrGetter<HTMLElement | null | undefined>
  /** Width inside `container` that the other tracks and the neighbouring
   *  column's floor need. The panel may never grow into it. */
  reserve?: number
  /** Delay before a change reaches localStorage. A drag emits a value per
   *  pointermove, so persisting each one would put 60+ synchronous storage
   *  writes per second on the main thread while the layout reflows. */
  persistDelayMs?: number
}

export interface ResizablePanel {
  /** The width the layout should apply: the user's choice, clamped to what the
   *  container currently allows. */
  width: ComputedRef<number>
  /** The effective upper bound right now. Exposed for `aria-valuemax`. */
  maxWidth: ComputedRef<number>
  min: number
  setWidth: (px: number) => void
  nudge: (deltaPx: number) => void
  reset: () => void
}

function hasWindow(): boolean {
  return typeof window !== 'undefined'
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), Math.max(low, high))
}

function readStored(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return fallback
    const parsed = Number.parseInt(raw, 10)
    // A hand-edited or corrupt value must not be able to strand the layout.
    // Range is not checked here: the clamp against the live container in
    // `width` is the single place bounds are enforced (see the note on `stored`).
    if (Number.isFinite(parsed) && parsed > 0) return parsed
  } catch { /* SSR / restricted storage */ }
  return fallback
}

function persist(key: string, px: number): void {
  try { localStorage.setItem(key, String(px)) } catch { /* quota / restricted */ }
}

/** A pointer- and keyboard-resizable panel width, persisted per browser.
 *
 *  The stored value is the user's *chosen* width and is rewritten only on an
 *  explicit user action; the width the layout consumes is that choice clamped to
 *  the room the container currently has. Keeping the two separate is what lets a
 *  shrinking container narrow the panel and a growing one restore the original
 *  choice, rather than the first shrink permanently overwriting it. */
export function useResizablePanel(opts: ResizablePanelOptions): ResizablePanel {
  const reserve = opts.reserve ?? 0
  const persistDelayMs = opts.persistDelayMs ?? 150

  const availableWidth = ref(hasWindow() ? window.innerWidth : opts.max + reserve)
  const stored = ref(readStored(opts.storageKey, opts.defaultWidth))

  const maxWidth = computed(() =>
    Math.max(opts.min, Math.min(opts.max, availableWidth.value - reserve)),
  )
  const width = computed(() => clamp(stored.value, opts.min, maxWidth.value))

  // ---- persistence, debounced -------------------------------------------------

  let pendingTimer: ReturnType<typeof setTimeout> | null = null
  let pendingValue: number | null = null

  function flushPersist(): void {
    if (pendingTimer !== null) {
      clearTimeout(pendingTimer)
      pendingTimer = null
    }
    if (pendingValue === null) return
    persist(opts.storageKey, pendingValue)
    pendingValue = null
  }

  function schedulePersist(px: number): void {
    pendingValue = px
    if (pendingTimer !== null) clearTimeout(pendingTimer)
    pendingTimer = setTimeout(flushPersist, persistDelayMs)
  }

  // ---- public API -------------------------------------------------------------

  function setWidth(px: number): void {
    if (!Number.isFinite(px)) return
    const next = clamp(Math.round(px), opts.min, maxWidth.value)
    stored.value = next
    schedulePersist(next)
  }

  // Nudges start from the effective width, not the stored one, so a keyboard
  // adjustment made while the container has the panel clamped moves from what
  // the user can actually see.
  function nudge(deltaPx: number): void {
    setWidth(width.value + deltaPx)
  }

  function reset(): void {
    setWidth(opts.defaultWidth)
  }

  // ---- measurement ------------------------------------------------------------

  function measureViewport(): void {
    if (hasWindow()) availableWidth.value = window.innerWidth
  }

  if (hasWindow()) {
    const supportsObserver = typeof ResizeObserver !== 'undefined'
    let observer: ResizeObserver | null = null

    if (opts.container && supportsObserver) {
      observer = new ResizeObserver((entries) => {
        const entry = entries[0]
        if (entry) availableWidth.value = entry.contentRect.width
      })
      watch(
        () => toValue(opts.container),
        (el, _prev, onCleanup) => {
          observer?.disconnect()
          if (!el) {
            // No element yet (or it went away): the viewport is the only
            // measurement left, and it over-estimates rather than under-, so the
            // panel stays usable until the element appears.
            measureViewport()
            return
          }
          availableWidth.value = el.getBoundingClientRect().width
          observer?.observe(el)
          onCleanup(() => observer?.disconnect())
        },
        { immediate: true },
      )
    } else {
      window.addEventListener('resize', measureViewport)
      onScopeDispose(() => window.removeEventListener('resize', measureViewport))
    }

    onScopeDispose(() => {
      observer?.disconnect()
      // A drag followed immediately by navigating away must still save.
      flushPersist()
    })
  }

  return { width, maxWidth, min: opts.min, setWidth, nudge, reset }
}
