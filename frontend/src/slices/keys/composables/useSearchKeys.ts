import { ref } from 'vue'
import {
  searchKeysApi,
  type SearchKey,
  type SearchProvider,
} from '../api/search-keys'

export function useSearchKeys(projectId: () => string) {
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
      error.value = detail(e)
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
      error.value = detail(e)
    }
    await reload()
  }

  async function retest(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.retest(pid, id)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function activate(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.activate(pid, id)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function remove(id: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await searchKeysApi.remove(pid, id)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  return { keys, error, reload, upload, retest, activate, remove }
}

function detail(e: unknown): string {
  const any_ = e as { response?: { data?: { detail?: string; title?: string } } }
  return any_.response?.data?.detail ?? any_.response?.data?.title ?? 'request failed'
}
