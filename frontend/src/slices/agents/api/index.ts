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

// Mirrors backend `RagDocumentOut`.
export interface RagDocument {
  id: string
  rag_config_id: string
  filename: string
  mime: string
  size_bytes: number
  status: 'ingesting' | 'ready' | 'failed' | 'quarantined'
  scan_status: 'pending' | 'clean' | 'quarantined' | 'skipped'
  uploaded_at: string
}

// Files at or below this size go through the synchronous multipart endpoint;
// larger ones MUST use the resumable tus path (R22.15 / backend MAX_MULTIPART).
export const RAG_MULTIPART_MAX = 32 * 1024 * 1024

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

  getRagConfig: (configId: string) =>
    http.get<RagConfig>(`/rag-configs/${configId}`),

  listDocuments: (configId: string) =>
    http.get<RagDocument[]>(`/rag-configs/${configId}/documents`),

  // ≤ 32 MB synchronous path. Larger files use tusUpload(purpose:'rag_source')
  // from @shared/transport, which the backend routes to the ingest worker.
  uploadDocumentMultipart: (configId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    form.append('mime', file.type || 'application/octet-stream')
    return http.post<RagDocument>(`/rag-configs/${configId}/documents`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  deleteDocument: (documentId: string) =>
    http.delete(`/rag-documents/${documentId}`),
}
