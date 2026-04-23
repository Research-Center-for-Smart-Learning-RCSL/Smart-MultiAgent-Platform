import { computed } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { getAccessToken, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

export function useImpersonation() {
  function decodePayload(): Record<string, unknown> | null {
    const token = getAccessToken()
    if (!token) return null
    try {
      return JSON.parse(atob(token.split('.')[1]))
    } catch {
      return null
    }
  }

  const impersonatedBy = computed<string | null>(() => {
    const p = decodePayload()
    return (p?.impersonated_by as string) ?? null
  })

  const activeSessionTarget = computed<string | null>(() => {
    if (!impersonatedBy.value) return null
    const p = decodePayload()
    return (p?.sub as string) ?? null
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
    activeSessionTarget,
    isImpersonating,
    startImpersonation,
    endImpersonation,
    blockMutatingAction,
  }
}
