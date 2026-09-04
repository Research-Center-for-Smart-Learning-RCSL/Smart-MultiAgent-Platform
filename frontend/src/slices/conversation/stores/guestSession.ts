import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const GUEST_STORAGE_PREFIX = 'smap:guest:'

export type GuestSessionState = 'active' | 'expired' | 'disabled'

export const useGuestSessionStore = defineStore('guestSession', () => {
  const guestToken = ref<string | null>(null)
  const chatroomId = ref<string | null>(null)
  const sessionState = ref<GuestSessionState>('active')

  const rejoinUrl = computed(() => {
    if (!guestToken.value || !chatroomId.value) return null
    return `/g/${chatroomId.value}/${guestToken.value}`
  })

  function setGuestToken(roomId: string, token: string): void {
    chatroomId.value = roomId
    guestToken.value = token
    sessionState.value = 'active'
  }

  function markExpired(): void {
    sessionState.value = 'expired'
  }

  function markDisabled(): void {
    sessionState.value = 'disabled'
  }

  function clear(): void {
    guestToken.value = null
    chatroomId.value = null
    sessionState.value = 'active'
  }

  return { guestToken, chatroomId, sessionState, rejoinUrl, setGuestToken, markExpired, markDisabled, clear }
})
