<script setup lang="ts">
/* eslint-disable @typescript-eslint/no-explicit-any -- React-in-Vue bridge has inherently weak typing */
import { ref, onMounted, onUnmounted, watch, toRaw } from 'vue'
import * as Y from 'yjs'
import type { Awareness } from 'y-protocols/awareness'

const props = defineProps<{
  doc: Y.Doc
  awareness: Awareness
}>()

const containerRef = ref<HTMLDivElement>()
let excalidrawApi: any = null
let reactRoot: any = null

// Generation counter: incremented on remote updates, checked in onChange.
// React 18 fires onChange asynchronously; a boolean flag would already be
// reset by the time the async callback runs. A counter survives the gap.
let remoteUpdateGen = 0
let lastSyncedGen = 0

function getElementsMap(): Y.Map<any> {
  return toRaw(props.doc).getMap('excalidraw-elements')
}

function readElementsFromYjs(): any[] {
  const ymap = getElementsMap()
  const result: any[] = []
  ymap.forEach((val: any) => {
    const el = val instanceof Y.Map ? val.toJSON() : val
    if (!el.isDeleted) result.push(el)
  })
  return result
}

async function mountExcalidraw() {
  if (!containerRef.value) return

  try {
    const React = await import('react')
    const ReactDOM = await import('react-dom/client')
    const { Excalidraw } = await import('@excalidraw/excalidraw')

    const initialElements = readElementsFromYjs()

    reactRoot = ReactDOM.createRoot(containerRef.value)

    const App = React.createElement(Excalidraw as any, {
      initialData: { elements: initialElements as any[] },
      excalidrawAPI: (api: any) => {
        excalidrawApi = api
      },
      onChange: (elements: readonly any[]) => {
        if (remoteUpdateGen !== lastSyncedGen) {
          lastSyncedGen = remoteUpdateGen
          return
        }
        syncToYjs(elements)
      },
      UIOptions: {
        canvasActions: {
          export: false,
          saveAsImage: false,
          loadScene: false,
        },
      },
    } as any)
    reactRoot.render(App)

    getElementsMap().observeDeep(onYjsChange)
  } catch (err) {
    console.error('Failed to mount Excalidraw:', err)
  }
}

function syncToYjs(elements: readonly any[]) {
  const doc = toRaw(props.doc)
  const ymap = getElementsMap()

  doc.transact(() => {
    const seen = new Set<string>()
    for (const el of elements) {
      if (!el.id) continue
      seen.add(el.id)
      const existing = ymap.get(el.id)
      if (existing instanceof Y.Map) {
        // Update changed fields only
        for (const [key, value] of Object.entries(el)) {
          const cur = existing.get(key)
          if (cur !== value && JSON.stringify(cur) !== JSON.stringify(value)) {
            existing.set(key, value)
          }
        }
      } else {
        ymap.set(el.id, new Y.Map(Object.entries(el)))
      }
    }
    // Mark deleted elements
    ymap.forEach((_val: any, key: string) => {
      if (!seen.has(key)) {
        const entry = ymap.get(key)
        if (entry instanceof Y.Map) {
          entry.set('isDeleted', true)
        }
      }
    })
  }, 'local')
}

function onYjsChange(events: any[], transaction: any) {
  if (transaction.origin === 'local') return
  if (!excalidrawApi) return

  remoteUpdateGen++
  const elements = readElementsFromYjs()
  excalidrawApi.updateScene({ elements })
}

let awarenessHandler: (() => void) | null = null

function onAwarenessChange() {
  if (!excalidrawApi) return
  const rawAwareness = toRaw(props.awareness)
  const states = rawAwareness.getStates()
  const collaborators = new Map<string, Record<string, unknown>>()

  states.forEach((state: any, clientId: number) => {
    if (clientId === rawAwareness.clientID) return
    const user = state.user
    if (!user) return
    const entry: Record<string, unknown> = {}
    if (state.cursor) entry.pointer = state.cursor
    if (user.name) entry.username = user.name
    if (user.color) {
      entry.color = { background: `${user.color}33`, stroke: user.color }
    }
    collaborators.set(String(clientId), entry)
  })

  excalidrawApi.updateScene({ collaborators })
}

watch(
  () => props.awareness,
  (awareness, oldAwareness) => {
    if (oldAwareness && awarenessHandler) {
      toRaw(oldAwareness).off('change', awarenessHandler)
    }
    if (!awareness) return
    awarenessHandler = onAwarenessChange
    toRaw(awareness).on('change', awarenessHandler)
  },
  { immediate: true },
)

onMounted(() => {
  mountExcalidraw()
})

onUnmounted(() => {
  if (awarenessHandler) {
    toRaw(props.awareness).off('change', awarenessHandler)
    awarenessHandler = null
  }
  getElementsMap().unobserveDeep(onYjsChange)
  excalidrawApi = null
  if (reactRoot) {
    reactRoot.unmount()
    reactRoot = null
  }
})
</script>

<template>
  <div
    ref="containerRef"
    class="canvas-renderer"
  />
</template>

<style scoped>
.canvas-renderer {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.canvas-renderer :deep(.excalidraw) {
  width: 100%;
  height: 100%;
}
</style>
