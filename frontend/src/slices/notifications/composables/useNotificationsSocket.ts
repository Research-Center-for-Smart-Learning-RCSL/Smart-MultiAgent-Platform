// Live notification updates over the shared /ws/user/{id} presence channel
// (R18.03). The backend emits `notification.created` when a new notification is
// persisted; we invalidate the list + unread-count queries on each.
//
// IMPORTANT: this is an ADDITIVE subscriber. useBanKickGuard owns the
// /user/{id} channel's lifecycle (it close()s on user switch, which clears all
// handlers — the WsManager channel is a singleton with no refcounting). So this
// composable only subscribes + connect()s (idempotent) and, on teardown, only
// unsubscribes its own handler — it must never close the channel, or it would
// kill ban-kick's subscription too.

import { onScopeDispose, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { notificationKeys } from '../queries'

export function useNotificationsSocket(): void {
  const session = useSessionStore()
  const qc = useQueryClient()
  let unsub: (() => void) | null = null

  watch(
    () => session.me?.id,
    (userId) => {
      unsub?.()
      unsub = null
      if (!userId) return

      const channel = wsManager.channel(`/user/${userId}`)
      unsub = channel.subscribe('notification.created', (_ev: ChannelEvent) => {
        qc.invalidateQueries({ queryKey: notificationKeys.list() })
        qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
      })
      // Idempotent: ban-kick likely already connected this channel.
      channel.connect()
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    unsub?.()
    unsub = null
  })
}
