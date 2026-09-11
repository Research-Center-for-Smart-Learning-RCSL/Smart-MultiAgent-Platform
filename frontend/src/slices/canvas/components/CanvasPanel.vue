<script setup lang="ts">
import { ref, computed, toRef, defineAsyncComponent, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import CanvasToolbar from './CanvasToolbar.vue'
import CanvasHistory from './CanvasHistory.vue'
import { useCanvasState } from '../composables/useCanvasState'
import { useCanvasExport } from '../composables/useCanvasExport'
import { useCanvasSocket } from '../composables/useCanvasSocket'
import { useYjsProvider } from '../composables/useYjsProvider'
import SLoadingSpinner from '@shared/ui/SLoadingSpinner.vue'

const CanvasRenderer = defineAsyncComponent(() => import('./CanvasRenderer.vue'))

const { t } = useI18n()

const props = defineProps<{
  chatroomId: string
  chatroomName: string
  isFullscreen: boolean
}>()

const emit = defineEmits<{
  close: []
  toggleFullscreen: []
}>()

const chatroomIdRef = toRef(props, 'chatroomId')

const {
  canvas,
  isLoading,
  error,
  uploadImage,
  saveSnapshot,
  updateSettings,
  isSavingSnapshot,
} = useCanvasState(chatroomIdRef)

useCanvasSocket(chatroomIdRef)

// CRDT sync via Yjs -- canvasId is derived from the REST canvas query
const canvasIdRef = computed(() => canvas.value?.id ?? '')
const { doc, awareness, connected } = useYjsProvider(canvasIdRef)

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- React-in-Vue bridge
const canvasRendererRef = ref<ComponentPublicInstance<any> | null>(null)

function getExcalidrawApi() {
  return canvasRendererRef.value?.getExcalidrawApi?.() ?? null
}

const chatroomNameRef = toRef(props, 'chatroomName')
const { exportPng, exportSvg, isExporting } = useCanvasExport(getExcalidrawApi, chatroomNameRef)

const showSettings = ref(false)
const showHistory = ref(false)
const fileInputRef = ref<HTMLInputElement>()

function handleUploadImage() {
  fileInputRef.value?.click()
}

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await uploadImage(file)
  input.value = ''
}

async function handleSave(label?: string) {
  await saveSnapshot(label ? { label } : undefined)
}

function toggleHistory() {
  showHistory.value = !showHistory.value
}

const exposeToAgents = computed(() => canvas.value?.expose_to_agents ?? true)

async function toggleExposeToAgents() {
  await updateSettings({ expose_to_agents: !exposeToAgents.value })
}

</script>

<template>
  <div
    class="canvas-panel"
    :class="{ 'canvas-panel--fullscreen': isFullscreen }"
  >
    <div class="canvas-panel__header">
      <span class="canvas-panel__title">{{ t('canvas.title') }}</span>
      <span
        v-if="connected"
        class="canvas-panel__status canvas-panel__status--connected"
      />
      <span
        v-else
        class="canvas-panel__status canvas-panel__status--disconnected"
      />
      <button
        class="canvas-panel__close"
        :title="t('canvas.close')"
        @click="emit('close')"
      >
        <XMarkIcon class="canvas-panel__close-icon" />
      </button>
    </div>

    <CanvasToolbar
      :is-fullscreen="isFullscreen"
      :is-saving="!!isSavingSnapshot"
      :is-exporting="isExporting"
      @add-note="() => {}"
      @add-text="() => {}"
      @add-shape="() => {}"
      @add-connector="() => {}"
      @draw="() => {}"
      @upload-image="handleUploadImage"
      @save="handleSave"
      @toggle-fullscreen="emit('toggleFullscreen')"
      @open-settings="showSettings = !showSettings"
      @export-png="exportPng"
      @export-svg="exportSvg"
      @open-history="toggleHistory"
    />

    <div
      v-if="showSettings"
      class="canvas-panel__settings"
    >
      <label
        for="canvas-expose-agents"
        class="canvas-panel__setting"
      >
        <input
          id="canvas-expose-agents"
          type="checkbox"
          :checked="exposeToAgents"
          @change="toggleExposeToAgents"
        >
        {{ t('canvas.exposeToAgents') }}
      </label>
    </div>

    <div class="canvas-panel__content">
      <div class="canvas-panel__body">
        <div
          v-if="isLoading"
          class="canvas-panel__centered"
        >
          <SLoadingSpinner />
        </div>
        <div
          v-else-if="error"
          class="canvas-panel__centered"
        >
          <span class="canvas-panel__error">{{ t('canvas.loadError') }}</span>
        </div>
        <template v-else>
          <div class="canvas-panel__canvas-area">
            <Suspense>
              <CanvasRenderer
                ref="canvasRendererRef"
                :doc="doc"
                :awareness="awareness"
              />
              <template #fallback>
                <SLoadingSpinner />
              </template>
            </Suspense>
          </div>
        </template>
      </div>
      <CanvasHistory
        v-if="showHistory"
        :chatroom-id="chatroomId"
        @close="showHistory = false"
      />
    </div>

    <input
      ref="fileInputRef"
      type="file"
      :aria-label="t('canvas.uploadImage')"
      accept="image/png,image/jpeg,image/webp,image/svg+xml"
      hidden
      @change="onFileSelected"
    >
  </div>
</template>

<style scoped>
.canvas-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
}

.canvas-panel--fullscreen {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 400);
  border-left: none;
}

.canvas-panel__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.canvas-panel__title {
  font-weight: var(--weight-semibold);
  font-size: var(--font-size-sm);
}

.canvas-panel__status {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.canvas-panel__status--connected {
  background: var(--color-success);
}

.canvas-panel__status--disconnected {
  background: var(--color-muted);
}

.canvas-panel__close {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.canvas-panel__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.canvas-panel__close-icon {
  width: 18px;
  height: 18px;
}

.canvas-panel__settings {
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-size-sm);
}

.canvas-panel__setting {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.canvas-panel__content {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.canvas-panel__body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.canvas-panel__canvas-area {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.canvas-panel__centered {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.canvas-panel__error {
  color: var(--color-danger);
  font-size: var(--font-size-sm);
}
</style>
