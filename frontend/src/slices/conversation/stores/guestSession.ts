import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { guestSessionEnd, markGuestSessionEnded } from '@shared/transport'

export const GUEST_STORAGE_PREFIX = 'smap:guest:'

export type GuestSessionState = 'active' | 'expired' | 'disabled' | 'gone'

export const useGuestSessionStore = defineStore('guestSession', () => {
  // Memory only: the link token must never reach persistent storage ([R24.43]),
  // so after a reload there is no rejoin URL and the room says to reopen the link.
  const guestToken = ref<string | null>(null)
  const chatroomId = ref<string | null>(null)
  // Derived from the transport's record, so every path that ends the session
  // (socket close, background refresh, request refresh, ticket) shows the same
  // banner.
  const sessionState = computed<GuestSessionState>(() => guestSessionEnd.value ?? 'active')

  const rejoinUrl = computed(() => {
    if (!guestToken.value || !chatroomId.value) return null
    return `/g/${chatroomId.value}/${guestToken.value}`
  })

  function setGuestToken(roomId: string, token: string): void {
    chatroomId.value = roomId
    guestToken.value = token
  }

  function markDisabled(): void {
    markGuestSessionEnded('disabled')
  }

  function clear(): void {
    guestToken.value = null
    chatroomId.value = null
  }

  return { guestToken, chatroomId, sessionState, rejoinUrl, setGuestToken, markDisabled, clear }
})
