<script setup lang="ts">
/* eslint-disable @typescript-eslint/no-explicit-any -- React-in-Vue bridge has inherently weak typing */
import { ref, onMounted, onUnmounted, watch, toRaw } from 'vue'
import { useI18n } from 'vue-i18n'
import { CpuChipIcon } from '@heroicons/vue/20/solid'
import * as Y from 'yjs'
import type { Awareness } from 'y-protocols/awareness'

const props = defineProps<{
  doc: Y.Doc
  awareness: Awareness
}>()

const { t } = useI18n()

const containerRef = ref<HTMLDivElement>()
const mountError = ref<string | null>(null)
const selectedAgentCreated = ref(false)
let excalidrawApi: any = null
let reactRoot: any = null

// Mount generation: incremented before each mount attempt so a superseded
// async mount (from the doc watcher racing onMounted) aborts silently.
let mountGen = 0

// Echo suppression: when a remote Yjs update arrives, we record the ids
// of elements that the Yjs events actually touched. The next onChange
// callback skips syncing those ids back (they came from the remote) but
// still syncs any locally-changed elements that were batched alongside.
let remoteChangedIds: Set<string> | null = null

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
  mountError.value = null

  // containerRef is always in the DOM (no v-if destroys it), but may be
  // null before onMounted or if the component is unmounting.
  if (!containerRef.value) return

  const thisMount = ++mountGen

  try {
    ;(window as any).EXCALIDRAW_ASSET_PATH = '/excalidraw-assets/'

    const [React, ReactDOM, { Excalidraw }] = await Promise.all([
      import('react'),
      import('react-dom/client'),
      import('@excalidraw/excalidraw'),
      import('@excalidraw/excalidraw/index.css'),
    ])

    if (thisMount !== mountGen) return

    const initialElements = readElementsFromYjs()

    reactRoot = ReactDOM.createRoot(containerRef.value)

    const App = React.createElement(Excalidraw as any, {
      initialData: { elements: initialElements as any[] },
      excalidrawAPI: (api: any) => {
        excalidrawApi = api
      },
      onChange: (elements: readonly any[], appState: any) => {
        const selectedIds = appState?.selectedElementIds ?? {}
        selectedAgentCreated.value = elements.some(
          (el) => selectedIds[el.id] && el.customData?.createdByAgentId,
        )

        if (remoteChangedIds !== null) {
          const ids = remoteChangedIds
          remoteChangedIds = null
          const hasLocalEdits = elements.some((el) => !ids.has(el.id))
          if (hasLocalEdits) syncToYjs(elements)
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
    if (thisMount !== mountGen) return
    console.error('Failed to mount Excalidraw:', err)
    mountError.value = t('canvas.mountError', 'Failed to load canvas')
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

  // Extract only the element ids that the remote transaction touched,
  // so the echo suppression in onChange does not mask concurrent local edits.
  const changed = new Set<string>()
  for (const event of events) {
    if (event instanceof Y.YMapEvent) {
      for (const key of event.keysChanged) changed.add(key)
    }
  }
  remoteChangedIds = changed

  const elements = readElementsFromYjs()
  excalidrawApi.updateScene({ elements })
}

// Capture the awareness instance at bind time so teardown removes the
// listener from the correct instance, not from a replacement that the
// Yjs provider may have swapped in.
let boundAwareness: Awareness | null = null
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
    boundAwareness = awareness ? toRaw(awareness) : null
    if (!awareness) return
    awarenessHandler = onAwarenessChange
    boundAwareness!.on('change', awarenessHandler)
  },
  { immediate: true },
)

function teardown() {
  if (awarenessHandler && boundAwareness) {
    boundAwareness.off('change', awarenessHandler)
    awarenessHandler = null
    boundAwareness = null
  }
  try { getElementsMap().unobserveDeep(onYjsChange) } catch { /* doc may have changed */ }
  excalidrawApi = null
  if (reactRoot) {
    reactRoot.unmount()
    reactRoot = null
  }
}

watch(
  () => props.doc,
  () => {
    teardown()
    mountExcalidraw()
  },
)

onMounted(() => {
  mountExcalidraw()
})

onUnmounted(teardown)

function getExcalidrawApi() {
  return excalidrawApi
}

defineExpose({ getExcalidrawApi })
</script>

<template>
  <div
    ref="containerRef"
    class="canvas-renderer"
  >
    <div
      v-if="mountError"
      class="canvas-renderer__error-overlay"
    >
      <span>{{ mountError }}</span>
      <button
        class="canvas-renderer__retry"
        @click="mountExcalidraw()"
      >
        {{ t('canvas.retry', 'Retry') }}
      </button>
    </div>

    <div
      v-if="selectedAgentCreated"
      class="canvas-renderer__agent-badge"
    >
      <CpuChipIcon class="canvas-renderer__agent-badge-icon" />
      <span>{{ t('canvas.agentCreated') }}</span>
    </div>
  </div>
</template>

<style scoped>
.canvas-renderer {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.canvas-renderer :deep(.excalidraw) {
  width: 100%;
  height: 100%;
}

.canvas-renderer__error-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  height: 100%;
  color: var(--color-danger);
  font-size: var(--font-size-sm);
}

.canvas-renderer__retry {
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-fg);
  cursor: pointer;
  font-size: var(--font-size-sm);
}

.canvas-renderer__retry:hover {
  background: var(--color-surface-hover);
}

.canvas-renderer__agent-badge {
  position: absolute;
  bottom: var(--space-2);
  left: var(--space-2);
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  pointer-events: none;
  box-shadow: var(--elevation-1, 0 1px 3px rgba(0 0 0 / 0.1));
}

.canvas-renderer__agent-badge-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
</style>
