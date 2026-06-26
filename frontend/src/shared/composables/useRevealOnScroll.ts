import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * Reveals an element the first time it scrolls into view, for entrance
 * animations. Bind the returned `el` to the target and drive a CSS class off
 * `revealed`.
 *
 * Degrades gracefully: when the user prefers reduced motion, or
 * IntersectionObserver is unavailable (SSR / jsdom), `revealed` starts `true`
 * so content shows immediately and never depends on motion. The observer is
 * disconnected both after the first intersection and on unmount, so its
 * callback closure (and the observed node) cannot leak if the component is torn
 * down before it ever intersects.
 */
export function useRevealOnScroll(options: { threshold?: number } = {}) {
  const el = ref<HTMLElement | null>(null)
  const revealed = ref(false)
  let observer: IntersectionObserver | null = null

  function prefersReducedMotion(): boolean {
    return (
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
  }

  onMounted(() => {
    if (prefersReducedMotion() || typeof IntersectionObserver === 'undefined' || !el.value) {
      revealed.value = true
      return
    }
    observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          revealed.value = true
          observer?.disconnect()
          observer = null
        }
      },
      { threshold: options.threshold ?? 0.2 },
    )
    observer.observe(el.value)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })

  return { el, revealed }
}
