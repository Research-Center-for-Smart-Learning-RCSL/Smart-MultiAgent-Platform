import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  setAccessToken,
  setRefreshToken,
  wsManager,
} from '@shared/transport'
import { queryClient } from '@shared/query-client'
import { useOrchestrationStore, useWorkflowStore } from '@slices/workflow'
import { useConversationStore } from '@slices/conversation'
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
    wsManager.closeAll()
    queryClient.clear()
    useOrchestrationStore().clearAll()
    useConversationStore().clearAll()
    useWorkflowStore().clearAll()
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
