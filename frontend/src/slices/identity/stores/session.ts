import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  clearGuestContext,
  getGuestChatroomId,
  setAccessToken,
  setRefreshToken,
  isGuestSession,
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
    // The guest context routes refresh and socket tickets to the guest
    // endpoints; left set under a user token, the guest cookie would replace
    // this session at its first expiry (F-9).
    clearGuestContext()
    setAccessToken(pair.access_token)
    setRefreshToken(pair.refresh_token ?? null)
    accessTokenExpiresAt.value = Date.now() + pair.expires_in * 1000
  }

  async function login(email: string, password: string): Promise<void> {
    // Login takes no CAPTCHA (R19a.12 is register-only); the backend /auth/login
    // payload has no captcha_token field.
    const pair = await authApi.login({ email, password })
    applyTokens(pair)
    await refreshMe()
  }

  async function refreshMe(): Promise<void> {
    me.value = await authApi.me()
  }

  // Replace the cached profile with a freshly returned `Me` (e.g. after a
  // profile update echoes back the normalised value) without a second fetch.
  function setMe(next: Me): void {
    me.value = next
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
    clearGuestContext()
    wsManager.closeAll()
    queryClient.clear()
    runAllCleanups()
  }

  async function hydrate(): Promise<void> {
    // Called at app boot: attempt a silent refresh using the httpOnly
    // smap_refresh cookie set by the server. If there is no valid cookie the
    // server returns 401 and we start unauthenticated.
    //
    // Skip while this tab holds a guest context, with or without a live guest
    // token: the catch-block clear() would wipe the guest session (or the
    // room's record of how it ended), and a successful refresh would swap the
    // account a signed-in user set aside to enter as a guest back in mid-session.
    if (isGuestSession.value || getGuestChatroomId()) return
    try {
      const pair = await authApi.refresh()
      applyTokens(pair)
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
    setMe,
    clear,
    hydrate,
  }
})
