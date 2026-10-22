import { describe, expect, it } from 'vitest'
import { runActor, stepsByActor, type Clock, type RunContext, type StepExecutor, type StepRecord } from '../scheduler/run.ts'
import { StepSchema, type Condition, type Step } from '../scenario/schema.ts'

/** Virtual time: sleeping advances the clock at once, so a 30-minute timeline runs instantly. */
function virtualClock(): Clock & { t: number } {
  const c = {
    t: 0,
    now: () => c.t,
    sleep: async (ms: number, signal: AbortSignal) => {
      if (!signal.aborted && ms > 0) c.t += ms
    },
  }
  return c
}

function steps(raw: unknown[]): Step[] {
  return raw.map((s) => StepSchema.parse(s))
}

function harness(opts: { satisfied?: (cond: Condition, armedAt: number, now: number) => boolean; fail?: Set<number> } = {}) {
  const clock = virtualClock()
  const records: StepRecord[] = []
  const calls: string[] = []
  const controller = new AbortController()
  let aborted: string | null = null
  let n = 0
  const exec: StepExecutor = {
    begin: (a) => calls.push(`begin ${a}`),
    end: (a) => calls.push(`end ${a}`),
    execute: async (step) => {
      const i = n++
      calls.push(`exec ${step.do}@${clock.now() / 1000}`)
      if (opts.fail?.has(i)) throw new Error('selector not found\nstack')
    },
    onFailure: async (step) => {
      calls.push(`fail ${step.do}`)
    },
  }
  const ctx: RunContext = {
    t0: 0,
    clock,
    signal: controller.signal,
    abort: (reason) => {
      aborted = reason
      controller.abort()
    },
    satisfied: (cond, armedAt) => (opts.satisfied ?? (() => true))(cond, armedAt, clock.now()),
    record: (r) => records.push(r),
    pollMs: 500,
  }
  return { clock, records, calls, exec, ctx, aborted: () => aborted }
}

describe('runActor (AC-7 logic, AC-8)', () => {
  it('fires `at` steps at their planned offset and records planned and actual times', async () => {
    const h = harness()
    const list = steps([
      { actor: 'S1', at: 5, do: 'say', text: 'a' },
      { actor: 'S1', at: '1:30', do: 'say', text: 'b' },
    ])
    await runActor('S1', list.map((step, index) => ({ index, step })), h.exec, h.ctx)
    expect(h.records.map((r) => [r.plannedSec, r.startedSec, r.outcome])).toEqual([
      [5, 5, 'ok'],
      [90, 90, 'ok'],
    ])
  })

  it('waits for a `when` condition armed at the `at` offset', async () => {
    const h = harness({ satisfied: (_c, armedAt, now) => armedAt === 10_000 && now >= 17_000 })
    const list = steps([{ actor: 'F', at: 10, when: { type: 'message', from: 'S1' }, do: 'say', text: 'ok' }])
    await runActor('F', [{ index: 0, step: list[0] as Step }], h.exec, h.ctx)
    expect(h.records[0]).toMatchObject({ armedSec: 10, startedSec: 17, outcome: 'ok' })
  })

  it('records a timeout and continues under onTimeout skip', async () => {
    const h = harness({ satisfied: () => false })
    const list = steps([
      { actor: 'F', at: 0, when: { type: 'activation', state: 'active' }, timeoutSec: 5, do: 'endActivity' },
      { actor: 'F', at: 20, do: 'say', text: 'after' },
    ])
    await runActor('F', list.map((step, index) => ({ index, step })), h.exec, h.ctx)
    expect(h.records.map((r) => r.outcome)).toEqual(['timeout', 'ok'])
    expect(h.records[0]).toMatchObject({ startedSec: null, endedSec: 5 })
    expect(h.aborted()).toBeNull()
  })

  it('aborts the run under onTimeout abort and runs nothing after', async () => {
    const h = harness({ satisfied: () => false })
    const list = steps([
      { actor: 'F', at: 0, when: { type: 'proposal', status: 'accepted' }, timeoutSec: 5, onTimeout: 'abort', do: 'endActivity' },
      { actor: 'F', at: 20, do: 'say', text: 'never' },
    ])
    await runActor('F', list.map((step, index) => ({ index, step })), h.exec, h.ctx)
    expect(h.records.map((r) => r.outcome)).toEqual(['timeout'])
    expect(h.aborted()).toMatch(/timed out/)
    expect(h.calls.some((c) => c.startsWith('exec'))).toBe(false)
  })

  it('records a failed step with its first error line and keeps going', async () => {
    const h = harness({ fail: new Set([0]) })
    const list = steps([
      { actor: 'S1', at: 1, do: 'openActivity' },
      { actor: 'S1', at: 2, do: 'say', text: 'still here' },
    ])
    await runActor('S1', list.map((step, index) => ({ index, step })), h.exec, h.ctx)
    expect(h.records.map((r) => r.outcome)).toEqual(['failed', 'ok'])
    expect(h.records[0]?.error).toBe('selector not found')
    expect(h.calls).toContain('fail openActivity')
  })

  it('brackets every executed step with begin and end, so a persona is pre-empted (AC-12)', async () => {
    const h = harness({ fail: new Set([0]) })
    const list = steps([
      { actor: 'P1', at: 1, do: 'say', text: 'x' },
      { actor: 'P1', at: 2, do: 'say', text: 'y' },
    ])
    await runActor('P1', list.map((step, index) => ({ index, step })), h.exec, h.ctx)
    expect(h.calls).toEqual(['begin P1', 'exec say@1', 'fail say', 'end P1', 'begin P1', 'exec say@2', 'end P1'])
  })

  it('groups steps per actor keeping the scenario index', () => {
    const by = stepsByActor(
      steps([
        { actor: 'A', at: 1, do: 'say', text: '1' },
        { actor: 'B', at: 1, do: 'say', text: '2' },
        { actor: 'A', at: 2, do: 'say', text: '3' },
      ]),
    )
    expect(by.get('A')?.map((x) => x.index)).toEqual([0, 2])
    expect(by.get('B')?.map((x) => x.index)).toEqual([1])
  })
})
