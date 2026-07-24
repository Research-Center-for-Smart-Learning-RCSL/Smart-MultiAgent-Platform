// Shared job-status poller. Replaces the per-view hand-rolled recursive
// setTimeout loops (export status, GraphRAG build status) that each re-derived
// the disposed-guard / bounded-attempts / clear-on-unmount logic.
//
// Behaviour:
//   - polls `fetcher(key)` every `intervalMs` until `isTerminal(value)` or
//     `maxAttempts` is reached;
//   - a transient fetch error does NOT stop polling — it counts as an attempt
//     and reschedules, so one network blip can't strand the UI mid-job;
//   - keyed: `start(key)` may be called for several keys concurrently
//     (e.g. building multiple GraphRAG configs at once), and `cancel(key)`
//     stops one of them without touching the others — including dropping a
//     fetch that is already in flight for that key when it is cancelled;
//   - `stop()` cancels every key permanently and is wired to scope dispose
//     (component unmount).

import { onScopeDispose } from 'vue'

export interface UsePollingOptions<T> {
  intervalMs?: number
  maxAttempts?: number
  isTerminal: (value: T) => boolean
  onResult: (key: string, value: T) => void
}

export interface PollingController {
  start: (key: string) => void
  cancel: (key: string) => void
  stop: () => void
}

export function usePolling<T>(
  fetcher: (key: string) => Promise<T>,
  options: UsePollingOptions<T>,
): PollingController {
  const intervalMs = options.intervalMs ?? 3000
  const maxAttempts = options.maxAttempts ?? 40
  // One timer per active key, so cancelling a key clears exactly its pending
  // tick. Membership also gates delivery: a key absent from the map is
  // cancelled, which is what makes an in-flight fetch drop instead of writing.
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  let disposed = false

  function schedule(key: string, attempts: number): void {
    // The map entry for `key` stays set to this (soon-fired) timer until the
    // next tick overwrites it; keeping the key present is what `isActive`
    // reads, so we must not delete it when the timer fires.
    const timer = setTimeout(() => void tick(key, attempts), intervalMs)
    timers.set(key, timer)
  }

  async function tick(key: string, attempts: number): Promise<void> {
    if (disposed || !isActive(key) || attempts > maxAttempts) return
    try {
      const value = await fetcher(key)
      // Re-check after the await: the key may have been cancelled (or the
      // whole poller disposed) while this fetch was in flight. Delivering it
      // would let a superseded job overwrite a single-slot consumer.
      if (disposed || !isActive(key)) return
      options.onResult(key, value)
      if (options.isTerminal(value)) {
        timers.delete(key)
        return
      }
    } catch {
      // Transient — fall through and reschedule (still bounded by maxAttempts)
      // so a single blip does not permanently halt polling.
      if (disposed || !isActive(key)) return
    }
    schedule(key, attempts + 1)
  }

  function isActive(key: string): boolean {
    return timers.has(key)
  }

  function start(key: string): void {
    if (disposed) return
    // Mark the key active before the first tick so an immediate in-flight
    // fetch is delivered; the placeholder is replaced by the real timer once
    // the tick reschedules.
    const existing = timers.get(key)
    if (existing !== undefined) clearTimeout(existing)
    timers.set(key, 0 as unknown as ReturnType<typeof setTimeout>)
    void tick(key, 0)
  }

  function cancel(key: string): void {
    const timer = timers.get(key)
    if (timer !== undefined) clearTimeout(timer)
    timers.delete(key)
  }

  function stop(): void {
    disposed = true
    timers.forEach((t) => clearTimeout(t))
    timers.clear()
  }

  onScopeDispose(stop)

  return { start, cancel, stop }
}
