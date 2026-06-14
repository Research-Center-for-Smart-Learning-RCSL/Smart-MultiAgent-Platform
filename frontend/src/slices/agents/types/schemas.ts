import { z } from 'zod'

// Mirrors backend `AgentCreateIn` (backend/app/api/v1/agents.py). `model_hint`
// + `key_group_id` are the required pair the BYO-key runtime routes on; the
// former "model_provider/model_name/temperature/max_tokens" shape never
// existed on the backend and 422'd on every submit.
export const agentCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  model_hint: z.enum(['claude', 'openai', 'gemini']),
  key_group_id: z.string().uuid(),
  system_prompt: z.string().max(100_000).default(''),
  prompt_strategy: z.enum(['full', 'lazy']).default('full'),
  rag_config_id: z.string().uuid().nullable().default(null),
  graphrag_config_id: z.string().uuid().nullable().default(null),
  context_mode: z.enum(['general', 'compact']).default('general'),
  context_token_cap: z.number().int().positive().nullable().default(null),
  a2a_enabled: z.boolean().default(false),
})

export type AgentCreateInput = z.infer<typeof agentCreateSchema>

// Mirrors backend `RagConfigCreateIn`. The embedding key + provider + model
// are a required triple; reranking is opt-in and pulls its own key.
export const ragConfigCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  chunk_strategy: z.enum(['fixed', 'semantic']),
  chunk_params: z.record(z.unknown()).default({}),
  embed_key_id: z.string().uuid(),
  embed_provider: z.enum(['openai', 'gemini', 'voyage']),
  embed_model: z.string().trim().min(1),
  rerank_enabled: z.boolean().default(false),
  rerank_key_id: z.string().uuid().nullable().default(null),
  rerank_provider: z.enum(['cohere']).nullable().default(null),
  rerank_model: z.string().trim().min(1).nullable().default(null),
  top_k: z.number().int().positive().max(100).default(8),
})

export type RagConfigCreateInput = z.infer<typeof ragConfigCreateSchema>

// Mirrors backend `GraphRagConfigCreateIn`. 1:1 with an agent (R15.16);
// `builder_key_group_id` must differ from the agent's own key group (the
// backend rejects a match with GraphRagBuilderKeyGroupConflict).
export const graphragConfigCreateSchema = z.object({
  agent_id: z.string().uuid(),
  builder_key_group_id: z.string().uuid(),
  trigger_config: z.record(z.unknown()).default({}),
})

export type GraphragConfigCreateInput = z.infer<typeof graphragConfigCreateSchema>
