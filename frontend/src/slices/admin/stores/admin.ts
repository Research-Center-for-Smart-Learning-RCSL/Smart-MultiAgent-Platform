import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { accessTokenClaims } from '@shared/transport'

export type AdminTab =
  | 'users'
  | 'admins'
  | 'ip-bans'
  | 'orgs'
  | 'projects'
  | 'audit'
  | 'ops'
  | 'rate-limits'
  | 'metrics'

export const useAdminStore = defineStore('admin/admin', () => {
  const currentTab = ref<AdminTab>('users')

  // Derives from the reactive `accessTokenClaims` — recomputes whenever the
  // token changes, so the banner appears/disappears with impersonation (FE-8).
  const impersonatedBy = computed<string | null>(
    () =>
      (accessTokenClaims.value?.impersonated_by as string | undefined) ?? null,
  )

  return { currentTab, impersonatedBy }
})
