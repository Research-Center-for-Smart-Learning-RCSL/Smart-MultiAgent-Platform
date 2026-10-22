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

type Limit = Pick<RateLimitPolicy, 'window_sec' | 'max_count'>

interface StateFile {
  target: string
  raisedAt: string
  pid: number
  original: RateLimitPolicy
  /** What this run set, or null when the policy was already permissive enough. */
  applied: Limit | null
}

export type RestoreOutcome =
  | { kind: 'none' }
  | { kind: 'restored'; policy: Limit }
  | { kind: 'untouched'; reason: string }

const KEY = 'auth'

function isAlive(pid: number): boolean {
  if (pid === process.pid) return true
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'
  }
}

export function describeOutcome(o: RestoreOutcome): string {
  if (o.kind === 'restored') return `restored to ${o.policy.max_count} per ${o.policy.window_sec}s`
  if (o.kind === 'untouched') return `left as is: ${o.reason}`
  return 'not changed by this command'
}

/**
 * Raises the AUTH policy for a run and puts it back (spec §5 "Rate-limit guard").
 *
 * The original is written to the state file before the patch, so a process killed
 * between the two still leaves enough on disk for the next start to restore it. Restore
 * is compare-and-set: it writes the original back only while the live policy still holds
 * what this run applied, so a concurrent run or an admin's change made meanwhile is not
 * overwritten.
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

  private readState(): StateFile | null {
    if (!existsSync(this.statePath)) return null
    const state = JSON.parse(readFileSync(this.statePath, 'utf8')) as StateFile
    if (state.target !== this.targetName) {
      throw new Error(`state file ${this.statePath} belongs to target ${state.target}, not ${this.targetName}`)
    }
    return state
  }

  /** The pid of another live process that holds the raise on this target, if any. */
  heldByOther(): number | null {
    const state = this.readState()
    return state && state.pid !== process.pid && isAlive(state.pid) ? state.pid : null
  }

  private async restoreFrom(state: StateFile): Promise<RestoreOutcome> {
    if (state.applied) {
      const live = (await this.admin.list()).find((p) => p.key === KEY)
      if (!live || live.window_sec !== state.applied.window_sec || live.max_count !== state.applied.max_count) {
        rmSync(this.statePath)
        return {
          kind: 'untouched',
          reason: `the live policy (${live?.max_count}/${live?.window_sec}s) is no longer what this tool set; something else changed it`,
        }
      }
      await this.admin.patch(KEY, { window_sec: state.original.window_sec, max_count: state.original.max_count })
    }
    rmSync(this.statePath)
    return state.applied ? { kind: 'restored', policy: state.original } : { kind: 'none' }
  }

  /** Restores a policy a dead process left raised. A live owner's raise is left alone. */
  async restoreLeftover(): Promise<RestoreOutcome> {
    const state = this.readState()
    if (!state || (state.pid !== process.pid && isAlive(state.pid))) return { kind: 'none' }
    return this.restoreFrom(state)
  }

  async raise(maxCount: number): Promise<RateLimitPolicy> {
    const other = this.heldByOther()
    if (other !== null) throw new Error(`process ${other} is running against target ${this.targetName}; wait for it to finish`)
    await this.restoreLeftover()
    const current = (await this.admin.list()).find((p) => p.key === KEY)
    if (!current) throw new Error(`rate-limit policy "${KEY}" not found`)
    const needed = current.max_count < maxCount || current.window_sec !== 60
    const applied: Limit | null = needed ? { window_sec: 60, max_count: Math.max(maxCount, current.max_count) } : null
    mkdirSync(dirname(this.statePath), { recursive: true })
    const state: StateFile = { target: this.targetName, raisedAt: new Date().toISOString(), pid: process.pid, original: current, applied }
    writeFileSync(this.statePath, JSON.stringify(state, null, 2))
    if (applied) {
      try {
        await this.admin.patch(KEY, applied)
      } catch (err) {
        // The patch may have applied before failing; restoring is a no-op when it did not.
        await this.restoreFrom(state).catch(() => undefined)
        throw err
      }
    }
    return current
  }

  async restore(): Promise<RestoreOutcome> {
    const state = this.readState()
    if (!state) return { kind: 'none' }
    if (state.pid !== process.pid && isAlive(state.pid)) {
      return { kind: 'untouched', reason: `process ${state.pid} still holds it` }
    }
    return this.restoreFrom(state)
  }
}

export function restoreFailedMessage(target: string, err: unknown): string {
  return (
    `the AUTH rate limit was NOT restored (${err instanceof Error ? err.message : String(err)}). ` +
    `Run \`pnpm sim restore-limits --target ${target}\` now; the platform's login limit stays raised until then.`
  )
}

export interface LimitReporter {
  restored(outcome: RestoreOutcome): void
  failed(message: string): void
}

/**
 * Runs `body` with the AUTH limit raised and restores it on every exit path, including
 * SIGINT/SIGTERM while `body` is still running. A failed restore is never silent.
 */
export async function withRaisedAuthLimit<T>(
  guard: AuthLimitGuard,
  target: string,
  maxCount: number,
  body: () => Promise<T>,
  report: LimitReporter,
): Promise<T> {
  await guard.raise(maxCount)
  let restoring: Promise<RestoreOutcome> | null = null
  const restoreOnce = (): Promise<RestoreOutcome> => {
    restoring ??= guard.restore().then((o) => {
      report.restored(o)
      return o
    })
    return restoring
  }
  const onSignal = (signal: NodeJS.Signals): void => {
    restoreOnce().then(
      () => process.exit(signal === 'SIGINT' ? 130 : 143),
      (err: unknown) => {
        report.failed(restoreFailedMessage(target, err))
        process.exit(2)
      },
    )
  }
  // `on`, not `once`: a second Ctrl-C would otherwise hit Node's default handler and exit mid-restore.
  process.on('SIGINT', onSignal)
  process.on('SIGTERM', onSignal)
  let result: { ok: true; value: T } | { ok: false; error: unknown }
  try {
    result = { ok: true, value: await body() }
  } catch (error) {
    result = { ok: false, error }
  }
  // The handlers stay attached through the restore, so a Ctrl-C now waits for it.
  try {
    await restoreOnce()
  } catch (err) {
    const message = restoreFailedMessage(target, err)
    report.failed(message)
    throw new Error(result.ok ? message : `${message} The run had already failed: ${String(result.error)}`)
  } finally {
    process.off('SIGINT', onSignal)
    process.off('SIGTERM', onSignal)
  }
  if (!result.ok) throw result.error
  return result.value
}
