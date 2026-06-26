import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/**
 * Reactive `prefers-reduced-motion` flag. Unlike a one-shot
 * `matchMedia().matches` read, this updates live when the OS setting is toggled,
 * so JS-gated motion (parallax, scroll reveals) tracks the preference without a
 * reload. Resolves to `false` under SSR or where matchMedia is unavailable.
 */
export function usePrefersReducedMotion(): Ref<boolean> {
  const reduced = ref(false)
  let mql: MediaQueryList | null = null

  function update(): void {
    if (mql) reduced.value = mql.matches
  }

  onMounted(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    reduced.value = mql.matches
    mql.addEventListener('change', update)
  })

  onBeforeUnmount(() => mql?.removeEventListener('change', update))

  return reduced
}
