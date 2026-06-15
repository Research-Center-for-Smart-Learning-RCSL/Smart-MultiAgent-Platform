import { ref } from 'vue'
import { useToast } from '@shared/composables'
import { errorMessage } from '@shared/errors'
import { keysApi, type ApiKey, type ApiKeyProvider } from '../api/keys'

export function useMyKeys() {
  const toast = useToast()
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
      error.value = errorMessage(e)
      toast.error('Failed to load keys.')
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
      error.value = errorMessage(e)
      toast.error('Failed to upload key.')
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
      error.value = errorMessage(e)
      toast.error('Failed to retest key.')
    }
  }

  async function remove(id: string): Promise<void> {
    try {
      await keysApi.remove(id)
      await reload()
    } catch (e) {
      error.value = errorMessage(e)
      toast.error('Failed to remove key.')
    }
  }

  return { keys, loading, uploading, error, reload, upload, retest, remove }
}
