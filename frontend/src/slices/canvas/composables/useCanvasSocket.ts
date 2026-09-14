import { watch, onUnmounted, type Ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager } from '@shared/transport/ws-manager'
import { canvasKeys } from '../queries'

/**
 * Subscribe to canvas settings changes on the chatroom's room WebSocket
 * channel. Object CRUD is handled by CRDT sync via Yjs; this composable
 * only retains the settings event so the expose_to_agents checkbox stays
 * in sync across tabs.
 */
export function useCanvasSocket(chatroomId: Ref<string>) {
  const queryClient = useQueryClient()
  const unsubs: Array<() => void> = []

  function subscribe(roomId: string) {
    cleanup()
    if (!roomId) return

    const channel = wsManager.channel(`/chatroom/${roomId}`)

    unsubs.push(
      channel.subscribe('canvas.settings_updated', () => {
        queryClient.invalidateQueries({ queryKey: canvasKeys.canvas(chatroomId.value) })
      }),
    )
  }

  function cleanup() {
    unsubs.forEach((fn) => fn())
    unsubs.length = 0
  }

  watch(chatroomId, (id) => subscribe(id), { immediate: true })
  onUnmounted(cleanup)
}
