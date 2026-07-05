// Single source of truth for the SMAP constellation glyph geometry, shared by
// the static hero (AgentConstellation) and the entry overlay (LandingIntro) so
// the two renders of the same logo cannot drift apart.

export const CENTER = { x: 200, y: 200 }
export const RADIUS = 150

// Heterogeneous ring: the varied radii carry the "multi-LLM, mixed providers"
// story.
const NODES = [
  { deg: -90, r: 12 },
  { deg: -30, r: 8 },
  { deg: 30, r: 10 },
  { deg: 90, r: 9 },
  { deg: 150, r: 8 },
  { deg: 210, r: 11 },
]

export interface ConstellationNode {
  id: number
  x: number
  y: number
  r: number
  // Distance from the centre to this node (edge length).
  len: number
  // Radial unit vector, for callers that offset along the spoke (fly-in).
  cos: number
  sin: number
}

export const SATELLITES: ConstellationNode[] = NODES.map((n, i) => {
  const rad = (n.deg * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  const x = Math.round(CENTER.x + RADIUS * cos)
  const y = Math.round(CENTER.y + RADIUS * sin)
  return { id: i, x, y, r: n.r, len: Math.round(Math.hypot(x - CENTER.x, y - CENTER.y)), cos, sin }
})

// Every ball in the glyph: the ring satellites plus the central hub.
export const BALL_COUNT = SATELLITES.length + 1

// Edges are gently bowed quadratic curves rather than straight spokes — more
// organic, and both renders of the glyph must share the exact same curve so
// the intro dock stays pixel-identical. The bow is a perpendicular offset at
// the midpoint, alternating sides so the figure stays balanced.
const EDGE_BOW = 16

export function edgePath(node: ConstellationNode): string {
  const sign = node.id % 2 === 0 ? 1 : -1
  const cx = Math.round((CENTER.x + node.x) / 2 - node.sin * EDGE_BOW * sign)
  const cy = Math.round((CENTER.y + node.y) / 2 + node.cos * EDGE_BOW * sign)
  return `M ${CENTER.x} ${CENTER.y} Q ${cx} ${cy} ${node.x} ${node.y}`
}
