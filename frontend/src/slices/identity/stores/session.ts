import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  setAccessToken,
  setRefreshToken,
  getRefreshToken,
} from '@shared/transport'
import { authApi, type Me, type TokenPair } from '../api/auth'

export const useSessionStore = defineStore('identity/session', () => {
  const me = ref<Me | null>(null)
  const accessTokenExpiresAt = ref<number | null>(null)
  const isAuthenticated = computed(() => me.value !== null)
  const isVerified = computed(() => !!me.value?.email_verified)

  function applyTokens(pair: TokenPair): void {
    setAccessToken(pair.access_token)
    setRefreshToken(pair.refresh_token)
    accessTokenExpiresAt.value = Date.now() + pair.expires_in * 1000
  }

  async function login(email: string, password: string, captcha?: string): Promise<void> {
    const payload = captcha
      ? { email, password, captcha_token: captcha }
      : { email, password }
    const { data } = await authApi.login(payload)
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
  }

  async function hydrate(): Promise<void> {
    // Called at app boot: if a refresh token was persisted, exchange it for
    // a fresh access token up front so `/auth/me` has a bearer to present.
    // Relying on the 401-then-refresh interceptor would only work when the
    // server signals `type=.../auth/token-expired`, which isn't guaranteed
    // for a flat-out missing Authorization header.
    const refresh = getRefreshToken()
    if (!refresh) return
    try {
      const { data } = await authApi.refresh(refresh)
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
