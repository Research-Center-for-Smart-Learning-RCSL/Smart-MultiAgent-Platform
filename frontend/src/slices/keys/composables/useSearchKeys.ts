import { ref } from 'vue'
import { useToast } from '@shared/composables'
import { errorMessage } from '@shared/errors'
import {
  searchKeysApi,
  type SearchKey,
  type SearchProvider,
} from '../api/search-keys'

export function useSearchKeys(projectId: () => string) {
  const toast = useToast()
  const keys = ref<SearchKey[]>([])
  const error = ref<string | null>(null)

  async function reload(): Promise<void> {
    const pid = projectId()
    if (!pid) {
      keys.value = []
      return
    }
    try {
      const { data } = await searchKeysApi.list(pid)
      keys.value = data
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to load search keys.')
    }
  }

  async function upload(
    provider: SearchProvider,
    secret: string,
    config: Record<string, unknown>,
  ): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.upload(pid, provider, secret, config)
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to upload search key.')
    }
    await reload()
  }

  async function retest(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.retest(pid, id)
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to retest search key.')
    }
    await reload()
  }

  async function activate(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.activate(pid, id)
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to activate search key.')
    }
    await reload()
  }

  async function remove(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.remove(pid, id)
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to remove search key.')
    }
    await reload()
  }

  return { keys, error, reload, upload, retest, activate, remove }
}
