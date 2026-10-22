import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { ScenarioSchema, type Scenario, type Step } from './schema.ts'

export async function loadScenario(path: string): Promise<Scenario> {
  const mod = (await import(pathToFileURL(resolve(path)).href)) as { default?: unknown }
  if (!mod.default) throw new Error(`${path} has no default export`)
  return ScenarioSchema.parse(mod.default)
}

export function formatOffset(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
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
