import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getAccessToken } from '@shared/transport'

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

  return { currentTab, impersonatedBy }
})
