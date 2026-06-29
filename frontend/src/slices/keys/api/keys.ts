import { http } from '@shared/transport'

// Mirrors backend contexts.keys.domain.providers.ApiKeyProvider (R7.01).
export type ApiKeyProvider = 'claude' | 'openai' | 'gemini' | 'voyage' | 'cohere'
export type ProviderCapability = 'llm_chat' | 'embedding' | 'rerank'
export type TestStatus = 'ok' | 'failed' | 'untested'

export interface ApiKey {
  id: string
  provider: ApiKeyProvider
  name: string
  masked_preview: string
  test_status: TestStatus
  test_error: string | null
  last_test_at: string | null
  created_at: string
  // Number of projects this key is actively carried into. Present on the
  // my-keys list; 0 on the project-carried surface where it is not computed.
  project_count?: number
}

// Mirrors backend `KeyProjectOut` — one project this key is carried into,
// with its binding footprint (groups + agents that consume it there).
export interface KeyProject {
  project_id: string
  project_name: string
  carried_at: string
  group_count: number
  agent_count: number
}

// Authoritative table — must match R7.01. Views consult this to decide
// which capability chips to render next to each provider badge.
export const CAPABILITIES: Record<ApiKeyProvider, ProviderCapability[]> = {
  claude: ['llm_chat'],
  openai: ['llm_chat', 'embedding'],
  gemini: ['llm_chat', 'embedding'],
  voyage: ['embedding'],
  cohere: ['rerank'],
}

export const keysApi = {
  list: () => http.get<ApiKey[]>('/keys'),
  get: (id: string) => http.get<ApiKey>(`/keys/${id}`),
  upload: (provider: ApiKeyProvider, name: string, secret: string) =>
    http.post<ApiKey>('/keys', { provider, name, secret }),
  retest: (id: string) => http.post<ApiKey>(`/keys/${id}/retest`),
  remove: (id: string) => http.delete(`/keys/${id}`),
  projects: (id: string) => http.get<KeyProject[]>(`/keys/${id}/projects`),
}
