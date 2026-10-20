import type { ChatroomAgentBubbleColor } from '@shared/api-client'

export type AgentBubbleColor = ChatroomAgentBubbleColor

// Keyed by the generated union, so a key the server adds or drops fails to
// compile here instead of drifting between the API and the picker. Insertion
// order is the order the picker shows.
const PALETTE: Record<AgentBubbleColor, true> = {
  cyan: true,
  teal: true,
  green: true,
  lime: true,
  amber: true,
  orange: true,
  rose: true,
  violet: true,
}

export const AGENT_BUBBLE_COLORS = Object.keys(PALETTE) as readonly AgentBubbleColor[]

export function isAgentBubbleColor(value: unknown): value is AgentBubbleColor {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(PALETTE, value)
}

/** Custom properties that point a bubble or swatch at one key's tokens in main.css. */
export function agentColorVars(color: AgentBubbleColor): Record<string, string> {
  return {
    '--agent-color': `var(--color-agent-${color})`,
    '--agent-color-tint': `var(--color-agent-${color}-tint)`,
  }
}
