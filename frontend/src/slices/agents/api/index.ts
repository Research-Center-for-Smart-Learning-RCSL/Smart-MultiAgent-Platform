import { http } from '@shared/transport'
import type { AgentCreateInput, RagConfigCreateInput } from '../types/schemas'

// Mirrors backend `AgentOut` (backend/app/api/v1/agents.py). `model_hint`
// selects the provider family; the concrete model + key rotation come from the
// bound `key_group_id`.
export interface Agent {
  id: string
  project_id: string
  name: string
  model_hint: string
  key_group_id: string
  system_prompt: string
  prompt_strategy: string
  rag_config_id: string | null
  graphrag_config_id: string | null
  context_mode: string
  context_token_cap: number | null
  a2a_enabled: boolean
  wakeup_config: Record<string, unknown>
  workflow_capabilities: Record<string, unknown>
  version: number
  created_at: string
  deleted_at: string | null
}

// Mirrors backend `RagConfigOut`.
export interface RagConfig {
  id: string
  project_id: string
  name: string
  chunk_strategy: string
  chunk_params: Record<string, unknown>
  embed_key_id: string | null
  embed_provider: string
  embed_model: string
  rerank_enabled: boolean
  rerank_key_id: string | null
  rerank_provider: string | null
  rerank_model: string | null
  top_k: number
  created_at: string
}

export const agentsApi = {
  list: (projectId: string) =>
    http.get<Agent[]>(`/projects/${projectId}/agents`),

  create: (projectId: string, payload: AgentCreateInput) =>
    http.post<Agent>(`/projects/${projectId}/agents`, payload),

  get: (agentId: string) =>
    http.get<Agent>(`/agents/${agentId}`),

  patch: (agentId: string, version: number, payload: Partial<AgentCreateInput>) =>
    http.patch<Agent>(`/agents/${agentId}`, payload, {
      headers: { 'If-Match': String(version) },
    }),

  remove: (agentId: string) =>
    http.delete(`/agents/${agentId}`),

  listRagConfigs: (projectId: string) =>
    http.get<RagConfig[]>(`/projects/${projectId}/rag-configs`),

  createRagConfig: (projectId: string, payload: RagConfigCreateInput) =>
    http.post<RagConfig>(`/projects/${projectId}/rag-configs`, payload),

  deleteRagConfig: (configId: string) =>
    http.delete(`/rag-configs/${configId}`),
}
