import { computed, ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { accessTokenClaims, getAccessToken, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

/** Saved admin token to restore after impersonation ends (B5).
 *  Memory-only — page refresh ends the impersonation session. Storing the
 *  admin JWT in sessionStorage/localStorage would widen the XSS surface. */
const savedAdminToken = ref<string | null>(null)

export function useImpersonation() {
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
      savedAdminToken.value = getAccessToken()
      setAccessToken(res.access_token)
    },
  })

  const endImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.endImpersonate(userId),
    onSuccess: () => {
      setAccessToken(savedAdminToken.value)
      savedAdminToken.value = null
    },
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
