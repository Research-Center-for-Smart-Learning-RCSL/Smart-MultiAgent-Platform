import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  setAccessToken,
  setRefreshToken,
  wsManager,
} from '@shared/transport'
import { queryClient } from '@shared/query-client'
import { runAllCleanups } from '@shared/stores/useAppCleanup'
import { authApi, type Me, type TokenPair } from '../api/auth'

export const useSessionStore = defineStore('identity/session', () => {
  const me = ref<Me | null>(null)
  const accessTokenExpiresAt = ref<number | null>(null)
  const isAuthenticated = computed(() => me.value !== null)
  const isVerified = computed(() => !!me.value?.email_verified)

  function applyTokens(pair: TokenPair): void {
    setAccessToken(pair.access_token)
    setRefreshToken(pair.refresh_token ?? null)
    accessTokenExpiresAt.value = Date.now() + pair.expires_in * 1000
  }

  async function login(email: string, password: string): Promise<void> {
    // Login takes no CAPTCHA (R19a.12 is register-only); the backend /auth/login
    // payload has no captcha_token field.
    const { data } = await authApi.login({ email, password })
    applyTokens(data)
    await refreshMe()
  }

  async function refreshMe(): Promise<void> {
    const { data } = await authApi.me()
    me.value = data
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clear()
    }
  }

  function clear(): void {
    me.value = null
    accessTokenExpiresAt.value = null
    setAccessToken(null)
    setRefreshToken(null)
    wsManager.closeAll()
    queryClient.clear()
    runAllCleanups()
  }

  async function hydrate(): Promise<void> {
    // Called at app boot: attempt a silent refresh using the httpOnly
    // smap_refresh cookie set by the server. If there is no valid cookie the
    // server returns 401 and we start unauthenticated.
    try {
      const { data } = await authApi.refresh()
      applyTokens(data)
      await refreshMe()
    } catch {
      clear()
    }
  }

  return {
    me,
    isAuthenticated,
    isVerified,
    applyTokens,
    login,
    logout,
    refreshMe,
    clear,
    hydrate,
  }
})
