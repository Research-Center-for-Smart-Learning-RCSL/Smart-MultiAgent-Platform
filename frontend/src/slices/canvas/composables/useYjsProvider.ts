import { ref, shallowRef, watch, onUnmounted, type Ref, type ShallowRef } from 'vue'
import * as Y from 'yjs'
import {
  Awareness,
  encodeAwarenessUpdate,
  applyAwarenessUpdate,
} from 'y-protocols/awareness'
import { wsManager, type ChannelEvent } from '@shared/transport/ws-manager'
import { accessTokenClaims } from '@shared/transport'

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
  return AWARENESS_COLORS[Math.abs(hash) % AWARENESS_COLORS.length] ?? '#FF6B6B'
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!)
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
  doc: ShallowRef<Y.Doc>
  awareness: ShallowRef<Awareness>
  connected: Ref<boolean>
  destroy: () => void
}

export function useYjsProvider(canvasId: Ref<string>): YjsProviderState {
  const doc = shallowRef<Y.Doc>(new Y.Doc())
  const awareness = shallowRef<Awareness>(new Awareness(doc.value))
  const connected = ref(false)

  let channel: ReturnType<typeof wsManager.channel> | null = null
  const unsubs: Array<() => void> = []
  let destroyed = false
  let prevCanvasPath: string | null = null

  function setLocalAwareness() {
    const claims = accessTokenClaims.value
    if (!claims) return
    const userId = String(claims.sub ?? 'unknown')
    awareness.value.setLocalStateField('user', {
      name: String(claims.display_name ?? claims.email ?? userId).split('@')[0],
      color: pickColor(userId),
      userId,
    })
  }

  function onDocUpdate(update: Uint8Array, origin: unknown) {
    if (origin === 'remote' || !channel) return
    channel.send({
      type: 'yjs-update',
      data: toBase64(update),
    })
  }

  function onAwarenessUpdate(
    { added, updated, removed }: { added: number[]; updated: number[]; removed: number[] },
    origin: unknown,
  ) {
    if (origin === 'remote' || !channel) return
    const changedClients = [...added, ...updated, ...removed]
    const encodedUpdate = encodeAwarenessUpdate(awareness.value, changedClients)
    channel.send({
      type: 'awareness',
      data: toBase64(encodedUpdate),
    })
  }

  function connectToCanvas(id: string) {
    cleanup()
    if (!id || destroyed) return

    // Fresh doc and awareness for the new canvas
    const newDoc = new Y.Doc()
    const newAwareness = new Awareness(newDoc)
    doc.value = newDoc
    awareness.value = newAwareness

    const path = `/canvas/${id}`
    prevCanvasPath = path
    channel = wsManager.channel(path)

    unsubs.push(
      channel.subscribe('yjs-sync-step-1', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(newDoc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('yjs-sync-step-2', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(newDoc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('yjs-update', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          Y.applyUpdate(newDoc, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.subscribe('awareness', (event: ChannelEvent) => {
        const data = event.data as string
        if (data) {
          applyAwarenessUpdate(newAwareness, fromBase64(data), 'remote')
        }
      }),
    )

    unsubs.push(
      channel.onStatus((isConnected: boolean) => {
        connected.value = isConnected
        if (isConnected) {
          setLocalAwareness()
          const sv = Y.encodeStateVector(newDoc)
          channel?.send({
            type: 'yjs-sync-step-1',
            data: toBase64(sv),
          })
        }
      }),
    )

    newDoc.on('update', onDocUpdate)
    newAwareness.on('update', onAwarenessUpdate)

    channel.connect()
  }

  function cleanup() {
    doc.value.off('update', onDocUpdate)
    awareness.value.off('update', onAwarenessUpdate)
    unsubs.forEach((fn) => fn())
    unsubs.length = 0
    if (channel && prevCanvasPath) {
      wsManager.close(prevCanvasPath)
      channel = null
      prevCanvasPath = null
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
    awareness.value.destroy()
    doc.value.destroy()
  }

  return { doc, awareness, connected, destroy }
}
