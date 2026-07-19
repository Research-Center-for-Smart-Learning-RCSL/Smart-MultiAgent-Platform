import { describe, expect, it } from 'vitest'

import { GRAPHRAG_BUILD_STATES, GRAPHRAG_IN_PROGRESS } from '../api'
import { graphragBuildStateLabelKey, graphragBuildStateVariant } from '../lib/graphragBuildState'
import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

// Added with 2026-07-17-graphrag-reset-expired-recovery (AC-7/AC-11). The presentation
// maps are exhaustive Records, so a missing state is already a typecheck error; what
// these cover is the part the type system cannot see -- that every key actually resolves
// in every locale, and that the new terminal state is not treated as in-progress.

function resolve(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((acc, k) => (acc as Record<string, unknown>)?.[k], obj)
}

describe('graphrag build-state presentation', () => {
  it('maps every state to a label key that exists in both locales', () => {
    for (const state of GRAPHRAG_BUILD_STATES) {
      const key = graphragBuildStateLabelKey(state)
      expect(key, `${state} has no label key`).not.toBe(state)
      // The locale files carry the same `agents.` root the keys use.
      expect(resolve(en, key), `en is missing ${key}`).toBeTypeOf('string')
      expect(resolve(zhTW, key), `zh-TW is missing ${key}`).toBeTypeOf('string')
    }
  })

  it('gives every state a badge variant', () => {
    for (const state of GRAPHRAG_BUILD_STATES) {
      expect(['neutral', 'info', 'danger', 'warning']).toContain(graphragBuildStateVariant(state))
    }
  })

  it('treats recovery_unavailable as terminal so the UI offers a rebuild', () => {
    // A rebuild is the documented escape from an irrecoverable graph; the list views
    // derive the rebuild affordance from "not in progress".
    expect(GRAPHRAG_IN_PROGRESS.has('recovery_unavailable')).toBe(false)
    expect(graphragBuildStateVariant('recovery_unavailable')).toBe('danger')
  })

  it('has the withheld-graph strings in both locales', () => {
    // The graph view distinguishes "withheld because of build state" from "empty";
    // a key missing in one locale would silently render the key path to those users.
    for (const key of [
      'agents.graphragGraph.blockedTitle',
      'agents.graphragGraph.blockedDescription',
    ]) {
      expect(resolve(en, key), `en is missing ${key}`).toBeTypeOf('string')
      expect(resolve(zhTW, key), `zh-TW is missing ${key}`).toBeTypeOf('string')
    }
  })

  it('falls back rather than rendering blank for an unknown state', () => {
    expect(graphragBuildStateVariant('nonsense')).toBe('neutral')
    expect(graphragBuildStateLabelKey('nonsense')).toBe('nonsense')
  })
})
