import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { errorMessage } from '@shared/errors'
import { keysKeys } from '../queries'
import { keysApi, type KeyProject } from '../api/keys'
import { projectKeysApi } from '../api/project-keys'

/**
 * Reverse view for a single owned key: which projects it is carried into, and
 * per-project withdrawal. Withdraw hits the existing project-scoped endpoint
 * (the owner passes the `key.delete_own` gate) and refreshes both this list
 * and the my-keys badge count.
 */
export function useKeyProjects(keyId: MaybeRefOrGetter<string>) {
  const qc = useQueryClient()
  const id = computed(() => toValue(keyId))

  const { data, isLoading: loading, error: queryError, refetch } = useQuery({
    queryKey: computed(() => keysKeys.keyProjects(id.value)),
    queryFn: () => keysApi.projects(id.value).then((r) => r.data),
    enabled: computed(() => !!id.value),
  })

  const projects = computed<KeyProject[]>(() => data.value ?? [])
  const error = computed(() => (queryError.value ? errorMessage(queryError.value) : null))

  async function withdraw(projectId: string): Promise<void> {
    await projectKeysApi.withdraw(projectId, id.value)
    await Promise.all([
      qc.invalidateQueries({ queryKey: keysKeys.keyProjects(id.value) }),
      qc.invalidateQueries({ queryKey: keysKeys.myKeys() }),
    ])
  }

  return { projects, loading, error, refetch, withdraw }
}
