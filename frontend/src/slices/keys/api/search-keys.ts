import { SearchKeysService } from '@shared/api-client'

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

// Thin wrappers over the generated SearchKeysService (R24.13); each resolves the
// bare body (SearchKeyOut is assignable to SearchKey — provider/test_status enums
// match, config Record<string,any> fits Record<string,unknown>).
export const searchKeysApi = {
  list: (projectId: string): Promise<SearchKey[]> =>
    SearchKeysService.listSearchKeysApiProjectsProjectIdSearchKeysGet({ projectId }),
  upload: (
    projectId: string,
    provider: SearchProvider,
    secret: string,
    config: Record<string, unknown>,
  ): Promise<SearchKey> =>
    SearchKeysService.uploadSearchKeyApiProjectsProjectIdSearchKeysPost({
      projectId,
      requestBody: { provider, secret, config },
    }),
  retest: (projectId: string, id: string): Promise<SearchKey> =>
    SearchKeysService.retestSearchKeyApiProjectsProjectIdSearchKeysKeyIdRetestPost({
      projectId,
      keyId: id,
    }),
  activate: (projectId: string, id: string): Promise<void> =>
    SearchKeysService.activateSearchKeyApiProjectsProjectIdSearchKeysKeyIdActivatePost({
      projectId,
      keyId: id,
    }),
  remove: (projectId: string, id: string): Promise<void> =>
    SearchKeysService.deleteSearchKeyApiProjectsProjectIdSearchKeysKeyIdDelete({
      projectId,
      keyId: id,
    }),
}
