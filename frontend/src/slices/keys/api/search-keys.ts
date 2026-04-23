import { http } from '@shared/transport'

export type SearchProvider = 'brave' | 'serper' | 'tavily' | 'google_cse'

export interface SearchKey {
  id: string
  project_id: string
  provider: SearchProvider
  masked_preview: string
  test_status: 'ok' | 'failed' | 'untested'
  test_error: string | null
  last_test_at: string | null
  is_active: boolean
  config: Record<string, unknown>
  created_at: string
}

export const searchKeysApi = {
  list: (projectId: string) =>
    http.get<SearchKey[]>(`/projects/${projectId}/search-keys`),
  upload: (
    projectId: string,
    provider: SearchProvider,
    secret: string,
    config: Record<string, unknown>,
  ) =>
    http.post<SearchKey>(`/projects/${projectId}/search-keys`, {
      provider,
      secret,
      config,
    }),
  retest: (projectId: string, id: string) =>
    http.post<SearchKey>(`/projects/${projectId}/search-keys/${id}/retest`),
  activate: (projectId: string, id: string) =>
    http.post(`/projects/${projectId}/search-keys/${id}/activate`),
  remove: (projectId: string, id: string) =>
    http.delete(`/projects/${projectId}/search-keys/${id}`),
}
