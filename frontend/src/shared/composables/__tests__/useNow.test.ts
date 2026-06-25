import { describe, it, expect, vi, afterEach } from 'vitest'
import { effectScope } from 'vue'
import { useNow } from '../useNow'

afterEach(() => {
  vi.useRealTimers()
})

describe('useNow', () => {
  it('advances the value on each interval tick', () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const now = scope.run(() => useNow(1000))!
    const start = now.value

    vi.advanceTimersByTime(1000)
    expect(now.value).toBeGreaterThan(start)

    scope.stop()
  })

  it('shares a single interval across subscribers and stops when the last disposes', () => {
    vi.useFakeTimers()
    const setInterval = vi.spyOn(globalThis, 'setInterval')
    const clearInterval = vi.spyOn(globalThis, 'clearInterval')

    const a = effectScope()
    const b = effectScope()
    a.run(() => useNow(1000))
    b.run(() => useNow(1000))

    // Two subscribers, one timer.
    expect(setInterval).toHaveBeenCalledTimes(1)

    a.stop()
    expect(clearInterval).not.toHaveBeenCalled() // b still subscribed
    b.stop()
    expect(clearInterval).toHaveBeenCalledTimes(1)

    setInterval.mockRestore()
    clearInterval.mockRestore()
  })
})
