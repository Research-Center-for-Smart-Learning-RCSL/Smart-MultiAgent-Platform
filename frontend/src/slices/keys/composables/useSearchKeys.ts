import { computed } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { errorMessage } from '@shared/errors'
import { keysKeys } from '../queries'
import {
  searchKeysApi,
  type SearchKey,
  type SearchProvider,
} from '../api/search-keys'

export function useSearchKeys(projectId: () => string) {
  const qc = useQueryClient()

  const { data, error: queryError, refetch } = useQuery({
    queryKey: computed(() => keysKeys.searchKeys(projectId())),
    queryFn: () => searchKeysApi.list(projectId()).then((r) => r.data),
    enabled: computed(() => !!projectId()),
  })

  const keys = computed<SearchKey[]>(() => data.value ?? [])
  const error = computed(() => queryError.value ? errorMessage(queryError.value) : null)

  async function reload(): Promise<void> {
    await refetch()
  }

  async function upload(
    provider: SearchProvider,
    secret: string,
    config: Record<string, unknown>,
  ): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await searchKeysApi.upload(pid, provider, secret, config)
    await qc.invalidateQueries({ queryKey: keysKeys.searchKeys(pid) })
  }

  async function retest(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await searchKeysApi.retest(pid, id)
    await qc.invalidateQueries({ queryKey: keysKeys.searchKeys(pid) })
  }

  async function activate(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await searchKeysApi.activate(pid, id)
    await qc.invalidateQueries({ queryKey: keysKeys.searchKeys(pid) })
  }

  async function remove(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await searchKeysApi.remove(pid, id)
    await qc.invalidateQueries({ queryKey: keysKeys.searchKeys(pid) })
  }

  return { keys, error, reload, upload, retest, activate, remove }
}
