import { describe, expect, it } from 'vitest'
import { ScenarioSchema, type ScenarioInput } from '../scenario/schema.ts'
import { applyFacilitatorOverride, scheduleLines } from '../scenario/load.ts'
import smoke from '../scenarios/smoke-local.ts'
import ac20 from '../scenarios/crat-ac20.ts'
import llmClass from '../scenarios/crat-llm-class.ts'
import charAc21 from '../scenarios/charbuild-ac21.ts'
import objAc21 from '../scenarios/objreinterp-ac21.ts'
import charAuto from '../scenarios/charbuild-autonomous.ts'
import objAuto from '../scenarios/objreinterp-autonomous.ts'
import cratAuto from '../scenarios/crat-autonomous.ts'

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
    ['charbuild-ac21', charAc21],
    ['objreinterp-ac21', objAc21],
    ['charbuild-autonomous', charAuto],
    ['objreinterp-autonomous', objAuto],
    ['crat-autonomous', cratAuto],
  ])('%s parses', (_name, s) => {
    expect(issues(s)).toEqual([])
  })

  it('the phase 4 runs configure the class flow and leave the facilitator out (AC-11)', () => {
    const char = ScenarioSchema.parse(charAuto)
    const obj = ScenarioSchema.parse(objAuto)
    const crat = ScenarioSchema.parse(cratAuto)
    const fSteps = (s: typeof char) => s.steps.filter((x) => x.actor === 'F')
    expect(fSteps(char)).toEqual([])
    expect(fSteps(crat)).toEqual([])
    // The object run's only facilitator message is the round-3 override.
    expect(fSteps(obj).filter((x) => x.do === 'say')).toHaveLength(1)
    for (const s of [char, obj, crat]) {
      expect(s.room.classStart).toBeDefined()
      expect(s.steps.some((x) => x.do === 'startActivity' || x.do === 'endActivity')).toBe(false)
      expect(s.room.agents.find((a) => a.name.startsWith('TA'))?.grantActivityTypes).toHaveLength(1)
    }
    expect(char.room.timeLimits).toEqual({ 'character-building': 3 })
    expect(obj.room.timeLimits).toEqual({ 'object-reinterpretation': 3 })
    // One CRAT activation spans every round, so a limit would end the whole class.
    expect(crat.room.timeLimits).toEqual({})
  })

  it('the character run leaves P4 without a submission in round 1 only', () => {
    const s = ScenarioSchema.parse(charAuto)
    const p4 = s.steps.filter((x) => x.actor === 'P4' && x.do === 'submit').map((x) => x.label)
    expect(p4).toEqual([2, 3, 4, 5, 6].map((r) => `submits in round ${r}`))
  })

  it('a time limit accepts null as an explicit no-limit and rejects out-of-range minutes', () => {
    expect(issues({ ...minimal, room: { mode: 'create', name: 'r', timeLimits: { a: null, b: 180 } } })).toEqual([])
    expect(issues({ ...minimal, room: { mode: 'create', name: 'r', timeLimits: { a: 0 } } })).not.toEqual([])
    expect(issues({ ...minimal, room: { mode: 'create', name: 'r', classStart: { minParticipants: 0, maxWaitMinutes: 1 } } })).not.toEqual([])
  })

  it('smoke-local exercises every scripted action the browser performs (AC-9)', () => {
    const used = new Set(ScenarioSchema.parse(smoke).steps.map((s) => s.do))
    for (const a of ['connect', 'leave', 'say', 'typePause', 'silent', 'openActivity', 'submit', 'propose', 'vote', 'withdraw', 'startActivity', 'endActivity']) {
      expect(used, a).toContain(a)
    }
  })

  it('the AC-21 runs cover a teacher without a grant and one with it, plus a panel end', () => {
    const char = ScenarioSchema.parse(charAc21)
    const obj = ScenarioSchema.parse(objAc21)
    const teacher = (s: typeof char) => s.room.agents.find((a) => a.name.startsWith('TA'))
    expect(teacher(char)?.grantActivityTypes).toEqual([])
    expect(char.steps.some((s) => s.do === 'startActivity' && s.actor === 'F')).toBe(true)
    expect(teacher(obj)?.grantActivityTypes).toEqual(['object-reinterpretation'])
    expect(obj.steps.filter((s) => s.do === 'startActivity')).toEqual([])
    // The panel end follows TA's round-2 work announcement, and the reactions follow round 2's end
    // notice (the second one), not round 1's.
    expect(obj.steps.some((s) => s.do === 'endActivity' && s.when?.type === 'message' && s.when.from === 'agent:TA')).toBe(true)
    expect(obj.steps.some((s) => s.when?.type === 'message' && s.when.from === 'system' && s.when.count === 2)).toBe(true)
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
    // Phase 2 D-22: the release waits for AA's first observation, at about minute 25.
    expect(lines.some((l) => l.startsWith('27:30  F') && l.includes('observation'))).toBe(true)
  })
})

describe('scenario validation', () => {
  it('accepts "m:ss" offsets', () => {
    const s = ScenarioSchema.parse({ ...minimal, steps: [{ actor: 'S1', at: '12:05', do: 'say', text: 'x' }] })
    expect(s.steps[0]?.at).toBe(725)
  })

  it('rejects an invalid message pattern at load time rather than mid-run', () => {
    const bad = { ...minimal, steps: [{ actor: 'F', at: 1, when: { type: 'message' as const, match: '老師(' }, do: 'leave' as const }] }
    expect(issues(bad)).toContain('match is not a valid regular expression')
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
