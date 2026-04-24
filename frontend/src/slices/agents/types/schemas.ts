import { z } from 'zod'

export const agentCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  model_provider: z.string().trim().min(1),
  model_name: z.string().trim().min(1),
  system_prompt: z.string().max(32_000).default(''),
  temperature: z.number().min(0).max(2).default(0.7),
  max_tokens: z.number().int().min(1).max(128_000).default(4096),
  rag_config_id: z.string().nullable().default(null),
  mcp_server_ids: z.array(z.string()).default([]),
})

export type AgentCreateInput = z.infer<typeof agentCreateSchema>

export const ragConfigCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  embedding_provider: z.string().trim().min(1),
  chunk_size: z.number().int().min(64).max(8192).default(512),
  chunk_overlap: z.number().int().min(0).max(4096).default(64),
})

export type RagConfigCreateInput = z.infer<typeof ragConfigCreateSchema>
