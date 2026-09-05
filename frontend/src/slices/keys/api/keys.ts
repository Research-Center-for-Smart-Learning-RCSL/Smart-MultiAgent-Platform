import { KeysService } from '@shared/api-client'

// Mirrors backend contexts.keys.domain.providers.ApiKeyProvider (R7.01).
export type ApiKeyProvider = 'claude' | 'openai' | 'gemini' | 'voyage' | 'cohere' | 'openai_compat'
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
  config?: Record<string, unknown>
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
  openai_compat: ['llm_chat', 'embedding'],
}

export interface OpenAICompatConfig {
  base_url: string
  label?: string
  timeout_s?: number
  capabilities?: ProviderCapability[]
}

// Thin wrappers over the generated KeysService (R24.13). Auth and problem+json
// error typing come from shared/transport/axios.ts's instrumentation of the bare
// axios singleton the generated client calls into; each method resolves the
// response body directly (KeyListOut/KeyOut are assignable to ApiKey).
export const keysApi = {
  list: (): Promise<ApiKey[]> => KeysService.listMyKeysApiKeysGet({}),
  get: (id: string): Promise<ApiKey> => KeysService.getMyKeyApiKeysKeyIdGet({ keyId: id }),
  upload: (
    provider: ApiKeyProvider,
    name: string,
    secret: string,
    config?: OpenAICompatConfig,
  ): Promise<ApiKey> =>
    KeysService.uploadKeyApiKeysPost({ requestBody: { provider, name, secret, config: config ?? null } }),
  retest: (id: string): Promise<ApiKey> =>
    KeysService.retestKeyApiKeysKeyIdRetestPost({ keyId: id }),
  remove: (id: string): Promise<void> => KeysService.deleteKeyApiKeysKeyIdDelete({ keyId: id }),
  projects: (id: string): Promise<KeyProject[]> =>
    KeysService.listKeyProjectsApiKeysKeyIdProjectsGet({ keyId: id }),
}
