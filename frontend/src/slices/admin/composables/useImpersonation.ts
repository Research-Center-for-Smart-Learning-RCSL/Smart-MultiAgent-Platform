import { computed } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { useToast } from '@shared/composables'
import { accessTokenClaims, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

export function useImpersonation() {
  const toast = useToast()
  // Both derive from the reactive `accessTokenClaims`, so they recompute the
  // moment `setAccessToken` runs (start/end of impersonation) — a plain decode
  // of the non-reactive token would cache the first value forever (FE-8).
  const impersonatedBy = computed<string | null>(
    () =>
      (accessTokenClaims.value?.impersonated_by as string | undefined) ?? null,
  )

  const activeSessionTarget = computed<string | null>(() => {
    if (!impersonatedBy.value) return null
    return (accessTokenClaims.value?.sub as string | undefined) ?? null
  })

  const isImpersonating = computed(() => impersonatedBy.value !== null)

  const startImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.impersonate(userId),
    onSuccess: (res) => {
      setAccessToken(res.data.access_token)
    },
    onError: () => toast.error('Failed to start impersonation.'),
  })

  const endImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.endImpersonate(userId),
    onError: () => toast.error('Failed to end impersonation.'),
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
