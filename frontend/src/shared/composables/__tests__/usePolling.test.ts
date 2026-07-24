import { describe, it, expect, vi, afterEach } from 'vitest'
import { effectScope } from 'vue'
import { usePolling } from '../usePolling'

afterEach(() => {
  vi.useRealTimers()
})

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void }

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('usePolling', () => {
  it('cancel(key) stops only that key', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const fetcher = vi.fn(async (key: string) => ({ key, done: false }))
    const onResult = vi.fn()

    const poll = scope.run(() =>
      usePolling(fetcher, {
        intervalMs: 1000,
        isTerminal: (v: { done: boolean }) => v.done,
        onResult,
      }),
    )!

    poll.start('A')
    poll.start('B')
    await vi.advanceTimersByTimeAsync(0)
    fetcher.mockClear()
    onResult.mockClear()

    poll.cancel('A')
    await vi.advanceTimersByTimeAsync(3000)

    const polled = fetcher.mock.calls.map(([key]) => key)
    expect(polled).not.toContain('A')
    expect(polled).toContain('B')
    expect(onResult.mock.calls.map(([key]) => key)).not.toContain('A')

    scope.stop()
  })

  it('does not deliver a fetch that was already in flight when the key was cancelled', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const pending = deferred<{ done: boolean }>()
    const onResult = vi.fn()

    const poll = scope.run(() =>
      usePolling(() => pending.promise, {
        intervalMs: 1000,
        isTerminal: (v: { done: boolean }) => v.done,
        onResult,
      }),
    )!

    poll.start('A')
    poll.cancel('A')
    pending.resolve({ done: true })
    await vi.advanceTimersByTimeAsync(0)

    expect(onResult).not.toHaveBeenCalled()

    scope.stop()
  })

  it('restarting a cancelled key polls again', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const fetcher = vi.fn(async () => ({ done: false }))

    const poll = scope.run(() =>
      usePolling(fetcher, {
        intervalMs: 1000,
        isTerminal: (v: { done: boolean }) => v.done,
        onResult: vi.fn(),
      }),
    )!

    poll.start('A')
    poll.cancel('A')
    fetcher.mockClear()
    poll.start('A')
    await vi.advanceTimersByTimeAsync(0)

    expect(fetcher).toHaveBeenCalledWith('A')

    scope.stop()
  })

  it('stop() cancels every key and survives scope disposal', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const fetcher = vi.fn(async () => ({ done: false }))
    const onResult = vi.fn()

    const poll = scope.run(() =>
      usePolling(fetcher, {
        intervalMs: 1000,
        isTerminal: (v: { done: boolean }) => v.done,
        onResult,
      }),
    )!

    poll.start('A')
    poll.start('B')
    await vi.advanceTimersByTimeAsync(0)

    // Scope disposal is wired to stop(); calling it twice must not throw.
    scope.stop()
    poll.stop()
    fetcher.mockClear()
    onResult.mockClear()

    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher).not.toHaveBeenCalled()
    expect(onResult).not.toHaveBeenCalled()
  })

  it('stops a key once its value is terminal', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const fetcher = vi.fn(async () => ({ done: true }))

    const poll = scope.run(() =>
      usePolling(fetcher, {
        intervalMs: 1000,
        isTerminal: (v: { done: boolean }) => v.done,
        onResult: vi.fn(),
      }),
    )!

    poll.start('A')
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher).toHaveBeenCalledTimes(1)

    scope.stop()
  })
})
