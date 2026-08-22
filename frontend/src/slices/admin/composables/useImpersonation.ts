import { computed, ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { accessTokenClaims, getAccessToken, setAccessToken } from '@shared/transport'
import { adminApi } from '../api/admin'

/** Saved admin token to restore after impersonation ends (B5).
 *  Memory-only — page refresh ends the impersonation session. Storing the
 *  admin JWT in sessionStorage/localStorage would widen the XSS surface. */
const savedAdminToken = ref<string | null>(null)

// Module scope, not per-call: all three derive from the reactive
// `accessTokenClaims` and nothing else, so they recompute the moment
// `setAccessToken` runs (start/end of impersonation) — a plain decode of the
// non-reactive token would cache the first value forever (FE-8). Hoisting them
// out of useImpersonation() is what lets `useImpersonationFlag` below read them
// without creating the mutations, which need a QueryClient.
const impersonatedBy = computed<string | null>(
  () => (accessTokenClaims.value?.impersonated_by as string | undefined) ?? null,
)

const activeSessionTarget = computed<string | null>(() => {
  if (!impersonatedBy.value) return null
  return (accessTokenClaims.value?.sub as string | undefined) ?? null
})

const isImpersonating = computed(() => impersonatedBy.value !== null)

/** The flag alone, for a host that sits outside the QueryClient provider.
 *
 *  App.vue is mounted above it and needs only to know whether the banner is
 *  rendering, because while it is, the banner owns the safe-area strip and the
 *  top bar must not reserve one (main.css, `--topbar-inset-top`). Calling
 *  `useImpersonation()` there throws on `useMutation`.
 */
export function useImpersonationFlag() {
  return { isImpersonating }
}

export function useImpersonation() {
  const { t } = useI18n()
  const toast = useToast()

  const startImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.impersonate(userId),
    onSuccess: (res) => {
      savedAdminToken.value = getAccessToken()
      setAccessToken(res.access_token)
    },
    onError: () => toast.error(t('admin.impersonation.startFailed')),
  })

  const endImpersonation = useMutation({
    mutationFn: (userId: string) => adminApi.endImpersonate(userId),
    onSuccess: () => {
      setAccessToken(savedAdminToken.value)
      savedAdminToken.value = null
    },
    onError: () => toast.error(t('admin.impersonation.endFailed')),
  })

  return {
    impersonatedBy,
    activeSessionTarget,
    isImpersonating,
    startImpersonation,
    endImpersonation,
  }
}
