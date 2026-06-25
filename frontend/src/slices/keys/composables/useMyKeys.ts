import { computed } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { errorMessage } from '@shared/errors'
import { keysKeys } from '../queries'
import { keysApi, type ApiKey, type ApiKeyProvider } from '../api/keys'

export function useMyKeys() {
  const qc = useQueryClient()

  const { data, isLoading: loading, error: queryError, refetch } = useQuery({
    queryKey: keysKeys.myKeys(),
    queryFn: () => keysApi.list().then((r) => r.data),
  })

  const keys = computed<ApiKey[]>(() => data.value ?? [])
  const error = computed(() => queryError.value ? errorMessage(queryError.value) : null)

  async function reload(): Promise<void> {
    await refetch()
  }

  async function upload(
    provider: ApiKeyProvider,
    name: string,
    secret: string,
  ): Promise<ApiKey | null> {
    const { data: created } = await keysApi.upload(provider, name, secret)
    await qc.invalidateQueries({ queryKey: keysKeys.myKeys() })
    return created
  }

  async function retest(id: string): Promise<void> {
    await keysApi.retest(id)
    await qc.invalidateQueries({ queryKey: keysKeys.myKeys() })
  }

  async function remove(id: string): Promise<void> {
    await keysApi.remove(id)
    await qc.invalidateQueries({ queryKey: keysKeys.myKeys() })
  }

  return { keys, loading, error, reload, upload, retest, remove }
}
