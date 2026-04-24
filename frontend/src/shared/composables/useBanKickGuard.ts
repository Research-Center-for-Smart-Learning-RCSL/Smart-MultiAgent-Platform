// Listens for ban-kick events on /ws/user/{id} and redirects to login (R24.19).

import { watch } from 'vue'
import { useRouter } from 'vue-router'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { useSessionStore } from '@slices/identity'

export function useBanKickGuard(): void {
  const router = useRouter()
  const session = useSessionStore()
  let unsubStatus: (() => void) | null = null
  let unsubEvent: (() => void) | null = null

  function teardown(): void {
    unsubStatus?.()
    unsubEvent?.()
    unsubStatus = null
    unsubEvent = null
  }

  watch(
    () => session.me?.id,
    (userId) => {
      teardown()
      if (!userId) return

      const channel = wsManager.channel(`/user/${userId}`)

      unsubEvent = channel.subscribe('ban-kick', (_ev: ChannelEvent) => {
        session.clear()
        router.push({ name: 'identity.login' })
      })

      channel.connect()

      unsubStatus = channel.onStatus(() => {
        // reconnect is handled by the channel itself
      })
    },
    { immediate: true },
  )
}
