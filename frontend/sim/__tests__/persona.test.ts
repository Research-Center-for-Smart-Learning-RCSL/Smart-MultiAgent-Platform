import { describe, expect, it } from 'vitest'
import { Budget, PersonaLoop, buildPrompts, type PersonaHost, type Timers } from '../llm/persona.ts'
import type { CompletionRequest, CompletionResult, Provider } from '../llm/provider.ts'
import { PersonaSchema, type Persona } from '../scenario/schema.ts'

function persona(over: Partial<Persona> = {}): Persona {
  return PersonaSchema.parse({ provider: 'anthropic', prompt: 'You are eager.', readDelaySec: [2, 2], minIntervalSec: 20, maxTurns: 3, ...over })
}

class StubProvider implements Provider {
  readonly name = 'anthropic' as const
  replies: string[] = []
  requests: CompletionRequest[] = []
  hold: Promise<void> | null = null
  async complete(req: CompletionRequest): Promise<CompletionResult> {
    this.requests.push(req)
    if (this.hold) await this.hold
    return { text: this.replies.shift() ?? '{"action":"silent"}', inputTokens: 100, outputTokens: 10 }
  }
}

function fakeTimers() {
  const pending: Array<{ fn: () => void; ms: number; id: number }> = []
  let id = 0
  const t: Timers & { now_: number; pending: typeof pending; fire: () => void } = {
    now_: 0,
    pending,
    now: () => t.now_,
    setTimeout: (fn, ms) => {
      pending.push({ fn, ms, id: ++id })
      return id
    },
    clearTimeout: (h) => {
      const i = pending.findIndex((p) => p.id === h)
      if (i >= 0) pending.splice(i, 1)
    },
    random: () => 0,
    fire: () => {
      const p = pending.shift()
      if (!p) throw new Error('nothing scheduled')
      t.now_ += p.ms
      p.fn()
    },
  }
  return t
}

function host(over: Partial<PersonaHost> = {}) {
  const acted: string[] = []
  const logs: Array<[string, Record<string, unknown>]> = []
  const h: PersonaHost = {
    selfName: '小安',
    transcript: () => ['[10:00:00] 老師: 大家好'],
    isBusy: () => false,
    act: async (d) => {
      acted.push(`${d.action}:${d.text}`)
    },
    log: (kind, fields) => logs.push([kind, fields]),
    ...over,
  }
  return { h, acted, logs }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('PersonaLoop (AC-10, AC-12)', () => {
  it('replies through its host after the reading delay', async () => {
    const p = new StubProvider()
    p.replies.push('{"action":"say","text":"我覺得是心"}')
    const timers = fakeTimers()
    const { h, acted } = host()
    const loop = new PersonaLoop(persona(), p, 'm', new Budget(10, 10_000), h, timers)
    loop.onRoomMessage(false)
    expect(timers.pending[0]?.ms).toBe(2000)
    timers.fire()
    await flush()
    expect(acted).toEqual(['say:我覺得是心'])
    expect(p.requests[0]?.system).toContain('小安')
  })

  it('ignores its own messages', () => {
    const timers = fakeTimers()
    const loop = new PersonaLoop(persona(), new StubProvider(), 'm', new Budget(10, 10_000), host().h, timers)
    loop.onRoomMessage(true)
    expect(timers.pending).toHaveLength(0)
  })

  it('may choose silence, and a malformed reply counts as silence', async () => {
    const p = new StubProvider()
    p.replies.push('{"action":"silent"}', 'I would say hello')
    const timers = fakeTimers()
    const { h, acted } = host()
    const loop = new PersonaLoop(persona(), p, 'm', new Budget(10, 10_000), h, timers)
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    expect(p.requests).toHaveLength(2)
    expect(acted).toEqual([])
  })

  it('waits out minIntervalSec after speaking', async () => {
    const p = new StubProvider()
    p.replies.push('{"action":"say","text":"一"}')
    const timers = fakeTimers()
    const loop = new PersonaLoop(persona(), p, 'm', new Budget(10, 10_000), host().h, timers)
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    timers.now_ += 5000
    loop.onRoomMessage(false)
    // Spoke at t=2s, now t=7s: 15s of the 20s interval remain, more than the 2s reading delay.
    expect(timers.pending[0]?.ms).toBe(15_000)
  })

  it('folds messages that arrive while a decision is scheduled into that decision', () => {
    const timers = fakeTimers()
    const loop = new PersonaLoop(persona(), new StubProvider(), 'm', new Budget(10, 10_000), host().h, timers)
    loop.onRoomMessage(false)
    loop.onRoomMessage(false)
    loop.onRoomMessage(false)
    expect(timers.pending).toHaveLength(1)
  })

  it('stops at maxTurns and logs exhaustion once', async () => {
    const p = new StubProvider()
    p.replies.push('{"action":"say","text":"a"}', '{"action":"say","text":"b"}', '{"action":"say","text":"c"}')
    const timers = fakeTimers()
    const { h, acted, logs } = host()
    const loop = new PersonaLoop(persona({ maxTurns: 1, minIntervalSec: 0 }), p, 'm', new Budget(10, 10_000), h, timers)
    for (let i = 0; i < 3; i++) {
      loop.onRoomMessage(false)
      timers.fire()
      await flush()
    }
    expect(acted).toEqual(['say:a'])
    expect(p.requests).toHaveLength(1)
    expect(logs.filter(([k]) => k === 'persona.exhausted')).toHaveLength(1)
  })

  it('stops when the run-wide call budget is spent, across personas', async () => {
    const budget = new Budget(1, 10_000)
    const p = new StubProvider()
    const timers = fakeTimers()
    const a = new PersonaLoop(persona(), p, 'm', budget, host().h, timers)
    const b = new PersonaLoop(persona(), p, 'm', budget, host().h, timers)
    a.onRoomMessage(false)
    b.onRoomMessage(false)
    timers.fire()
    await flush()
    timers.fire()
    await flush()
    expect(p.requests).toHaveLength(1)
    expect(budget.calls).toBe(1)
  })

  it('stops when the output-token budget is spent', async () => {
    const budget = new Budget(100, 10)
    const p = new StubProvider()
    const timers = fakeTimers()
    const loop = new PersonaLoop(persona({ minIntervalSec: 0 }), p, 'm', budget, host().h, timers)
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    expect(p.requests).toHaveLength(1)
    expect(budget.outputTokens).toBe(10)
  })

  it('does not decide while a script step owns the actor', async () => {
    const p = new StubProvider()
    const timers = fakeTimers()
    const loop = new PersonaLoop(persona(), p, 'm', new Budget(10, 10_000), host({ isBusy: () => true }).h, timers)
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    expect(p.requests).toHaveLength(0)
  })

  it('cancel drops a scheduled decision and aborts one in flight; the persona resumes after (AC-12)', async () => {
    const p = new StubProvider()
    let release!: () => void
    p.hold = new Promise((r) => (release = r))
    p.replies.push('{"action":"say","text":"too late"}', '{"action":"say","text":"resumed"}')
    const timers = fakeTimers()
    const { h, acted } = host()
    const loop = new PersonaLoop(persona(), p, 'm', new Budget(10, 10_000), h, timers)

    loop.onRoomMessage(false)
    loop.cancel()
    expect(timers.pending).toHaveLength(0)

    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    expect(p.requests).toHaveLength(1)
    const signal = p.requests[0]?.signal
    loop.cancel()
    expect(signal?.aborted).toBe(true)
    release()
    await flush()
    expect(acted).toEqual([])

    p.hold = null
    loop.onRoomMessage(false)
    timers.fire()
    await flush()
    expect(acted).toEqual(['say:resumed'])
  })

  it('speaks into a quiet room only when speakOnQuietSec is set', () => {
    const timers = fakeTimers()
    const quiet = new PersonaLoop(persona({ speakOnQuietSec: 60 }), new StubProvider(), 'm', new Budget(10, 10_000), host().h, timers)
    timers.now_ = 59_000
    quiet.onQuietCheck(0)
    expect(timers.pending).toHaveLength(0)
    timers.now_ = 61_000
    quiet.onQuietCheck(0)
    expect(timers.pending).toHaveLength(1)

    const t2 = fakeTimers()
    const never = new PersonaLoop(persona(), new StubProvider(), 'm', new Budget(10, 10_000), host().h, t2)
    t2.now_ = 600_000
    never.onQuietCheck(0)
    expect(t2.pending).toHaveLength(0)
  })

  it('builds prompts that carry the persona, language and reply contract', () => {
    const { system, user } = buildPrompts(persona({ maxChars: 60 }), '小博', ['[10:00:00] 老師: 開始'])
    expect(system).toContain('小博')
    expect(system).toContain('zh-TW')
    expect(system).toContain('You are eager.')
    expect(system).toContain('under 60 characters')
    expect(user).toContain('老師: 開始')
  })
})
