import { ref } from 'vue'
import { keysApi, type ApiKey, type ApiKeyProvider } from '../api/keys'

export function useMyKeys() {
  const keys = ref<ApiKey[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function reload(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const { data } = await keysApi.list()
      keys.value = data
    } catch (e) {
      error.value = extractDetail(e)
    } finally {
      loading.value = false
    }
  }

  async function upload(
    provider: ApiKeyProvider,
    name: string,
    secret: string,
  ): Promise<ApiKey | null> {
    error.value = null
    try {
      const { data } = await keysApi.upload(provider, name, secret)
      await reload()
      return data
    } catch (e) {
      error.value = extractDetail(e)
      return null
    }
  }

  async function retest(id: string): Promise<void> {
    try {
      await keysApi.retest(id)
    } catch (e) {
      error.value = extractDetail(e)
    }
    await reload()
  }

  async function remove(id: string): Promise<void> {
    try {
      await keysApi.remove(id)
    } catch (e) {
      error.value = extractDetail(e)
    }
    await reload()
  }

  return { keys, loading, error, reload, upload, retest, remove }
}

function extractDetail(e: unknown): string {
  const any_ = e as { response?: { data?: { detail?: string; title?: string } } }
  return any_.response?.data?.detail ?? any_.response?.data?.title ?? 'request failed'
}
