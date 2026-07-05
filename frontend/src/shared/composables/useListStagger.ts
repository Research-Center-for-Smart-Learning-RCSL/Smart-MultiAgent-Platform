import { ref, watch, type Ref } from 'vue'

// How long the stagger window stays armed: max per-row delay (270ms) plus the
// item animation (200ms) plus margin. After this the class clears so later
// refetches, pagination, and live updates render statically.
const STAGGER_WINDOW_MS = 700

/**
 * First-load-only list entrance (motion language, R24.49): returns a class ref
 * carrying `list-stagger` while the first batch of rows mounts, then null.
 * Bind it to the list container (e.g. STable) — the row animation itself lives
 * in shared/styles/main.css under the same class, and collapses under
 * prefers-reduced-motion via the global freeze.
 *
 * Pass the query's loading ref; the stagger arms on the first loading -> false
 * transition (or immediately when data was cached and never loading).
 */
export function useListStagger(loading: Ref<boolean>): Ref<string | null> {
  const cls = ref<string | null>(null)

  function arm(): void {
    cls.value = 'list-stagger'
    setTimeout(() => {
      cls.value = null
    }, STAGGER_WINDOW_MS)
  }

  if (!loading.value) {
    arm()
  } else {
    const stop = watch(loading, (l) => {
      if (l) return
      arm()
      stop()
    })
  }

  return cls
}
