import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { AuthLimitGuard, withRaisedAuthLimit, type RateLimitAdmin, type RateLimitPolicy } from '../limits/authLimit.ts'

function fakeAdmin(initial: RateLimitPolicy): RateLimitAdmin & { policy: RateLimitPolicy; patches: unknown[] } {
  const state = { policy: { ...initial }, patches: [] as unknown[] }
  return Object.assign(state, {
    list: async () => [{ key: 'chat', window_sec: 60, max_count: 60, scope: 'user' }, { ...state.policy }],
    patch: async (_key: string, body: { window_sec?: number; max_count?: number }) => {
      state.patches.push(body)
      state.policy = { ...state.policy, ...body }
      return { ...state.policy }
    },
  })
}

const ORIGINAL: RateLimitPolicy = { key: 'auth', window_sec: 60, max_count: 10, scope: 'ip' }

function statePath(): string {
  return join(mkdtempSync(join(tmpdir(), 'sim-limit-')), '.state', 'local.json')
}

describe('AuthLimitGuard (AC-4 logic)', () => {
  it('records the original before raising, and restores it after a normal end', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const path = statePath()
    const guard = new AuthLimitGuard(admin, path, 'local')
    let restored: RateLimitPolicy | null = null
    const value = await withRaisedAuthLimit(
      guard,
      600,
      async () => {
        expect(existsSync(path)).toBe(true)
        expect(admin.policy.max_count).toBe(600)
        return 'done'
      },
      (p) => {
        restored = p
      },
    )
    expect(value).toBe('done')
    expect(admin.policy).toMatchObject({ window_sec: 60, max_count: 10 })
    expect(restored).toMatchObject({ max_count: 10 })
    expect(existsSync(path)).toBe(false)
  })

  it('restores when the body throws', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const path = statePath()
    const guard = new AuthLimitGuard(admin, path, 'local')
    await expect(
      withRaisedAuthLimit(guard, 600, async () => {
        throw new Error('step exploded')
      }, () => undefined),
    ).rejects.toThrow('step exploded')
    expect(admin.policy.max_count).toBe(10)
    expect(existsSync(path)).toBe(false)
  })

  it('restores a leftover from a killed process before anything else', async () => {
    const admin = fakeAdmin({ ...ORIGINAL, max_count: 600 })
    const path = statePath()
    // A first guard raised and the process died: the state file is all that is left.
    const dead = new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local')
    await dead.raise(600)
    const guard = new AuthLimitGuard(admin, path, 'local')
    expect(guard.hasLeftover()).toBe(true)
    const p = await guard.restoreLeftover()
    expect(p).toMatchObject({ max_count: 10, window_sec: 60 })
    expect(admin.policy.max_count).toBe(10)
    expect(guard.hasLeftover()).toBe(false)
  })

  it('does not patch when the policy is already at least as permissive', async () => {
    const admin = fakeAdmin({ ...ORIGINAL, max_count: 1000 })
    const guard = new AuthLimitGuard(admin, statePath(), 'local')
    await guard.raise(600)
    expect(admin.patches).toEqual([])
    await guard.restore()
    expect(admin.policy.max_count).toBe(1000)
  })

  it('refuses a state file written for another target', async () => {
    const path = statePath()
    await new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'staging').raise(600)
    await expect(new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local').restoreLeftover()).rejects.toThrow(/belongs to target staging/)
  })

  it('reports nothing to restore when there is no state file', async () => {
    const path = statePath()
    expect(await new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local').restore()).toBeNull()
  })
})
