import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  AuthLimitGuard,
  describeOutcome,
  withRaisedAuthLimit,
  type LimitReporter,
  type RateLimitAdmin,
  type RateLimitPolicy,
  type RestoreOutcome,
} from '../limits/authLimit.ts'

type FakeAdmin = RateLimitAdmin & { policy: RateLimitPolicy; patches: unknown[]; failPatch: boolean }

function fakeAdmin(initial: RateLimitPolicy): FakeAdmin {
  const admin: FakeAdmin = {
    policy: { ...initial },
    patches: [],
    failPatch: false,
    list: async () => [{ key: 'chat', window_sec: 60, max_count: 60, scope: 'user' }, { ...admin.policy }],
    patch: async (_key, body) => {
      if (admin.failPatch) throw new Error('503 from admin API')
      admin.patches.push(body)
      admin.policy = { ...admin.policy, ...body }
      return { ...admin.policy }
    },
  }
  return admin
}

const ORIGINAL: RateLimitPolicy = { key: 'auth', window_sec: 60, max_count: 10, scope: 'ip' }

function statePath(): string {
  return join(mkdtempSync(join(tmpdir(), 'sim-limit-')), '.state', 'local.json')
}

function reporter(): LimitReporter & { outcomes: RestoreOutcome[]; failures: string[] } {
  const r = {
    outcomes: [] as RestoreOutcome[],
    failures: [] as string[],
    restored: (o: RestoreOutcome) => r.outcomes.push(o),
    failed: (m: string) => r.failures.push(m),
  }
  return r
}

/** Rewrites the state file as if another process had written it. */
function ownedBy(path: string, pid: number): void {
  const state = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>
  writeFileSync(path, JSON.stringify({ ...state, pid }))
}

const DEAD_PID = 2 ** 22 - 1

describe('AuthLimitGuard (AC-4 logic)', () => {
  it('records the original before raising, and restores it after a normal end', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const path = statePath()
    const rep = reporter()
    const value = await withRaisedAuthLimit(
      new AuthLimitGuard(admin, path, 'local'),
      'local',
      600,
      async () => {
        expect(existsSync(path)).toBe(true)
        expect(admin.policy.max_count).toBe(600)
        return 'done'
      },
      rep,
    )
    expect(value).toBe('done')
    expect(admin.policy).toMatchObject({ window_sec: 60, max_count: 10 })
    expect(rep.outcomes).toEqual([{ kind: 'restored', policy: ORIGINAL }])
    expect(existsSync(path)).toBe(false)
  })

  it('restores when the body throws, and rethrows the body error', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const path = statePath()
    await expect(
      withRaisedAuthLimit(new AuthLimitGuard(admin, path, 'local'), 'local', 600, async () => {
        throw new Error('step exploded')
      }, reporter()),
    ).rejects.toThrow('step exploded')
    expect(admin.policy.max_count).toBe(10)
    expect(existsSync(path)).toBe(false)
  })

  it('reports a failed restore loudly, naming the recovery command and the earlier failure', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const rep = reporter()
    const run = withRaisedAuthLimit(new AuthLimitGuard(admin, statePath(), 'local'), 'local', 600, async () => {
      admin.failPatch = true
      throw new Error('step exploded')
    }, rep)
    await expect(run).rejects.toThrow(/NOT restored.*restore-limits --target local.*already failed: Error: step exploded/)
    expect(rep.failures).toHaveLength(1)
  })

  it('restores a leftover from a dead process before anything else', async () => {
    const path = statePath()
    await new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local').raise(600)
    ownedBy(path, DEAD_PID)
    const admin = fakeAdmin({ ...ORIGINAL, max_count: 600 })
    const outcome = await new AuthLimitGuard(admin, path, 'local').restoreLeftover()
    expect(outcome).toEqual({ kind: 'restored', policy: ORIGINAL })
    expect(admin.policy.max_count).toBe(10)
    expect(existsSync(path)).toBe(false)
  })

  it('leaves a live process\'s raise alone and refuses to start a second one', async () => {
    const path = statePath()
    await new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local').raise(600)
    ownedBy(path, process.ppid)
    const admin = fakeAdmin({ ...ORIGINAL, max_count: 600 })
    const guard = new AuthLimitGuard(admin, path, 'local')
    expect(guard.heldByOther()).toBe(process.ppid)
    expect(await guard.restoreLeftover()).toEqual({ kind: 'none' })
    expect(await guard.restore()).toMatchObject({ kind: 'untouched' })
    await expect(guard.raise(600)).rejects.toThrow(/is running against target local/)
    expect(admin.patches).toEqual([])
    expect(existsSync(path)).toBe(true)
  })

  it('does not overwrite a policy someone else changed during the run', async () => {
    const admin = fakeAdmin(ORIGINAL)
    const guard = new AuthLimitGuard(admin, statePath(), 'local')
    await guard.raise(600)
    admin.policy = { ...admin.policy, max_count: 30 }
    const outcome = await guard.restore()
    expect(outcome.kind).toBe('untouched')
    expect(admin.policy.max_count).toBe(30)
  })

  it('touches nothing when the policy is already permissive, and restores nothing after', async () => {
    // The concurrent-operator case: another machine's run has it at 600 already.
    const admin = fakeAdmin({ ...ORIGINAL, max_count: 600 })
    const guard = new AuthLimitGuard(admin, statePath(), 'local')
    await guard.raise(600)
    expect(admin.patches).toEqual([])
    expect(await guard.restore()).toEqual({ kind: 'none' })
    expect(admin.patches).toEqual([])
  })

  it('cleans up when the raising patch fails', async () => {
    const admin = fakeAdmin(ORIGINAL)
    admin.failPatch = true
    const path = statePath()
    await expect(new AuthLimitGuard(admin, path, 'local').raise(600)).rejects.toThrow('503')
    expect(existsSync(path)).toBe(false)
  })

  it('refuses a state file written for another target', async () => {
    const path = statePath()
    await new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'staging').raise(600)
    await expect(new AuthLimitGuard(fakeAdmin(ORIGINAL), path, 'local').restoreLeftover()).rejects.toThrow(/belongs to target staging/)
  })

  it('reports nothing to restore when there is no state file', async () => {
    const outcome = await new AuthLimitGuard(fakeAdmin(ORIGINAL), statePath(), 'local').restore()
    expect(outcome).toEqual({ kind: 'none' })
    expect(describeOutcome(outcome)).toBe('not changed by this command')
  })
})
