import { watch, onUnmounted, type Ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { wsManager } from '@shared/transport/ws-manager'
import { http } from '@shared/transport'
import { canvasKeys } from '../queries'

/**
 * Subscribe to canvas events on the chatroom's room WebSocket channel.
 * Object CRUD is handled by CRDT sync via Yjs; this composable retains
 * settings, snapshot, and image events so the UI stays in sync across tabs.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- React-in-Vue bridge
export function useCanvasSocket(chatroomId: Ref<string>, getExcalidrawApi?: () => any) {
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

    unsubs.push(
      channel.subscribe('canvas.snapshot_created', () => {
        queryClient.invalidateQueries({ queryKey: canvasKeys.snapshots(chatroomId.value) })
      }),
    )

    unsubs.push(
      channel.subscribe('canvas.snapshot_restored', () => {
        queryClient.invalidateQueries({ queryKey: canvasKeys.snapshots(chatroomId.value) })
      }),
    )

    unsubs.push(
      channel.subscribe('canvas.image_added', (data: Record<string, unknown>) => {
        const api = getExcalidrawApi?.()
        if (!api) return

        const fileId = data.fileId as string
        const url = data.url as string
        const mimeType = (data.mimeType as string) || 'image/png'

        http
          .get<Blob>(url, { responseType: 'blob' })
          .then(({ data: blob }) => {
            const reader = new FileReader()
            reader.onload = () => {
              api.addFiles([
                {
                  id: fileId,
                  dataURL: reader.result as string,
                  mimeType,
                  created: Date.now(),
                },
              ])
            }
            reader.readAsDataURL(blob)
          })
          .catch(() => {
            // Image fetch failed; Excalidraw shows a placeholder
          })
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
