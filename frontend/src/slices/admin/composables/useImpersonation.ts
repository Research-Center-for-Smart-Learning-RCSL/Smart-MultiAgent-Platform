import { computed } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { getAccessToken, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

export function useImpersonation() {
  const impersonatedBy = computed<string | null>(() => {
    const token = getAccessToken()
    if (!token) return null
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      return payload.impersonated_by ?? null
    } catch {
      return null
    }
  })

  const isImpersonating = computed(() => impersonatedBy.value !== null)

  const startImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.impersonate(userId),
    onSuccess: (res) => {
      setAccessToken(res.data.access_token)
    },
  })

  const endImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.endImpersonate(userId),
  })

  function blockMutatingAction(): boolean {
    if (isImpersonating.value) {
      return true
    }
    return false
  }

  return {
    impersonatedBy,
    isImpersonating,
    startImpersonation,
    endImpersonation,
    blockMutatingAction,
  }
}
