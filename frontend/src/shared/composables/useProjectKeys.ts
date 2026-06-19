// Shared composable wrapping the key-group and project-key listing API.
// This decouples slices (agents, conversation) from @slices/keys (H27).
//
// NOTE: the underlying HTTP helpers and types are re-exported here so
// consumers only need a single import path.

import { http } from '@shared/transport'

// ---------------------------------------------------------------------------
// Types (mirrored from keys slice — kept in sync by contract)
// ---------------------------------------------------------------------------

export type ApiKeyProvider = 'claude' | 'openai' | 'gemini' | 'voyage' | 'cohere'
export type ProviderCapability = 'llm_chat' | 'embedding' | 'rerank'

export interface ApiKey {
  id: string
  provider: ApiKeyProvider
  name: string
  masked_preview: string
  test_status: 'ok' | 'failed' | 'untested'
  test_error: string | null
  last_test_at: string | null
  created_at: string
}

export interface KeyGroup {
  id: string
  project_id: string
  name: string
  created_at: string
}

// ---------------------------------------------------------------------------
// Capability table (R7.01)
// ---------------------------------------------------------------------------

export const CAPABILITIES: Record<ApiKeyProvider, ProviderCapability[]> = {
  claude: ['llm_chat'],
  openai: ['llm_chat', 'embedding'],
  gemini: ['llm_chat', 'embedding'],
  voyage: ['embedding'],
  cohere: ['rerank'],
}

// ---------------------------------------------------------------------------
// Query-key factory (shared across slices for cache coherency)
// ---------------------------------------------------------------------------

export const keysKeys = {
  myKeys: () => ['keys', 'myKeys'] as const,
  keyGroups: (projectId: string) =>
    ['keys', 'keyGroups', projectId] as const,
  keyGroup: (groupId: string) =>
    ['keys', 'keyGroup', groupId] as const,
  projectKeys: (projectId: string) =>
    ['keys', 'projectKeys', projectId] as const,
  searchKeys: (projectId: string) =>
    ['keys', 'searchKeys', projectId] as const,
}

// ---------------------------------------------------------------------------
// API wrappers
// ---------------------------------------------------------------------------

export const keyGroupsApi = {
  listForProject: (projectId: string) =>
    http.get<KeyGroup[]>(`/projects/${projectId}/key-groups`),
}

export const projectKeysApi = {
  listCarried: (projectId: string) =>
    http.get<ApiKey[]>(`/projects/${projectId}/keys`),
}
