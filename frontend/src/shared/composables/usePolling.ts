// Shared job-status poller. Replaces the per-view hand-rolled recursive
// setTimeout loops (export status, GraphRAG build status) that each re-derived
// the disposed-guard / bounded-attempts / clear-on-unmount logic.
//
// Behaviour:
//   - polls `fetcher(key)` every `intervalMs` until `isTerminal(value)` or
//     `maxAttempts` is reached;
//   - a transient fetch error does NOT stop polling — it counts as an attempt
//     and reschedules, so one network blip can't strand the UI mid-job;
//   - an error `isFatal` accepts stops the key and goes to `onError`, and
//     running out of attempts goes to `onExhausted`, so the consumer always
//     learns how polling ended (a later `start(key)` resumes either way);
//   - `intervalMs` may be a function of the attempt number, to back off;
//   - keyed: `start(key)` may be called for several keys concurrently
//     (e.g. building multiple GraphRAG configs at once), and `cancel(key)`
//     stops one of them without touching the others — including dropping a
//     fetch that is already in flight for that key when it is cancelled;
//   - `stop()` cancels every key permanently and is wired to scope dispose
//     (component unmount).

import { onScopeDispose } from 'vue'

export interface UsePollingOptions<T> {
  /** Delay before each attempt after the first; a function receives the attempt number (1-based). */
  intervalMs?: number | ((attempt: number) => number)
  maxAttempts?: number
  isTerminal: (value: T) => boolean
  onResult: (key: string, value: T) => void
  /** Errors that stop the key; each one is reported through `onError`. */
  isFatal?: (error: unknown) => boolean
  onError?: (key: string, error: unknown) => void
  onExhausted?: (key: string) => void
  /** A minimum wait before the next attempt after a transient error (e.g. a 429's Retry-After). */
  retryAfterMs?: (error: unknown) => number | null
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
  const interval = options.intervalMs ?? 3000
  const delayFor = typeof interval === 'function' ? interval : () => interval
  const maxAttempts = options.maxAttempts ?? 40
  // One timer per active key, so cancelling a key clears exactly its pending
  // tick. A key may be active with no pending timer (between start() and the
  // first reschedule, or while a fetch is in flight), so activeness is tracked
  // separately from the timer handle.
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  // The current run of each active key. A tick carries the run it belongs to
  // and does nothing once that run is no longer current: cancelled (absent),
  // or restarted (a newer run) while its fetch was in flight. That is what
  // makes a stale fetch drop instead of writing to a single-slot consumer, and
  // what keeps a restart from leaving two loops on one key.
  const runs = new Map<string, number>()
  let nextRun = 0
  let disposed = false

  function current(key: string, run: number): boolean {
    return !disposed && runs.get(key) === run
  }

  function schedule(key: string, run: number, attempts: number, minDelayMs: number): void {
    const delay = Math.max(delayFor(attempts), minDelayMs)
    timers.set(key, setTimeout(() => void tick(key, run, attempts), delay))
  }

  async function tick(key: string, run: number, attempts: number): Promise<void> {
    if (!current(key, run)) return
    let value: T
    try {
      value = await fetcher(key)
    } catch (error) {
      if (!current(key, run)) return
      if (options.isFatal?.(error)) {
        cancel(key)
        options.onError?.(key, error)
        return
      }
      // Transient: reschedule (still bounded by maxAttempts) so a single blip
      // does not permanently halt polling.
      next(key, run, attempts, options.retryAfterMs?.(error) ?? 0)
      return
    }
    // Only the fetch is guarded above: an error thrown by the consumer's own
    // callbacks is a bug to surface, not a network blip to retry.
    if (!current(key, run)) return
    options.onResult(key, value)
    if (options.isTerminal(value)) {
      cancel(key)
      return
    }
    next(key, run, attempts, 0)
  }

  function next(key: string, run: number, attempts: number, minDelayMs: number): void {
    if (attempts >= maxAttempts) {
      // Cleared before the callback, so the consumer may start(key) again from it.
      cancel(key)
      options.onExhausted?.(key)
      return
    }
    schedule(key, run, attempts + 1, minDelayMs)
  }

  function start(key: string): void {
    if (disposed) return
    cancel(key)
    const run = ++nextRun
    runs.set(key, run)
    void tick(key, run, 0)
  }

  function cancel(key: string): void {
    const timer = timers.get(key)
    if (timer !== undefined) clearTimeout(timer)
    timers.delete(key)
    runs.delete(key)
  }

  function stop(): void {
    disposed = true
    timers.forEach((t) => clearTimeout(t))
    timers.clear()
    runs.clear()
  }

  onScopeDispose(stop)

  return { start, cancel, stop }
}
