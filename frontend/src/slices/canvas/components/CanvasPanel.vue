<script setup lang="ts">
import { ref, computed, toRef, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import CanvasToolbar from './CanvasToolbar.vue'
import { useCanvasState } from '../composables/useCanvasState'
import { useCanvasSocket } from '../composables/useCanvasSocket'
import type { CanvasObjectKind } from '../types'
import SLoadingSpinner from '@shared/ui/SLoadingSpinner.vue'
import SEmptyState from '@shared/ui/SEmptyState.vue'

const CanvasRenderer = defineAsyncComponent(() => import('./CanvasRenderer.vue'))

const { t } = useI18n()

const props = defineProps<{
  chatroomId: string
  isFullscreen: boolean
}>()

const emit = defineEmits<{
  close: []
  toggleFullscreen: []
}>()

const chatroomIdRef = toRef(props, 'chatroomId')
const {
  canvas,
  objects,
  isLoading,
  error,
  createObject,
  uploadImage,
  saveSnapshot,
  updateSettings,
  isSavingSnapshot,
} = useCanvasState(chatroomIdRef)

useCanvasSocket(chatroomIdRef)

const showSettings = ref(false)
const fileInputRef = ref<HTMLInputElement>()

async function handleAddObject(kind: CanvasObjectKind) {
  await createObject({
    kind,
    position_x: 100 + Math.random() * 200,
    position_y: 100 + Math.random() * 200,
    width: kind === 'text' ? 200 : 150,
    height: kind === 'text' ? 40 : 150,
    content: kind === 'note' ? '' : kind === 'text' ? '' : null,
  })
}

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

async function handleSave() {
  await saveSnapshot()
}

function handleChange(_elements: unknown[]) {
  // Phase 1: changes are saved manually via snapshot; Phase 2 will use CRDT sync
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
        v-if="objects.length"
        class="canvas-panel__count"
      >
        {{ objects.length }} {{ t('canvas.objects') }}
      </span>
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
      @add-note="handleAddObject('note')"
      @add-text="handleAddObject('text')"
      @add-shape="handleAddObject('shape')"
      @add-connector="handleAddObject('connector')"
      @draw="handleAddObject('drawing')"
      @upload-image="handleUploadImage"
      @save="handleSave"
      @toggle-fullscreen="emit('toggleFullscreen')"
      @open-settings="showSettings = !showSettings"
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

    <div class="canvas-panel__body">
      <SLoadingSpinner v-if="isLoading" />
      <div
        v-else-if="error"
        class="canvas-panel__error"
      >
        {{ t('canvas.loadError') }}
      </div>
      <SEmptyState
        v-else-if="objects.length === 0"
        :title="t('canvas.empty')"
        :description="t('canvas.emptyDescription')"
      />
      <Suspense v-else>
        <CanvasRenderer
          :objects="objects"
          @change="handleChange"
        />
        <template #fallback>
          <SLoadingSpinner />
        </template>
      </Suspense>
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
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
}

.canvas-panel__title {
  font-weight: 600;
  font-size: var(--font-size-sm);
}

.canvas-panel__count {
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
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
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.canvas-panel__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.canvas-panel__close-icon {
  width: 18px;
  height: 18px;
}

.canvas-panel__settings {
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-size-sm);
}

.canvas-panel__setting {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.canvas-panel__body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.canvas-panel__error {
  color: var(--color-danger);
  font-size: var(--font-size-sm);
}
</style>
