import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'

const router = vi.hoisted(() => ({ push: vi.fn().mockResolvedValue(undefined) }))
const transport = vi.hoisted(() => ({
  get: vi.fn(),
  refresh: vi.fn().mockResolvedValue('new-token'),
}))
const toast = vi.hoisted(() => ({ info: vi.fn() }))
// `reactive` is a normal import (unavailable inside vi.hoisted), so the store is
// built in beforeEach and held in this hoisted container the mock reads from.
const holder = vi.hoisted(() => ({
  store: undefined as undefined | { isAuthenticated: boolean; logout: ReturnType<typeof vi.fn> },
}))

vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('@shared/stores/session', () => ({ useSessionStore: () => holder.store }))
vi.mock('@shared/i18n', () => ({ i18n: { global: { t: (k: string) => k } } }))
vi.mock('@shared/transport', () => ({
  http: { get: (...a: unknown[]) => transport.get(...a) },
  refreshAccessToken: () => transport.refresh(),
}))
vi.mock('../useToast', () => ({ useToast: () => toast }))

import { useIdleLogout } from '../useIdleLogout'

// Tight window so the case runs in a few simulated seconds: warn at 2s idle,
// hard logout at 4s idle.
const POLICY = { idle_timeout_seconds: 4, idle_warning_seconds: 2 }

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(0)
  holder.store = reactive({
    isAuthenticated: false,
    logout: vi.fn().mockResolvedValue(undefined),
  })
  transport.get.mockResolvedValue({ data: POLICY })
  transport.refresh.mockClear().mockResolvedValue('new-token')
  router.push.mockClear()
  toast.info.mockClear()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('useIdleLogout', () => {
  it('warns after inactivity then signs out when the countdown elapses', async () => {
    // The singleton installs on first call; subsequent calls return the same
    // handle, so this whole lifecycle is exercised in one case.
    const { warningActive, remainingSeconds } = useIdleLogout()

    // Becomes authenticated -> guard starts watching and fetches the policy.
    holder.store!.isAuthenticated = true
    await nextTick()
    // Flush the loadPolicy() microtask without advancing the clock.
    await Promise.resolve()
    await Promise.resolve()

    expect(warningActive.value).toBe(false)

    // Cross the warn threshold (timeout - warning = 2s of no activity).
    await vi.advanceTimersByTimeAsync(2000)
    expect(warningActive.value).toBe(true)
    expect(remainingSeconds.value).toBeGreaterThan(0)
    expect(remainingSeconds.value).toBeLessThanOrEqual(2)

    // Run out the countdown -> automatic logout + redirect + notice.
    await vi.advanceTimersByTimeAsync(2000)
    expect(holder.store!.logout).toHaveBeenCalledTimes(1)
    expect(router.push).toHaveBeenCalledWith({ name: 'identity.login' })
    expect(toast.info).toHaveBeenCalledWith('app.idle.signedOut')
    expect(warningActive.value).toBe(false)
  })
})
