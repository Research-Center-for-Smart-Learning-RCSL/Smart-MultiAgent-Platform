import { describe, expect, it } from 'vitest'

import { computeGraphLayout } from '../composables/useGraphLayout'
import type { GraphEdge, GraphNode } from '../api'

function nodes(ids: string[]): GraphNode[] {
  return ids.map((id) => ({ id, degree: 0, build_id: null }))
}

describe('computeGraphLayout', () => {
  it('returns nothing for an empty graph', () => {
    expect(computeGraphLayout([], [])).toEqual([])
  })

  it('centers a single node', () => {
    const out = computeGraphLayout(nodes(['a']), [], { width: 1000, height: 600 })
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ id: 'a', x: 500, y: 300 })
  })

  it('places every node exactly once', () => {
    const out = computeGraphLayout(nodes(['a', 'b', 'c', 'd']), [
      { source: 'a', target: 'b', relation: 'r', confidence: 1 },
      { source: 'b', target: 'c', relation: 'r', confidence: 1 },
    ] as GraphEdge[])
    expect(out.map((p) => p.id).sort()).toEqual(['a', 'b', 'c', 'd'])
    for (const p of out) {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
    }
  })

  it('is deterministic for the same input', () => {
    const ns = nodes(['a', 'b', 'c'])
    const es = [{ source: 'a', target: 'b', relation: 'r', confidence: 1 }] as GraphEdge[]
    expect(computeGraphLayout(ns, es)).toEqual(computeGraphLayout(ns, es))
  })

  it('falls back to a circular layout above relaxMax without relaxing', () => {
    const ids = Array.from({ length: 10 }, (_, i) => `n${i}`)
    const out = computeGraphLayout(nodes(ids), [], { relaxMax: 5, width: 1000, height: 1000 })
    expect(out).toHaveLength(10)
    // Circular seed: every node sits on the same radius from the center.
    const radii = out.map((p) => Math.round(Math.hypot(p.x - 500, p.y - 500)))
    expect(new Set(radii).size).toBe(1)
  })
})
