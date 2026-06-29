import { describe, it, expect } from 'vitest'
import { computeGraphLayout } from '../useGraphLayout'
import type { GraphNode, GraphEdge } from '../../api'

function node(id: string): GraphNode {
  return { id, degree: 1, build_id: null, type: '' }
}
function edge(source: string, target: string): GraphEdge {
  return { source, relation: 'rel', target, confidence: 1 }
}

describe('computeGraphLayout', () => {
  it('returns an empty array for no nodes', () => {
    expect(computeGraphLayout([], [])).toEqual([])
  })

  it('centres a single node', () => {
    const out = computeGraphLayout([node('a')], [], { width: 1000, height: 600 })
    expect(out).toEqual([{ id: 'a', x: 500, y: 300 }])
  })

  it('positions every node with finite coordinates', () => {
    const nodes = ['a', 'b', 'c', 'd'].map(node)
    const edges = [edge('a', 'b'), edge('b', 'c'), edge('c', 'd')]
    const out = computeGraphLayout(nodes, edges, { iterations: 20 })
    expect(out.map((p) => p.id).sort()).toEqual(['a', 'b', 'c', 'd'])
    for (const p of out) {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
    }
  })

  it('skips relaxation above relaxMax but keeps every node', () => {
    const nodes = Array.from({ length: 12 }, (_, i) => node(`n${i}`))
    const out = computeGraphLayout(nodes, [], { relaxMax: 5 })
    expect(out).toHaveLength(12)
  })

  it('is deterministic for the same input', () => {
    const nodes = ['a', 'b', 'c'].map(node)
    const edges = [edge('a', 'b')]
    const a = computeGraphLayout(nodes, edges, { iterations: 30 })
    const b = computeGraphLayout(nodes, edges, { iterations: 30 })
    expect(a).toEqual(b)
  })

  it('keeps coordinates finite when seed points are antipodal', () => {
    const out = computeGraphLayout([node('a'), node('b')], [edge('a', 'b')], { iterations: 50 })
    for (const p of out) {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
    }
  })
})
