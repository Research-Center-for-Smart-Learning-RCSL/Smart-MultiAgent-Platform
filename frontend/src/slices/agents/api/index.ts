import { http } from '@shared/transport'

export interface Agent {
  id: string
  project_id: string
  name: string
  model_provider: string
  model_name: string
  system_prompt: string
  temperature: number
  max_tokens: number
  rag_config_id: string | null
  mcp_server_ids: string[]
  version: number
  created_at: string
}

export interface RagConfig {
  id: string
  project_id: string
  name: string
  embedding_provider: string
  chunk_size: number
  chunk_overlap: number
  created_at: string
}

export const agentsApi = {
  list: (projectId: string) =>
    http.get<Agent[]>(`/projects/${projectId}/agents`),

  create: (projectId: string, payload: Omit<Agent, 'id' | 'project_id' | 'version' | 'created_at'>) =>
    http.post<Agent>(`/projects/${projectId}/agents`, payload),

  get: (agentId: string) =>
    http.get<Agent>(`/agents/${agentId}`),

  patch: (agentId: string, version: number, payload: Partial<Agent>) =>
    http.patch<Agent>(`/agents/${agentId}`, payload, {
      headers: { 'If-Match': String(version) },
    }),

  remove: (agentId: string) =>
    http.delete(`/agents/${agentId}`),

  listRagConfigs: (projectId: string) =>
    http.get<RagConfig[]>(`/projects/${projectId}/rag-configs`),

  createRagConfig: (projectId: string, payload: Omit<RagConfig, 'id' | 'project_id' | 'created_at'>) =>
    http.post<RagConfig>(`/projects/${projectId}/rag-configs`, payload),

  deleteRagConfig: (configId: string) =>
    http.delete(`/rag-configs/${configId}`),
}
