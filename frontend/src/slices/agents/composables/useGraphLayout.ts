// Deterministic force-directed layout for the GraphRAG knowledge-graph viewer.
// Kept as a pure function (no Vue, no randomness) so it is unit-testable and
// produces stable positions across renders. Nodes start on a circle and are
// relaxed with a few iterations of repulsion (all pairs) + attraction (along
// edges). For large graphs the O(n^2) relaxation is skipped — a plain circular
// layout is returned instead so the browser never janks.

import type { GraphEdge, GraphNode } from '../api'

export interface PositionedNode {
  id: string
  x: number
  y: number
}

export interface LayoutOptions {
  width?: number
  height?: number
  iterations?: number
  // Above this node count, skip relaxation and keep the circular layout.
  relaxMax?: number
}

const DEFAULTS = {
  width: 1200,
  height: 800,
  iterations: 120,
  relaxMax: 250,
}

export function computeGraphLayout(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  opts: LayoutOptions = {},
): PositionedNode[] {
  const width = opts.width ?? DEFAULTS.width
  const height = opts.height ?? DEFAULTS.height
  const iterations = opts.iterations ?? DEFAULTS.iterations
  const relaxMax = opts.relaxMax ?? DEFAULTS.relaxMax

  const n = nodes.length
  if (n === 0) return []

  const cx = width / 2
  const cy = height / 2
  const radius = Math.min(width, height) / 2 - 80

  // Circular seed — deterministic by index.
  const pos = nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n
    return { id: node.id, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }
  })

  if (n === 1) return [{ id: pos[0]!.id, x: cx, y: cy }]
  if (n > relaxMax) return pos

  const index = new Map(pos.map((p, i) => [p.id, i]))
  const k = Math.sqrt((width * height) / n) // ideal edge length
  const adjacency: Array<[number, number]> = []
  for (const e of edges) {
    const a = index.get(e.source)
    const b = index.get(e.target)
    if (a !== undefined && b !== undefined && a !== b) adjacency.push([a, b])
  }

  let temperature = width / 10
  const cooling = temperature / (iterations + 1)

  for (let step = 0; step < iterations; step++) {
    const disp = pos.map(() => ({ x: 0, y: 0 }))

    // Repulsion between every pair. Indices are in [0, n) so the elements are
    // always present; hoist them past noUncheckedIndexedAccess once per pair.
    for (let i = 0; i < n; i++) {
      const pi = pos[i]!
      const di = disp[i]!
      for (let j = i + 1; j < n; j++) {
        const pj = pos[j]!
        const dj = disp[j]!
        let dx = pi.x - pj.x
        let dy = pi.y - pj.y
        let dist = Math.hypot(dx, dy)
        if (dist < 0.01) {
          // Deterministic nudge so coincident nodes separate.
          dx = (i - j) * 0.01
          dy = (i + j) * 0.01
          dist = Math.hypot(dx, dy) || 0.01
        }
        const force = (k * k) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        di.x += fx
        di.y += fy
        dj.x -= fx
        dj.y -= fy
      }
    }

    // Attraction along edges. a/b are valid pos indices (built from index.get).
    for (const [a, b] of adjacency) {
      const pa = pos[a]!
      const pb = pos[b]!
      const da = disp[a]!
      const db = disp[b]!
      const dx = pa.x - pb.x
      const dy = pa.y - pb.y
      const dist = Math.hypot(dx, dy) || 0.01
      const force = (dist * dist) / k
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      da.x -= fx
      da.y -= fy
      db.x += fx
      db.y += fy
    }

    // Apply displacement capped by the cooling temperature.
    for (let i = 0; i < n; i++) {
      const di = disp[i]!
      const pi = pos[i]!
      const d = Math.hypot(di.x, di.y) || 0.01
      pi.x += (di.x / d) * Math.min(d, temperature)
      pi.y += (di.y / d) * Math.min(d, temperature)
    }
    temperature = Math.max(temperature - cooling, 1)
  }

  return pos
}
