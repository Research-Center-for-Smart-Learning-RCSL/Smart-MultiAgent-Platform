import { onScopeDispose, readonly, ref, type Ref } from 'vue'

// A shared, ticking "current time" for relative-timestamp displays. Without a
// reactive clock, a `computed` over a fixed timestamp (e.g. "2 min ago") never
// re-evaluates and the label freezes while the page stays open.
//
// All callers share ONE interval via subscriber refcounting, so a list of N
// cards costs a single timer, not N. The tick cadence is fixed by the first
// subscriber; 60s suits minute-granular relative times.
let subscribers = 0
let timer: ReturnType<typeof setInterval> | undefined
const nowRef = ref(Date.now())

export function useNow(intervalMs = 60_000): Readonly<Ref<number>> {
  subscribers += 1
  if (!timer) {
    nowRef.value = Date.now()
    timer = setInterval(() => {
      nowRef.value = Date.now()
    }, intervalMs)
  }

  onScopeDispose(() => {
    subscribers -= 1
    if (subscribers <= 0 && timer) {
      clearInterval(timer)
      timer = undefined
      subscribers = 0
    }
  })

  return readonly(nowRef)
}
