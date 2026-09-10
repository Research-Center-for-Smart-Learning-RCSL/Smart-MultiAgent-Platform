<script setup lang="ts">
/* eslint-disable @typescript-eslint/no-explicit-any -- React-in-Vue bridge has inherently weak typing */
import { ref, watch, onMounted, onUnmounted } from 'vue'
import type { CanvasObject } from '../types'

const props = defineProps<{
  objects: CanvasObject[]
}>()

const emit = defineEmits<{
  change: [elements: unknown[]]
}>()

const containerRef = ref<HTMLDivElement>()
let excalidrawApi: any = null
let reactRoot: any = null

function objectsToExcalidrawElements(objects: CanvasObject[]): unknown[] {
  return objects.map((obj) => ({
    id: obj.id,
    type: mapKindToExcalidrawType(obj.kind),
    x: obj.position_x,
    y: obj.position_y,
    width: obj.width,
    height: obj.height,
    text: obj.content ?? undefined,
    ...((obj.style as Record<string, unknown>) ?? {}),
  }))
}

function mapKindToExcalidrawType(kind: string): string {
  switch (kind) {
    case 'note': return 'rectangle'
    case 'text': return 'text'
    case 'image': return 'image'
    case 'shape': return 'rectangle'
    case 'drawing': return 'freedraw'
    case 'connector': return 'arrow'
    default: return 'rectangle'
  }
}

async function mountExcalidraw() {
  if (!containerRef.value) return

  try {
    const React = await import('react')
    const ReactDOM = await import('react-dom/client')
    const { Excalidraw } = await import('@excalidraw/excalidraw')

    const elements = objectsToExcalidrawElements(props.objects)

    reactRoot = ReactDOM.createRoot(containerRef.value)

    const App = React.createElement(Excalidraw as any, {
      initialData: { elements: elements as any[] },
      excalidrawAPI: (api: any) => {
        excalidrawApi = api
      },
      onChange: (els: readonly unknown[]) => {
        emit('change', [...els])
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
  } catch (err) {
    console.error('Failed to mount Excalidraw:', err)
  }
}

watch(
  () => props.objects,
  (newObjects) => {
    if (!excalidrawApi) return
    const elements = objectsToExcalidrawElements(newObjects)
    excalidrawApi.updateScene({ elements })
  },
)

onMounted(() => {
  mountExcalidraw()
})

onUnmounted(() => {
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
