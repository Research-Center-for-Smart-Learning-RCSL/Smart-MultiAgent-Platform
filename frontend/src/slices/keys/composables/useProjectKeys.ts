import { computed, ref } from 'vue'
import { errorMessage } from '@shared/errors'
import { projectKeysApi } from '../api/project-keys'
import type { ApiKey } from '../api/keys'

export function useProjectKeys(projectId: () => string) {
  const carried = ref<ApiKey[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const isError = computed(() => error.value !== null)

  async function reload(): Promise<void> {
    const pid = projectId()
    if (!pid) {
      carried.value = []
      return
    }
    loading.value = true
    error.value = null
    try {
      const { data } = await projectKeysApi.listCarried(pid)
      carried.value = data
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      loading.value = false
    }
  }

  async function carry(keyId: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await projectKeysApi.carry(pid, keyId)
    } catch (e) {
      error.value = errorMessage(e)
    }
    await reload()
  }

  async function withdraw(keyId: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await projectKeysApi.withdraw(pid, keyId)
    } catch (e) {
      error.value = errorMessage(e)
    }
    await reload()
  }

  return { carried, loading, error, isError, reload, carry, withdraw }
}
