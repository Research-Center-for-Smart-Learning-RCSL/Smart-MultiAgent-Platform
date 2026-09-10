import { watch, onUnmounted, type Ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager } from '@shared/transport/ws-manager'
import { canvasKeys } from '../queries'

/**
 * Subscribe to canvas-related events on the chatroom's room WebSocket channel.
 * Phase 1 uses the shared room channel; Phase 2 will use a dedicated canvas channel.
 */
export function useCanvasSocket(chatroomId: Ref<string>) {
  const queryClient = useQueryClient()
  const unsubs: Array<() => void> = []

  function subscribe(roomId: string) {
    cleanup()
    if (!roomId) return

    const channel = wsManager.channel(`/chatroom/${roomId}`)

    const canvasEvents = [
      'canvas.object_created',
      'canvas.object_updated',
      'canvas.object_deleted',
      'canvas.batch_updated',
    ]

    for (const eventType of canvasEvents) {
      unsubs.push(
        channel.subscribe(eventType, () => {
          queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
        }),
      )
    }

    unsubs.push(
      channel.subscribe('canvas.snapshot_created', () => {
        queryClient.invalidateQueries({ queryKey: canvasKeys.snapshots(chatroomId.value) })
      }),
    )

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
