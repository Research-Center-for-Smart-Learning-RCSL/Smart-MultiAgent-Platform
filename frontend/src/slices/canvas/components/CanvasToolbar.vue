<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  RectangleGroupIcon,
  DocumentTextIcon,
  PhotoIcon,
  PencilIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ArrowDownTrayIcon,
  CameraIcon,
  Cog6ToothIcon,
  LinkIcon,
  Square2StackIcon,
} from '@heroicons/vue/24/outline'
import SDropdown from '@shared/ui/SDropdown.vue'

const { t } = useI18n()

const props = defineProps<{
  isFullscreen: boolean
  isSaving: boolean
  isExporting: boolean
}>()

const emit = defineEmits<{
  addNote: []
  addText: []
  addShape: []
  addConnector: []
  draw: []
  uploadImage: []
  save: []
  toggleFullscreen: []
  openSettings: []
  exportPng: [scale: 1 | 2]
  exportSvg: []
}>()

const exportItems = computed(() => [
  { key: 'png-1x', label: t('canvas.exportPng') },
  { key: 'png-2x', label: t('canvas.exportPng2x') },
  { key: 'svg', label: t('canvas.exportSvg') },
])

function onExportSelect(key: string) {
  if (key === 'png-1x') emit('exportPng', 1)
  else if (key === 'png-2x') emit('exportPng', 2)
  else if (key === 'svg') emit('exportSvg')
}
</script>

<template>
  <div class="canvas-toolbar">
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.addNote')"
      @click="emit('addNote')"
    >
      <Square2StackIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.addText')"
      @click="emit('addText')"
    >
      <DocumentTextIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.addShape')"
      @click="emit('addShape')"
    >
      <RectangleGroupIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.addConnector')"
      @click="emit('addConnector')"
    >
      <LinkIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.draw')"
      @click="emit('draw')"
    >
      <PencilIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.uploadImage')"
      @click="emit('uploadImage')"
    >
      <PhotoIcon class="canvas-toolbar__icon" />
    </button>

    <div class="canvas-toolbar__separator" />

    <SDropdown
      :items="exportItems"
      placement="bottom-start"
      @select="onExportSelect"
    >
      <template #trigger>
        <button
          class="canvas-toolbar__btn"
          :title="t('canvas.export')"
          :disabled="props.isExporting"
        >
          <ArrowDownTrayIcon class="canvas-toolbar__icon" />
        </button>
      </template>
    </SDropdown>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.save')"
      :disabled="isSaving"
      @click="emit('save')"
    >
      <CameraIcon class="canvas-toolbar__icon" />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="isFullscreen ? t('canvas.exitFullscreen') : t('canvas.fullscreen')"
      @click="emit('toggleFullscreen')"
    >
      <ArrowsPointingInIcon
        v-if="isFullscreen"
        class="canvas-toolbar__icon"
      />
      <ArrowsPointingOutIcon
        v-else
        class="canvas-toolbar__icon"
      />
    </button>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.settings')"
      @click="emit('openSettings')"
    >
      <Cog6ToothIcon class="canvas-toolbar__icon" />
    </button>
  </div>
</template>

<style scoped>
.canvas-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-0-5);
  padding: var(--space-1) var(--space-2);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.canvas-toolbar__btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.canvas-toolbar__btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.canvas-toolbar__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.canvas-toolbar__icon {
  width: 18px;
  height: 18px;
}

.canvas-toolbar__separator {
  width: 1px;
  height: 20px;
  background: var(--color-border);
  margin: 0 var(--space-1);
}
</style>
