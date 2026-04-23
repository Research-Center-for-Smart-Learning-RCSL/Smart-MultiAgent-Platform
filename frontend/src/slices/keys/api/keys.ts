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
  upload: (provider: ApiKeyProvider, name: string, secret: string) =>
    http.post<ApiKey>('/keys', { provider, name, secret }),
  retest: (id: string) => http.post<ApiKey>(`/keys/${id}/retest`),
  remove: (id: string) => http.delete(`/keys/${id}`),
}
