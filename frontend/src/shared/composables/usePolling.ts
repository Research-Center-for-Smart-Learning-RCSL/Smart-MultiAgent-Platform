// Shared job-status poller. Replaces the per-view hand-rolled recursive
// setTimeout loops (export status, GraphRAG build status) that each re-derived
// the disposed-guard / bounded-attempts / clear-on-unmount logic.
//
// Behaviour:
//   - polls `fetcher(key)` every `intervalMs` until `isTerminal(value)` or
//     `maxAttempts` is reached;
//   - a transient fetch error does NOT stop polling — it counts as an attempt
//     and reschedules, so one network blip can't strand the UI mid-job;
//   - all timers are cancelled on scope dispose (component unmount);
//   - keyed: `start(key)` may be called for several keys concurrently
//     (e.g. building multiple GraphRAG configs at once).

import { onScopeDispose } from 'vue'

export interface UsePollingOptions<T> {
  intervalMs?: number
  maxAttempts?: number
  isTerminal: (value: T) => boolean
  onResult: (key: string, value: T) => void
}

export interface PollingController {
  start: (key: string) => void
  stop: () => void
}

export function usePolling<T>(
  fetcher: (key: string) => Promise<T>,
  options: UsePollingOptions<T>,
): PollingController {
  const intervalMs = options.intervalMs ?? 3000
  const maxAttempts = options.maxAttempts ?? 40
  const timers = new Set<ReturnType<typeof setTimeout>>()
  let disposed = false

  function schedule(key: string, attempts: number): void {
    const timer = setTimeout(() => {
      timers.delete(timer)
      void tick(key, attempts)
    }, intervalMs)
    timers.add(timer)
  }

  async function tick(key: string, attempts: number): Promise<void> {
    if (disposed || attempts > maxAttempts) return
    try {
      const value = await fetcher(key)
      if (disposed) return
      options.onResult(key, value)
      if (options.isTerminal(value)) return
    } catch {
      // Transient — fall through and reschedule (still bounded by maxAttempts)
      // so a single blip does not permanently halt polling.
      if (disposed) return
    }
    schedule(key, attempts + 1)
  }

  function start(key: string): void {
    void tick(key, 0)
  }

  function stop(): void {
    disposed = true
    timers.forEach((t) => clearTimeout(t))
    timers.clear()
  }

  onScopeDispose(stop)

  return { start, stop }
}
