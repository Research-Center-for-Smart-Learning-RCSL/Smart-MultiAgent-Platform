import { computed, onScopeDispose, ref, type ComputedRef, type Ref } from 'vue'

export interface ResizablePanelOptions {
  /** localStorage key. Namespaced by the caller, e.g. `smap-chatroom-rail-w`. */
  storageKey: string
  defaultWidth: number
  min: number
  /** Hard ceiling in px. The effective ceiling is additionally capped at
   *  `maxViewportFraction` of the viewport so the neighbouring column always
   *  keeps a usable share on a narrow screen. */
  max: number
  maxViewportFraction: number
}

export interface ResizablePanel {
  /** The width the layout should apply: the user's choice, clamped to what the
   *  current viewport allows. */
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
    // Range is not checked here: the clamp against the live viewport in `width`
    // is the single place bounds are enforced (see the note on `stored`).
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
 *  the current viewport. Keeping the two separate is what lets a window shrink
 *  narrow the panel and a window widen restore the original choice, rather than
 *  the first shrink permanently overwriting it. */
export function useResizablePanel(opts: ResizablePanelOptions): ResizablePanel {
  const viewportWidth = ref(hasWindow() ? window.innerWidth : opts.max)
  const stored: Ref<number> = ref(readStored(opts.storageKey, opts.defaultWidth))

  const maxWidth = computed(() =>
    Math.max(opts.min, Math.min(opts.max, Math.floor(viewportWidth.value * opts.maxViewportFraction))),
  )
  const width = computed(() => clamp(stored.value, opts.min, maxWidth.value))

  function setWidth(px: number): void {
    if (!Number.isFinite(px)) return
    const next = clamp(Math.round(px), opts.min, maxWidth.value)
    stored.value = next
    persist(opts.storageKey, next)
  }

  // Nudges start from the effective width, not the stored one, so a keyboard
  // adjustment made while the viewport has the panel clamped moves from what the
  // user can actually see.
  function nudge(deltaPx: number): void {
    setWidth(width.value + deltaPx)
  }

  function reset(): void {
    setWidth(opts.defaultWidth)
  }

  if (hasWindow()) {
    const onResize = (): void => { viewportWidth.value = window.innerWidth }
    window.addEventListener('resize', onResize)
    onScopeDispose(() => window.removeEventListener('resize', onResize))
  }

  return { width, maxWidth, min: opts.min, setWidth, nudge, reset }
}
