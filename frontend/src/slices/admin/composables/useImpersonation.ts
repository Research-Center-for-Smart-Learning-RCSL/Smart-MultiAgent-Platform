import { computed, ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { accessTokenClaims, getAccessToken, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

const _STORAGE_KEY = 'smap:impersonation:admin_token'

function _readStorage(): string | null {
  try { return sessionStorage.getItem(_STORAGE_KEY) } catch { return null }
}
function _writeStorage(v: string) {
  try { sessionStorage.setItem(_STORAGE_KEY, v) } catch { /* restricted */ }
}
function _clearStorage() {
  try { sessionStorage.removeItem(_STORAGE_KEY) } catch { /* restricted */ }
}

const savedAdminToken = ref<string | null>(_readStorage())

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
      const adminToken = getAccessToken()
      savedAdminToken.value = adminToken
      if (adminToken) _writeStorage(adminToken)
      setAccessToken(res.data.access_token)
    },
  })

  const endImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.endImpersonate(userId),
    onSuccess: () => {
      setAccessToken(savedAdminToken.value)
      savedAdminToken.value = null
      _clearStorage()
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
