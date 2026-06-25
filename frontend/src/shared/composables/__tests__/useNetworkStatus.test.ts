import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

const qc = vi.hoisted(() => ({ invalidate: vi.fn() }))
const transport = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@shared/query-client', () => ({
  queryClient: { invalidateQueries: () => qc.invalidate() },
}))
vi.mock('@shared/transport', () => ({
  http: { get: (...args: unknown[]) => transport.get(...args) },
}))

import {
  useNetworkStatus,
  markConnectionLost,
  markConnectionRestored,
} from '../useNetworkStatus'

beforeEach(() => {
  vi.useFakeTimers()
  // Reset the module singleton to the online baseline before each case.
  markConnectionRestored()
  qc.invalidate.mockClear()
  transport.get.mockReset()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('useNetworkStatus', () => {
  it('starts online and not reconnecting', () => {
    const { online, reconnecting } = useNetworkStatus()
    expect(online.value).toBe(true)
    expect(reconnecting.value).toBe(false)
  })

  it('flips offline (and reconnecting) when the connection is lost', () => {
    const { online, reconnecting } = useNetworkStatus()
    markConnectionLost()
    expect(online.value).toBe(false)
    expect(reconnecting.value).toBe(true)
  })

  it('invalidates queries exactly once on a genuine offline->online transition', () => {
    markConnectionLost()
    markConnectionRestored()
    expect(qc.invalidate).toHaveBeenCalledTimes(1)

    // A second restore while already online must not refetch everything again.
    markConnectionRestored()
    expect(qc.invalidate).toHaveBeenCalledTimes(1)
  })

  it('does not restart the backoff when already offline', () => {
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    markConnectionLost()
    const afterFirst = setTimeoutSpy.mock.calls.length
    markConnectionLost()
    expect(setTimeoutSpy.mock.calls.length).toBe(afterFirst)
    setTimeoutSpy.mockRestore()
  })

  it('probes immediately on retryNow and recovers when the server answers', async () => {
    // retryNow fires the probe directly (no backoff wait), so drive it on real
    // timers and flush the awaited health call.
    vi.useRealTimers()
    transport.get.mockResolvedValue({ data: { status: 'ok' } })
    const { online, retryNow } = useNetworkStatus()
    markConnectionLost()
    expect(online.value).toBe(false)

    retryNow()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(transport.get).toHaveBeenCalledWith('/healthz', { baseURL: '' })
    expect(online.value).toBe(true)
  })
})
