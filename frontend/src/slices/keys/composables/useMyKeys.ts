import { ref } from 'vue'
import axios from 'axios'
import { keysApi, type ApiKey, type ApiKeyProvider } from '../api/keys'

export function useMyKeys() {
  const keys = ref<ApiKey[]>([])
  const loading = ref(false)
  const uploading = ref(false)
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
    if (uploading.value) return null
    error.value = null
    uploading.value = true
    try {
      const { data } = await keysApi.upload(provider, name, secret)
      await reload()
      return data
    } catch (e) {
      error.value = extractDetail(e)
      return null
    } finally {
      uploading.value = false
    }
  }

  async function retest(id: string): Promise<void> {
    try {
      await keysApi.retest(id)
      await reload()
    } catch (e) {
      error.value = extractDetail(e)
    }
  }

  async function remove(id: string): Promise<void> {
    try {
      await keysApi.remove(id)
      await reload()
    } catch (e) {
      error.value = extractDetail(e)
    }
  }

  return { keys, loading, uploading, error, reload, upload, retest, remove }
}

interface ProblemDetails {
  detail?: string
  title?: string
}

function extractDetail(e: unknown): string {
  if (axios.isAxiosError<ProblemDetails>(e)) {
    return e.response?.data?.detail ?? e.response?.data?.title ?? e.message ?? 'request failed'
  }
  if (e instanceof Error) return e.message
  return 'request failed'
}
