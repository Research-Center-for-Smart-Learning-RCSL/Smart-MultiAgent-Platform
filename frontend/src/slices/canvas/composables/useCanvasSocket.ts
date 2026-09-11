import { watch, onUnmounted, type Ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { canvasKeys } from '../queries'

/**
 * Subscribe to canvas-related events on the chatroom's room WebSocket channel.
 * Object CRUD events are handled by CRDT sync (Phase 2); this composable
 * retains settings, snapshot, and comment events on the room channel.
 */
export function useCanvasSocket(chatroomId: Ref<string>) {
  const queryClient = useQueryClient()
  const unsubs: Array<() => void> = []

  function subscribe(roomId: string) {
    cleanup()
    if (!roomId) return

    const channel = wsManager.channel(`/chatroom/${roomId}`)

    const commentEvents = [
      'canvas.comment_created',
      'canvas.comment_updated',
      'canvas.comment_deleted',
    ]

    for (const eventType of commentEvents) {
      unsubs.push(
        channel.subscribe(eventType, (event: ChannelEvent) => {
          queryClient.invalidateQueries({
            queryKey: canvasKeys.commentCounts(chatroomId.value),
          })
          const objectId = event.object_id as string | undefined
          if (objectId) {
            queryClient.invalidateQueries({
              queryKey: canvasKeys.comments(chatroomId.value, objectId),
            })
          }
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
