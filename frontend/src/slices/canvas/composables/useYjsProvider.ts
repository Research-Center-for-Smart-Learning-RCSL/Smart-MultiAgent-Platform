import { ref, watch, onUnmounted, type Ref } from 'vue'
import * as Y from 'yjs'
import {
  Awareness,
  encodeAwarenessUpdate,
  applyAwarenessUpdate,
} from 'y-protocols/awareness'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { useSessionStore } from '@slices/identity'

const AWARENESS_COLORS = [
  '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4',
  '#FFEAA7', '#DDA0DD', '#98D8C8', '#F7DC6F',
  '#BB8FCE', '#85C1E9', '#F1948A', '#82E0AA',
]

function pickColor(userId: string): string {
  let hash = 0
  for (let i = 0; i < userId.length; i++) {
    hash = ((hash << 5) - hash + userId.charCodeAt(i)) | 0
  }
  return AWARENESS_COLORS[Math.abs(hash) % AWARENESS_COLORS.length]
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

function fromBase64(b64: string): Uint8Array {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

export interface YjsProviderState {
  doc: Y.Doc
  awareness: Awareness
  connected: Ref<boolean>
  destroy: () => void
}

export function useYjsProvider(canvasId: Ref<string>): YjsProviderState {
  const doc = new Y.Doc()
  const awareness = new Awareness(doc)
  const connected = ref(false)

  const session = useSessionStore()
  let channel: ReturnType<typeof wsManager.channel> | null = null
  const unsubs: Array<() => void> = []
  let destroyed = false

  // Set local awareness state
  function setLocalAwareness() {
    const me = session.me
    if (!me) return
    awareness.setLocalStateField('user', {
      name: me.display_name || me.email.split('@')[0],
      color: pickColor(me.id),
      userId: me.id,
    })
  }

  // Handle doc updates: send to server
  function onDocUpdate(update: Uint8Array, origin: unknown) {
    if (origin === 'remote' || !channel) return
    channel.send({
      type: 'yjs-update',
      data: toBase64(update),
    })
  }

  // Handle awareness updates: send to server
  function onAwarenessUpdate(
    { added, updated, removed }: { added: number[]; updated: number[]; removed: number[] },
    origin: unknown,
  ) {
    if (origin === 'remote' || !channel) return
    const changedClients = [...added, ...updated, ...removed]
    const encodedUpdate = encodeAwarenessUpdate(awareness, changedClients)
    channel.send({
      type: 'awareness',
      data: toBase64(encodedUpdate),
    })
  }

  function connectToCanvas(id: string) {
    cleanup()
    if (!id || destroyed) return

    const path = `/canvas/${id}`
    channel = wsManager.channel(path)

    // Subscribe to server messages
    unsubs.push(
      channel.subscribe('yjs-sync-step-1', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(doc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('yjs-sync-step-2', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(doc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('yjs-update', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(doc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('awareness', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          applyAwarenessUpdate(awareness, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.onStatus((isConnected: boolean) => {
        connected.value = isConnected
        if (isConnected) {
          setLocalAwareness()
          // Send sync step 1 (our state vector)
          const sv = Y.encodeStateVector(doc)
          channel?.send({
            type: 'yjs-sync-step-1',
            data: toBase64(sv),
          })
        }
      }),
    )

    // Listen for local doc updates
    doc.on('update', onDocUpdate)

    // Listen for local awareness changes
    awareness.on('update', onAwarenessUpdate)

    channel.connect()
  }

  function cleanup() {
    doc.off('update', onDocUpdate)
    awareness.off('update', onAwarenessUpdate)
    unsubs.forEach((fn) => fn())
    unsubs.length = 0
    if (channel) {
      const path = `/canvas/${canvasId.value}`
      wsManager.close(path)
      channel = null
    }
    connected.value = false
  }

  watch(canvasId, (id) => connectToCanvas(id), { immediate: true })

  onUnmounted(() => {
    destroyed = true
    cleanup()
  })

  function destroy() {
    destroyed = true
    cleanup()
    awareness.destroy()
    doc.destroy()
  }

  return { doc, awareness, connected, destroy }
}
