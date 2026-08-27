import { nextTick, onBeforeUnmount, ref, watch, type CSSProperties, type Ref } from 'vue'

/**
 * Shared engine for body-teleported, fixed-position overlays anchored to an
 * in-flow trigger (SDropdown, STooltip, OrgProjectSwitcher). One home for the
 * listener lifecycle, the viewport clamp, and the "anchor scrolled away"
 * check, so a positioning fix cannot land in one overlay and silently miss
 * the others. Placement math stays with each component: a menu that caps and
 * flips, a tooltip with an arrow, and a panel with a width floor genuinely
 * differ there.
 */

/** Clearance kept between an overlay and the viewport edges. */
export const VIEWPORT_MARGIN = 8

/**
 * Clamp a fixed-position coordinate so a box of `size` stays inside the
 * viewport axis of `viewportSize`, honouring the margin. When the box is
 * larger than the viewport, the leading edge wins (top/left stays reachable).
 */
export function clampToViewport(
  coord: number,
  size: number,
  viewportSize: number,
  margin: number = VIEWPORT_MARGIN,
): number {
  return Math.min(Math.max(coord, margin), Math.max(margin, viewportSize - margin - size))
}

/**
 * True when the anchor has been scrolled fully out of the viewport or out of
 * any overflow-clipping ancestor. A fixed overlay keeps painting when its
 * anchor is clipped away (nothing clips a body child), so the overlay must
 * check this on every reposition and dismiss itself — otherwise it floats
 * over unrelated chrome, pointing at nothing.
 *
 * A zero-size rect means layout has not run (jsdom, display:none) and proves
 * nothing, so it reports "not clipped".
 */
export function isAnchorClippedOut(anchor: HTMLElement): boolean {
  const rect = anchor.getBoundingClientRect()
  if (rect.width === 0 && rect.height === 0) return false
  if (
    rect.bottom <= 0 ||
    rect.top >= window.innerHeight ||
    rect.right <= 0 ||
    rect.left >= window.innerWidth
  ) {
    return true
  }
  for (let el = anchor.parentElement; el && el !== document.body; el = el.parentElement) {
    const style = getComputedStyle(el)
    // Match explicit clipping values only: jsdom reports '' for the default,
    // and treating unknown as clipping would dismiss overlays under test.
    const clips = (v: string) => v === 'auto' || v === 'scroll' || v === 'hidden' || v === 'clip'
    const clipsX = clips(style.overflowX)
    const clipsY = clips(style.overflowY)
    if (!clipsX && !clipsY) continue
    const box = el.getBoundingClientRect()
    if (clipsY && (rect.bottom <= box.top || rect.top >= box.bottom)) return true
    if (clipsX && (rect.right <= box.left || rect.left >= box.right)) return true
  }
  return false
}

export interface AnchoredPositionContext {
  /** The anchor's current viewport rect. */
  rect: DOMRect
  /** The overlay element, for measuring its natural size. */
  panel: HTMLElement
  viewportWidth: number
  viewportHeight: number
}

export interface AnchoredPositionOptions {
  anchor: Ref<HTMLElement | null>
  panel: Ref<HTMLElement | null>
  /** Watched: listeners attach while true and detach while false. */
  open: Ref<boolean>
  /** Pure placement math; runs after the panel exists and on every scroll/resize. */
  compute: (ctx: AnchoredPositionContext) => CSSProperties
  /**
   * Called instead of compute when the anchor is scrolled out of view
   * (see isAnchorClippedOut). Overlays should dismiss themselves here.
   */
  onAnchorClipped: () => void
}

export function useAnchoredPosition(options: AnchoredPositionOptions): {
  style: Ref<CSSProperties>
  update: () => void
} {
  // Off-screen until the first measurement so the pre-position frame never
  // paints anywhere visible.
  const OFFSCREEN: CSSProperties = { top: '-9999px', left: '0px' }
  const style = ref<CSSProperties>(OFFSCREEN)

  function update() {
    const anchor = options.anchor.value
    const panel = options.panel.value
    if (!anchor || !panel) return
    if (isAnchorClippedOut(anchor)) {
      options.onAnchorClipped()
      return
    }
    style.value = options.compute({
      rect: anchor.getBoundingClientRect(),
      panel,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    })
  }

  function onViewportChange() {
    if (options.open.value) update()
  }

  function detach() {
    window.removeEventListener('scroll', onViewportChange, { capture: true })
    window.removeEventListener('resize', onViewportChange)
  }

  watch(options.open, async (open) => {
    if (open) {
      window.addEventListener('scroll', onViewportChange, { capture: true, passive: true })
      window.addEventListener('resize', onViewportChange, { passive: true })
      await nextTick()
      // After the overlay exists: placement math needs its measured size.
      update()
    } else {
      detach()
      style.value = OFFSCREEN
    }
  })

  onBeforeUnmount(detach)

  return { style, update }
}
