import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

export interface RateLimitPolicy {
  key: string
  window_sec: number
  max_count: number
  scope: string
}

/** The subset of the admin API this module needs; a fake implements it in tests. */
export interface RateLimitAdmin {
  list(): Promise<RateLimitPolicy[]>
  patch(key: string, body: { window_sec?: number; max_count?: number }): Promise<RateLimitPolicy>
}

interface StateFile {
  target: string
  raisedAt: string
  original: RateLimitPolicy
}

const KEY = 'auth'

/**
 * Raises the AUTH policy for a run and puts it back (spec §5 "Rate-limit guard").
 *
 * The original is written to the state file before the patch, so a process killed
 * between the two still leaves enough on disk for the next start to restore it.
 */
export class AuthLimitGuard {
  private readonly admin: RateLimitAdmin
  private readonly statePath: string
  private readonly targetName: string

  constructor(admin: RateLimitAdmin, statePath: string, targetName: string) {
    this.admin = admin
    this.statePath = statePath
    this.targetName = targetName
  }

  hasLeftover(): boolean {
    return existsSync(this.statePath)
  }

  /** Restores a policy left raised by an earlier process. Returns what was restored, if anything. */
  async restoreLeftover(): Promise<RateLimitPolicy | null> {
    if (!this.hasLeftover()) return null
    const state = JSON.parse(readFileSync(this.statePath, 'utf8')) as StateFile
    if (state.target !== this.targetName) {
      throw new Error(`state file ${this.statePath} belongs to target ${state.target}, not ${this.targetName}`)
    }
    await this.admin.patch(KEY, { window_sec: state.original.window_sec, max_count: state.original.max_count })
    rmSync(this.statePath)
    return state.original
  }

  async raise(maxCount: number): Promise<RateLimitPolicy> {
    await this.restoreLeftover()
    const current = (await this.admin.list()).find((p) => p.key === KEY)
    if (!current) throw new Error(`rate-limit policy "${KEY}" not found`)
    mkdirSync(dirname(this.statePath), { recursive: true })
    const state: StateFile = { target: this.targetName, raisedAt: new Date().toISOString(), original: current }
    writeFileSync(this.statePath, JSON.stringify(state, null, 2))
    if (current.max_count < maxCount || current.window_sec !== 60) {
      await this.admin.patch(KEY, { window_sec: 60, max_count: Math.max(maxCount, current.max_count) })
    }
    return current
  }

  async restore(): Promise<RateLimitPolicy | null> {
    return this.restoreLeftover()
  }
}

/**
 * Runs `body` with the AUTH limit raised and restores it on every exit path, including
 * SIGINT/SIGTERM while `body` is still running.
 */
export async function withRaisedAuthLimit<T>(
  guard: AuthLimitGuard,
  maxCount: number,
  body: () => Promise<T>,
  onRestored: (p: RateLimitPolicy | null) => void,
): Promise<T> {
  await guard.raise(maxCount)
  let restoring: Promise<void> | null = null
  const restoreOnce = (): Promise<void> => {
    restoring ??= guard.restore().then(onRestored)
    return restoring
  }
  const onSignal = (signal: NodeJS.Signals): void => {
    void restoreOnce().finally(() => process.exit(signal === 'SIGINT' ? 130 : 143))
  }
  process.once('SIGINT', onSignal)
  process.once('SIGTERM', onSignal)
  try {
    return await body()
  } finally {
    process.off('SIGINT', onSignal)
    process.off('SIGTERM', onSignal)
    await restoreOnce()
  }
}
