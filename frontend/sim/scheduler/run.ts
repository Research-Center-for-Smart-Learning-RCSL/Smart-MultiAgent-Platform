import type { Condition, Step } from '../scenario/schema.ts'

export interface Clock {
  now(): number
  sleep(ms: number, signal: AbortSignal): Promise<void>
}

export const realClock: Clock = {
  now: () => Date.now(),
  sleep: (ms, signal) =>
    new Promise((resolve) => {
      if (signal.aborted || ms <= 0) return resolve()
      const t = setTimeout(done, ms)
      function done(): void {
        clearTimeout(t)
        signal.removeEventListener('abort', done)
        resolve()
      }
      signal.addEventListener('abort', done)
    }),
}

export interface StepExecutor {
  /** Marks the actor as owned by a script step and pre-empts its persona. */
  begin(actor: string): void
  end(actor: string): void
  execute(step: Step): Promise<void>
  onFailure(step: Step, err: unknown): Promise<void>
}

export interface StepRecord {
  actor: string
  index: number
  action: string
  label?: string
  plannedSec: number | null
  armedSec: number
  startedSec: number | null
  endedSec: number
  outcome: 'ok' | 'failed' | 'timeout'
  error?: string
}

export interface RunContext {
  t0: number
  clock: Clock
  signal: AbortSignal
  abort: (reason: string) => void
  satisfied: (cond: Condition, armedAt: number) => boolean
  record: (rec: StepRecord) => void
  pollMs?: number
}

const sec = (ctx: RunContext, at: number): number => Math.round((at - ctx.t0) / 100) / 10

async function waitFor(cond: Condition, armedAt: number, timeoutMs: number, ctx: RunContext): Promise<boolean> {
  const deadline = ctx.clock.now() + timeoutMs
  for (;;) {
    if (ctx.signal.aborted) return false
    if (ctx.satisfied(cond, armedAt)) return true
    if (ctx.clock.now() >= deadline) return false
    await ctx.clock.sleep(Math.min(ctx.pollMs ?? 500, deadline - ctx.clock.now()), ctx.signal)
  }
}

/**
 * Runs one actor's steps in declared order. A step with `at` waits for that offset; a
 * step with `when` then waits for its condition, armed at that moment. Actors run in
 * parallel, so two actors' steps planned for the same second fire together.
 */
export async function runActor(
  actor: string,
  steps: Array<{ index: number; step: Step }>,
  exec: StepExecutor,
  ctx: RunContext,
): Promise<void> {
  for (const { index, step } of steps) {
    if (ctx.signal.aborted) return
    if (step.at !== undefined) await ctx.clock.sleep(ctx.t0 + step.at * 1000 - ctx.clock.now(), ctx.signal)
    if (ctx.signal.aborted) return
    const armedAt = ctx.clock.now()
    const base = {
      actor,
      index,
      action: step.do,
      ...(step.label ? { label: step.label } : {}),
      plannedSec: step.at ?? null,
      armedSec: sec(ctx, armedAt),
    }
    if (step.when) {
      const met = await waitFor(step.when, armedAt, step.timeoutSec * 1000, ctx)
      if (ctx.signal.aborted) return
      if (!met) {
        ctx.record({ ...base, startedSec: null, endedSec: sec(ctx, ctx.clock.now()), outcome: 'timeout' })
        if (step.onTimeout === 'abort') {
          ctx.abort(`step ${index} (${actor} ${step.do}) timed out waiting for ${JSON.stringify(step.when)}`)
          return
        }
        continue
      }
    }
    const startedAt = ctx.clock.now()
    exec.begin(actor)
    try {
      await exec.execute(step)
      ctx.record({ ...base, startedSec: sec(ctx, startedAt), endedSec: sec(ctx, ctx.clock.now()), outcome: 'ok' })
    } catch (err) {
      await exec.onFailure(step, err)
      ctx.record({
        ...base,
        startedSec: sec(ctx, startedAt),
        endedSec: sec(ctx, ctx.clock.now()),
        outcome: 'failed',
        error: err instanceof Error ? err.message.split('\n')[0] : String(err),
      })
    } finally {
      exec.end(actor)
    }
  }
}

export function stepsByActor(steps: Step[]): Map<string, Array<{ index: number; step: Step }>> {
  const out = new Map<string, Array<{ index: number; step: Step }>>()
  steps.forEach((step, index) => {
    const list = out.get(step.actor) ?? []
    list.push({ index, step })
    out.set(step.actor, list)
  })
  return out
}
