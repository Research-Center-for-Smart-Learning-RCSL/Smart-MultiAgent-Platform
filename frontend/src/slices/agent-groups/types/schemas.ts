import { z } from 'zod'

// Mirrors backend `AgentGroupCreateIn` — name 1..200 chars.
export const agentGroupCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
})
export type AgentGroupCreateInput = z.infer<typeof agentGroupCreateSchema>

// Mirrors backend `AgentGroupUpdateIn` (rename).
export const agentGroupUpdateSchema = z.object({
  name: z.string().trim().min(1).max(200),
})
export type AgentGroupUpdateInput = z.infer<typeof agentGroupUpdateSchema>
