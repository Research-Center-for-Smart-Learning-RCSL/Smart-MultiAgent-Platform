import { computed } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { errorMessage } from '@shared/errors'
import { keysKeys } from '../queries'
import { projectKeysApi } from '../api/project-keys'
import type { ApiKey } from '../api/keys'

export function useProjectKeys(projectId: () => string) {
  const qc = useQueryClient()

  const { data, isLoading: loading, error: queryError, refetch } = useQuery({
    queryKey: computed(() => keysKeys.projectKeys(projectId())),
    queryFn: () => projectKeysApi.listCarried(projectId()).then((r) => r.data),
    enabled: computed(() => !!projectId()),
  })

  const carried = computed<ApiKey[]>(() => data.value ?? [])
  const error = computed(() => queryError.value ? errorMessage(queryError.value) : null)
  const isError = computed(() => error.value !== null)

  async function reload(): Promise<void> {
    await refetch()
  }

  async function carry(keyId: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await projectKeysApi.carry(pid, keyId)
    await qc.invalidateQueries({ queryKey: keysKeys.projectKeys(pid) })
  }

  async function withdraw(keyId: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await projectKeysApi.withdraw(pid, keyId)
    await qc.invalidateQueries({ queryKey: keysKeys.projectKeys(pid) })
  }

  return { carried, loading, error, isError, reload, carry, withdraw }
}
