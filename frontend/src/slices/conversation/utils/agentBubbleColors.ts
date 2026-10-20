export const AGENT_BUBBLE_COLORS = [
  'cyan', 'teal', 'green', 'lime', 'amber', 'orange', 'rose', 'violet',
] as const

export type AgentBubbleColor = (typeof AGENT_BUBBLE_COLORS)[number]

export function isAgentBubbleColor(value: unknown): value is AgentBubbleColor {
  return AGENT_BUBBLE_COLORS.some((key) => key === value)
}
