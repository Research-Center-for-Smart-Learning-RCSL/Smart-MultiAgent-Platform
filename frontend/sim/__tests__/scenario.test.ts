import { describe, expect, it } from 'vitest'
import { ScenarioSchema, type ScenarioInput } from '../scenario/schema.ts'
import { scheduleLines } from '../scenario/load.ts'
import { applyFacilitatorOverride } from '../runner.ts'
import smoke from '../scenarios/smoke-local.ts'
import ac20 from '../scenarios/crat-ac20.ts'
import llmClass from '../scenarios/crat-llm-class.ts'

const minimal: ScenarioInput = {
  name: 'x',
  durationSec: 60,
  room: { mode: 'create', name: 'r' },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'S1', role: 'student', account: 1 },
    { name: 'G', role: 'guest', displayName: 'Amy' },
  ],
  steps: [],
}

function issues(input: ScenarioInput): string[] {
  const r = ScenarioSchema.safeParse(input)
  return r.success ? [] : r.error.issues.map((i) => i.message)
}

describe('shipped scenarios', () => {
  it.each([
    ['smoke-local', smoke],
    ['crat-ac20', ac20],
    ['crat-llm-class', llmClass],
  ])('%s parses', (_name, s) => {
    expect(issues(s)).toEqual([])
  })

  it('smoke-local exercises every scripted action the browser performs (AC-9)', () => {
    const used = new Set(ScenarioSchema.parse(smoke).steps.map((s) => s.do))
    for (const a of ['connect', 'leave', 'say', 'typePause', 'silent', 'openActivity', 'submit', 'propose', 'vote', 'withdraw', 'startActivity', 'endActivity']) {
      expect(used, a).toContain(a)
    }
  })

  it('crat-ac20 declares the protocol preconditions (AC-16)', () => {
    const s = ScenarioSchema.parse(ac20)
    expect(s.memberGroup?.members).toEqual(['P1', 'P2', 'P3'])
    const p1 = s.actors.find((a) => a.name === 'P1')
    const p4 = s.actors.find((a) => a.name === 'P4')
    expect(p4?.role).toBe('guest')
    expect(p4?.displayName).toBe(p1?.displayName)
    expect(s.room.agents.map((a) => [a.name.slice(0, 2), a.role, a.grantActivityTypes])).toEqual([
      ['TA', 'normal', ['crat-word-puzzle']],
      ['SA', 'normal', []],
      ['AA', 'observer', []],
    ])
    expect(s.room.mode === 'create' && s.room.guestLinks).toBe(true)
    const lines = scheduleLines(s)
    expect(lines[0]).toMatch(/^00:05 {2}F/)
    expect(lines.some((l) => l.startsWith('05:00  P1'))).toBe(true)
    expect(lines.some((l) => l.startsWith('16:00  F'))).toBe(true)
    expect(lines.some((l) => l.startsWith('20:00  F') && l.includes('observation'))).toBe(true)
  })
})

describe('scenario validation', () => {
  it('accepts "m:ss" offsets', () => {
    const s = ScenarioSchema.parse({ ...minimal, steps: [{ actor: 'S1', at: '12:05', do: 'say', text: 'x' }] })
    expect(s.steps[0]?.at).toBe(725)
  })

  it('requires exactly one facilitator', () => {
    expect(issues({ ...minimal, actors: [...minimal.actors, { name: 'F2', role: 'facilitator' }] })).toContain('exactly one facilitator actor is required')
  })

  it('refuses group actions for a guest and facilitator actions for a student', () => {
    expect(issues({ ...minimal, steps: [{ actor: 'G', at: 1, do: 'vote', approve: true }] })[0]).toMatch(/registered student/)
    expect(issues({ ...minimal, steps: [{ actor: 'S1', at: 1, do: 'startActivity', type: 'x' }] })[0]).toMatch(/facilitator action/)
  })

  it('refuses unknown actors, steps without timing, and duplicate accounts', () => {
    expect(issues({ ...minimal, steps: [{ actor: 'Z', at: 1, do: 'leave' }] })[0]).toMatch(/unknown actor/)
    expect(issues({ ...minimal, steps: [{ actor: 'S1', do: 'leave' }] })[0]).toMatch(/needs at, when/)
    expect(issues({ ...minimal, actors: [...minimal.actors, { name: 'S2', role: 'student', account: 1 }] })).toContain('account numbers must be unique')
  })

  it('requires a persona for llm and hybrid actors', () => {
    expect(issues({ ...minimal, actors: [minimal.actors[0]!, { name: 'S1', role: 'student', account: 1, drive: 'llm' }] })[0]).toMatch(/needs a persona/)
  })
})

describe('--facilitator human', () => {
  it('turns the facilitator into a human and its steps into cues', () => {
    const s = ScenarioSchema.parse(ac20)
    const { scenario, cues } = applyFacilitatorOverride(s, true)
    expect(scenario.actors.find((a) => a.role === 'facilitator')?.drive).toBe('human')
    expect(scenario.steps.some((x) => x.actor === 'F')).toBe(false)
    expect(cues.length).toBe(s.steps.filter((x) => x.actor === 'F').length)
    expect(applyFacilitatorOverride(s, false).scenario).toBe(s)
  })
})
