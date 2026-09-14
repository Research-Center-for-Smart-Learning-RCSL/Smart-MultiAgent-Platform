<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ArrowDownTrayIcon,
  CameraIcon,
  Cog6ToothIcon,
} from '@heroicons/vue/24/outline'
import SDropdown from '@shared/ui/SDropdown.vue'

const { t } = useI18n()

const props = defineProps<{
  isFullscreen: boolean
  isSaving: boolean
  isExporting: boolean
}>()

const emit = defineEmits<{
  save: [label?: string]
  toggleFullscreen: []
  openSettings: []
  exportPng: [scale: 1 | 2]
  exportSvg: []
}>()

const snapshotLabel = ref('')

function handleSaveClick() {
  const label = snapshotLabel.value.trim() || undefined
  emit('save', label)
  snapshotLabel.value = ''
}

function handleSaveKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    handleSaveClick()
  }
}

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
    <SDropdown
      :items="exportItems"
      placement="bottom-start"
      @select="onExportSelect"
    >
      <template #trigger>
        <button
          class="canvas-toolbar__btn"
          :title="t('canvas.export')"
          :aria-label="t('canvas.export')"
          :disabled="props.isExporting"
        >
          <ArrowDownTrayIcon class="canvas-toolbar__icon" />
        </button>
      </template>
    </SDropdown>

    <div class="canvas-toolbar__save-group">
      <input
        v-model="snapshotLabel"
        class="canvas-toolbar__label-input"
        :placeholder="t('canvas.snapshotLabelPlaceholder')"
        :aria-label="t('canvas.snapshotLabelPlaceholder')"
        maxlength="200"
        @keydown="handleSaveKeydown"
      >
      <button
        class="canvas-toolbar__btn"
        :title="t('canvas.save')"
        :aria-label="t('canvas.save')"
        :disabled="isSaving"
        @click="handleSaveClick"
      >
        <CameraIcon class="canvas-toolbar__icon" />
      </button>
    </div>

    <button
      class="canvas-toolbar__btn"
      :title="isFullscreen ? t('canvas.exitFullscreen') : t('canvas.fullscreen')"
      :aria-label="isFullscreen ? t('canvas.exitFullscreen') : t('canvas.fullscreen')"
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
      :aria-label="t('canvas.settings')"
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
  flex: 1;
  min-width: 0;
}

.canvas-toolbar__btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
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
  width: 16px;
  height: 16px;
}

.canvas-toolbar__save-group {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.canvas-toolbar__label-input {
  flex: 1;
  min-width: 60px;
  max-width: 120px;
  padding: var(--space-0-5) var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-canvas);
  color: var(--color-fg);
  font-size: var(--font-size-xs);
}

.canvas-toolbar__label-input:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent);
}
</style>
