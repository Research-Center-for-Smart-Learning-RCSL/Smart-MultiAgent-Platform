import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

// Single source of truth for breakpoint pixel thresholds (px). Exported so
// consumers like STable's hideBelow logic don't re-hardcode the same numbers.
export const BP = { xs: 0, sm: 480, md: 768, lg: 1024, xl: 1280 } as const
export type Breakpoint = keyof typeof BP

function current(w: number): Breakpoint {
  if (w >= BP.xl) return 'xl'
  if (w >= BP.lg) return 'lg'
  if (w >= BP.md) return 'md'
  if (w >= BP.sm) return 'sm'
  return 'xs'
}

// Module-level shared state: a single `resize` listener serves every caller,
// ref-counted so it is removed when the last consumer unmounts. This avoids one
// listener per component instance (notably when many STables render at once).
const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1280)
let consumers = 0

function onResize(): void {
  width.value = window.innerWidth
}

export function useBreakpoint() {
  onMounted(() => {
    if (typeof window !== 'undefined') {
      if (consumers === 0) window.addEventListener('resize', onResize)
      // Re-sync on every mount: a consumer mounting after the viewport changed
      // (no resize event fired, e.g. between tests) must not read a stale width.
      width.value = window.innerWidth
    }
    consumers++
  })

  onBeforeUnmount(() => {
    consumers--
    if (consumers === 0 && typeof window !== 'undefined') {
      window.removeEventListener('resize', onResize)
    }
  })

  const bp = computed(() => current(width.value))

  return {
    width,
    bp,
    // Spec-named alias (11-responsive-a11y.md section 1); `bp` retained for callers.
    breakpoint: bp,
    isMobile: computed(() => width.value < BP.md),
    isTablet: computed(() => width.value >= BP.md && width.value < BP.lg),
    isDesktop: computed(() => width.value >= BP.lg),
  }
}
