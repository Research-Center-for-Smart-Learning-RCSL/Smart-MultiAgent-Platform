import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { formatOffset } from '../lib/time.ts'
import { ScenarioSchema, type Actor, type Scenario, type Step } from './schema.ts'

/** Imports and runs the file: a scenario is trusted operator code, like a test spec. */
export async function loadScenario(path: string): Promise<Scenario> {
  const mod = (await import(pathToFileURL(resolve(path)).href)) as { default?: unknown }
  if (!mod.default) throw new Error(`${path} has no default export`)
  return ScenarioSchema.parse(mod.default)
}

/** The schema guarantees exactly one. */
export function facilitatorOf(scenario: Scenario): Actor {
  return scenario.actors.find((a) => a.role === 'facilitator') as Actor
}

/** Applies `--facilitator human`: the facilitator's steps become printed cues for the operator. */
export function applyFacilitatorOverride(scenario: Scenario, human: boolean): { scenario: Scenario; cues: Step[] } {
  if (!human) return { scenario, cues: [] }
  const fac = facilitatorOf(scenario)
  return {
    scenario: {
      ...scenario,
      actors: scenario.actors.map((a) => (a === fac ? { ...a, drive: 'human' as const } : a)),
      steps: scenario.steps.filter((s) => s.actor !== fac.name),
    },
    cues: scenario.steps.filter((s) => s.actor === fac.name),
  }
}

export function describeStep(step: Step): string {
  const { actor, at, when, label, timeoutSec: _t, onTimeout: _o, ...action } = step
  const time = at === undefined ? '--:--' : formatOffset(at)
  const cond = when ? ` when ${JSON.stringify(when)}` : ''
  const what = label ?? JSON.stringify(action)
  return `${time}  ${actor.padEnd(12)} ${what}${cond}`
}

/** The schedule `dry-run` prints: steps ordered by planned time, conditional-only ones last. */
export function scheduleLines(scenario: Scenario): string[] {
  const ordered = [...scenario.steps].sort((a, b) => (a.at ?? Number.MAX_SAFE_INTEGER) - (b.at ?? Number.MAX_SAFE_INTEGER))
  return ordered.map(describeStep)
}
