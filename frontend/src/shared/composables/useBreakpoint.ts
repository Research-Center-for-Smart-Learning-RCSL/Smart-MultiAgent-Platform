import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const BP = { xs: 0, sm: 480, md: 768, lg: 1024, xl: 1280 } as const
export type Breakpoint = keyof typeof BP

function current(w: number): Breakpoint {
  if (w >= BP.xl) return 'xl'
  if (w >= BP.lg) return 'lg'
  if (w >= BP.md) return 'md'
  if (w >= BP.sm) return 'sm'
  return 'xs'
}

export function useBreakpoint() {
  const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1280)
  const bp = computed(() => current(width.value))

  function onResize() {
    width.value = window.innerWidth
  }

  onMounted(() => window.addEventListener('resize', onResize))
  onBeforeUnmount(() => window.removeEventListener('resize', onResize))

  return {
    width,
    bp,
    isMobile: computed(() => width.value < BP.md),
    isTablet: computed(() => width.value >= BP.md && width.value < BP.lg),
    isDesktop: computed(() => width.value >= BP.lg),
  }
}
