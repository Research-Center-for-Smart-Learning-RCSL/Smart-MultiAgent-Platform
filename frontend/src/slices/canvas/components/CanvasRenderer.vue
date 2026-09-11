<script setup lang="ts">
/* eslint-disable @typescript-eslint/no-explicit-any -- React-in-Vue bridge has inherently weak typing */
import { ref, onMounted, onUnmounted, watch, toRaw } from 'vue'
import type * as Y from 'yjs'
import type { Awareness } from 'y-protocols/awareness'

const props = defineProps<{
  doc: Y.Doc
  awareness: Awareness
}>()

const containerRef = ref<HTMLDivElement>()
let excalidrawApi: any = null
let reactRoot: any = null
let suppressOnChange = false

async function mountExcalidraw() {
  if (!containerRef.value) return

  try {
    const React = await import('react')
    const ReactDOM = await import('react-dom/client')
    const { Excalidraw } = await import('@excalidraw/excalidraw')

    const doc = toRaw(props.doc)
    const elementsArray = doc.getArray('elements')

    // Build initial elements from Yjs doc
    const initialElements = elementsArray.toArray().map((item: any) => {
      if (item.toJSON) return item.toJSON()
      return item
    })

    reactRoot = ReactDOM.createRoot(containerRef.value)

    const App = React.createElement(Excalidraw as any, {
      initialData: { elements: initialElements as any[] },
      excalidrawAPI: (api: any) => {
        excalidrawApi = api
      },
      onChange: (elements: readonly any[]) => {
        if (suppressOnChange) return
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

    // Listen for remote Yjs changes
    elementsArray.observe(onYjsChange)
  } catch (err) {
    console.error('Failed to mount Excalidraw:', err)
  }
}

function syncToYjs(elements: readonly any[]) {
  const doc = toRaw(props.doc)
  const elementsArray = doc.getArray('elements')

  doc.transact(() => {
    // Replace all elements in the Yjs array
    if (elementsArray.length > 0) {
      elementsArray.delete(0, elementsArray.length)
    }
    for (const el of elements) {
      elementsArray.push([el])
    }
  }, 'local')
}

function onYjsChange(_event: any, transaction: any) {
  if (transaction.origin === 'local') return
  if (!excalidrawApi) return

  const doc = toRaw(props.doc)
  const elementsArray = doc.getArray('elements')
  const elements = elementsArray.toArray().map((item: any) => {
    if (item.toJSON) return item.toJSON()
    return item
  })

  suppressOnChange = true
  try {
    excalidrawApi.updateScene({ elements })
  } finally {
    suppressOnChange = false
  }
}

// Watch awareness for cursor/selection updates
watch(
  () => props.awareness,
  (awareness) => {
    if (!awareness) return
    const rawAwareness = toRaw(awareness)
    rawAwareness.on('change', () => {
      if (!excalidrawApi) return
      const states = rawAwareness.getStates()
      const collaborators = new Map<string, { pointer?: { x: number; y: number }; username?: string; color?: { background: string; stroke: string } }>()

      states.forEach((state: any, clientId: number) => {
        if (clientId === rawAwareness.clientID) return
        const user = state.user
        if (!user) return
        collaborators.set(String(clientId), {
          pointer: state.cursor ?? undefined,
          username: user.name ?? undefined,
          color: user.color
            ? { background: `${user.color}33`, stroke: user.color }
            : undefined,
        })
      })

      excalidrawApi.updateScene({ collaborators })
    })
  },
  { immediate: true },
)

onMounted(() => {
  mountExcalidraw()
})

onUnmounted(() => {
  const doc = toRaw(props.doc)
  const elementsArray = doc.getArray('elements')
  elementsArray.unobserve(onYjsChange)
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
