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

    const commentEvents = [
      'canvas.comment_created',
      'canvas.comment_updated',
      'canvas.comment_deleted',
    ]

    for (const eventType of commentEvents) {
      unsubs.push(
        channel.subscribe(eventType, (payload: { object_id?: string }) => {
          queryClient.invalidateQueries({
            queryKey: canvasKeys.commentCounts(chatroomId.value),
          })
          if (payload.object_id) {
            queryClient.invalidateQueries({
              queryKey: canvasKeys.comments(chatroomId.value, payload.object_id),
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
