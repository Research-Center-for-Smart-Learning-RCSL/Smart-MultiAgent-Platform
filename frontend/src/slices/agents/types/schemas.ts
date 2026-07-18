import { z } from 'zod'

import { INPUT_LIMITS } from '@shared/constants/inputLimits'

// Mirrors backend `AgentCreateIn` (backend/app/api/v1/agents.py). `model_hint`
// + `key_group_id` are the required pair the BYO-key runtime routes on; the
// former "model_provider/model_name/temperature/max_tokens" shape never
// existed on the backend and 422'd on every submit.
// Coerce an empty-string select / number input to null. SSelect emits '' for a
// "none" option and SInput type=number emits 0 when cleared; both must map to
// null for nullable id/cap fields, otherwise uuid()/positive() reject them and
// silently block submit.
const emptyToNull = (v: unknown): unknown =>
  typeof v === 'string' && v.trim() === '' ? null : v
const zeroOrEmptyToNull = (v: unknown): unknown =>
  v === '' || v === 0 ? null : v

export const agentCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  model_hint: z.enum(['claude', 'openai', 'gemini']),
  model_id: z.preprocess(
    emptyToNull,
    z.string().trim().max(200).nullable().default(null),
  ),
  // Cross-provider reasoning effort. null = provider default (param not sent).
  effort: z.preprocess(
    emptyToNull,
    z.enum(['low', 'medium', 'high']).nullable().default(null),
  ),
  key_group_id: z.string().uuid(),
  system_prompt: z.string().max(100_000).default(''),
  rag_config_id: z.preprocess(emptyToNull, z.string().uuid().nullable().default(null)),
  knowmap_config_id: z.preprocess(emptyToNull, z.string().uuid().nullable().default(null)),
  context_mode: z.enum(['general', 'compact']).default('general'),
  context_token_cap: z.preprocess(
    zeroOrEmptyToNull,
    z.number().int().positive().max(INPUT_LIMITS.CONTEXT_TOKEN_CAP).nullable().default(null),
  ),
  // Sampling controls for reproducible-enough-to-audit output. null = provider
  // default (param not sent). temperature=0 is a *meaningful* low-variance value,
  // so only '' — not 0 — maps to null here (unlike context_token_cap). seed is
  // OpenAI-only; it is a no-op on Anthropic/Gemini.
  temperature: z.preprocess(
    emptyToNull,
    z.number().min(0).max(2).nullable().default(null),
  ),
  top_p: z.preprocess(
    emptyToNull,
    z.number().min(0).max(1).nullable().default(null),
  ),
  // Bounded to the backend int4 `seed` column range so an oversized value is
  // flagged in the form rather than 500ing on the DB write.
  seed: z.preprocess(
    emptyToNull,
    z.number().int().min(-(2 ** 31)).max(2 ** 31 - 1).nullable().default(null),
  ),
  a2a_enabled: z.boolean().default(false),
  // Free-form JSON dicts assembled from the orchestration tab's decomposed
  // fields; kept in the schema so create/patch/duplicate share one payload type.
  wakeup_config: z.record(z.unknown()).default({}),
  workflow_capabilities: z.record(z.unknown()).default({}),
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
  // 'cohere' is BYO-key; 'bge' is the keyless bundled local reranker (F-19).
  rerank_provider: z.enum(['cohere', 'bge']).nullable().default(null),
  rerank_model: z.string().trim().min(1).nullable().default(null),
  top_k: z.number().int().positive().max(100).default(8),
})

export type RagConfigCreateInput = z.infer<typeof ragConfigCreateSchema>

// Mirrors backend `KnowmapConfigCreateIn` (Phase 3β). Unlike RAG, there is no
// embed key selection here — the embedding provider/model/dim are resolved
// server-side from `builder_key_group_id`, same as GraphRAG's builder group.
export const knowmapConfigCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  builder_key_group_id: z.string().uuid(),
  chunk_strategy: z.enum(['fixed', 'semantic']).default('fixed'),
  chunk_params: z.record(z.unknown()).default({}),
})

export type KnowmapConfigCreateInput = z.infer<typeof knowmapConfigCreateSchema>

// Mirrors backend `KnowmapConfigPatchIn` — `builder_key_group_id` and
// `chunk_params` are patchable; `chunk_strategy` is immutable post-creation
// (an indexed corpus can't switch chunking strategy), same as RAG.
export const knowmapConfigPatchSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  builder_key_group_id: z.string().uuid().optional(),
  chunk_params: z.record(z.unknown()).optional(),
})

export type KnowmapConfigPatchInput = z.infer<typeof knowmapConfigPatchSchema>

// The three Concept Map owner layers (Phase 4α). Mirrors backend `OwnerKind`.
export const CONCEPT_MAP_OWNER_KINDS = ['agent_group', 'chatroom', 'workspace'] as const
export type ConceptMapOwnerKind = (typeof CONCEPT_MAP_OWNER_KINDS)[number]

// Recency half-life bounds for the temporal control (R11.21). Backend requires
// > 0 and finite; the UI adds a sane 10-year ceiling so a typo can't persist an
// absurd decay window.
export const RECENCY_HALF_LIFE_MAX_DAYS = 3650

// Mirrors backend `GraphRagConfigCreateIn` (Phase 4α owner-centric). A Concept
// Map is created for a discriminated owner (agent_group | chatroom | workspace),
// not a single agent — coverage is by membership. `builder_key_group_id` is the
// key group used to extract triples and must differ from the owner's consumer
// key group (billing separation). `recency_half_life_days` (R11.21) is optional
// and positive; null inherits the platform default.
export const graphragConfigCreateSchema = z.object({
  owner_kind: z.enum(CONCEPT_MAP_OWNER_KINDS),
  owner_id: z.string().uuid(),
  builder_key_group_id: z.string().uuid(),
  trigger_config: z.record(z.unknown()).default({}),
  recency_half_life_days: z.preprocess(
    zeroOrEmptyToNull,
    z.number().positive().max(RECENCY_HALF_LIFE_MAX_DAYS).nullable().default(null),
  ),
})

export type GraphragConfigCreateInput = z.infer<typeof graphragConfigCreateSchema>

// Mirrors backend `GraphRagConfigPatchIn` — all fields optional (omitted =
// unchanged). Drives the WS4 temporal control and any builder/trigger edit.
export const graphragConfigPatchSchema = z.object({
  builder_key_group_id: z.string().uuid().optional(),
  trigger_config: z.record(z.unknown()).optional(),
  recency_half_life_days: z
    .preprocess(zeroOrEmptyToNull, z.number().positive().max(RECENCY_HALF_LIFE_MAX_DAYS).nullable())
    .optional(),
})

export type GraphragConfigPatchInput = z.infer<typeof graphragConfigPatchSchema>

// --- Unified Agent Tools (Phase B) ---

export const mcpToolCreateSchema = z.object({
  tool_type: z.literal('hosted_mcp'),
  display_name: z.string().trim().max(200).optional(),
  config: z.object({
    source: z.enum(['url', 'package']),
    reference: z.string().trim().min(1).max(2000),
    // Validated against the raw textarea in onSubmit (not bound to a vee-validate
    // field); an empty allowlist yields zero runtime tools and is rejected there.
    allowed_tools: z.array(z.string().trim().min(1)).max(200).default([]),
  }),
  auth: z.record(z.unknown()).optional(),
})

export const functionToolCreateSchema = z.object({
  tool_type: z.literal('local_function'),
  display_name: z.string().trim().max(200).optional(),
  config: z.object({
    name: z.string().trim().min(1).max(64).regex(/^[a-z0-9_]+$/),
    description: z.string().trim().min(1).max(1000),
    parameters: z
      .record(z.unknown())
      .default({ type: 'object', properties: {} })
      .refine((p) => p.type === 'object', 'Parameters must have type "object"'),
    http: z.object({
      method: z.enum(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']),
      url: z
        .string()
        .trim()
        .url()
        .min(1)
        .max(2000)
        .refine((u) => u.startsWith('https://'), 'Must use HTTPS'),
      headers: z.record(z.string()).default({}),
    }),
  }),
  auth: z
    .object({
      type: z.enum(['bearer', 'header']),
      token: z.string().optional(),
      name: z.string().optional(),
      value: z.string().optional(),
    })
    .optional(),
})

export type FunctionToolCreateInput = z.infer<typeof functionToolCreateSchema>

export const agentToolCreateSchema = z.discriminatedUnion('tool_type', [
  mcpToolCreateSchema,
  functionToolCreateSchema,
])

export type AgentToolCreateInput = z.infer<typeof agentToolCreateSchema>
