<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
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
  ClockIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
} from '@heroicons/vue/24/outline'
import SDropdown from '@shared/ui/SDropdown.vue'

const { t } = useI18n()

const searchQuery = defineModel<string>('searchQuery', { default: '' })

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
  save: [label?: string]
  toggleFullscreen: []
  openSettings: []
  openHistory: []
  exportPng: [scale: 1 | 2]
  exportSvg: []
}>()

const searchExpanded = ref(false)
const searchInputRef = ref<HTMLInputElement>()

function toggleSearch() {
  searchExpanded.value = !searchExpanded.value
  if (searchExpanded.value) {
    nextTick(() => searchInputRef.value?.focus())
  } else {
    searchQuery.value = ''
  }
}

function handleSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    searchExpanded.value = false
    searchQuery.value = ''
  }
}

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

    <div class="canvas-toolbar__search-group">
      <button
        class="canvas-toolbar__btn"
        :class="{ 'canvas-toolbar__btn--active': searchExpanded }"
        :title="t('canvas.search')"
        @click="toggleSearch"
      >
        <MagnifyingGlassIcon class="canvas-toolbar__icon" />
      </button>
      <div
        v-if="searchExpanded"
        class="canvas-toolbar__search-input-wrap"
      >
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          class="canvas-toolbar__search-input"
          :placeholder="t('canvas.searchPlaceholder')"
          maxlength="500"
          @keydown="handleSearchKeydown"
        >
        <button
          v-if="searchQuery"
          class="canvas-toolbar__search-clear"
          :title="t('canvas.searchClear')"
          @click="searchQuery = ''"
        >
          <XMarkIcon class="canvas-toolbar__icon-sm" />
        </button>
      </div>
    </div>

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
    <div class="canvas-toolbar__save-group">
      <input
        v-model="snapshotLabel"
        class="canvas-toolbar__label-input"
        :placeholder="t('canvas.snapshotLabelPlaceholder')"
        maxlength="200"
        @keydown="handleSaveKeydown"
      >
      <button
        class="canvas-toolbar__btn"
        :title="t('canvas.save')"
        :disabled="isSaving"
        @click="handleSaveClick"
      >
        <CameraIcon class="canvas-toolbar__icon" />
      </button>
    </div>
    <button
      class="canvas-toolbar__btn"
      :title="t('canvas.history')"
      @click="emit('openHistory')"
    >
      <ClockIcon class="canvas-toolbar__icon" />
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

.canvas-toolbar__save-group {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.canvas-toolbar__label-input {
  flex: 1;
  min-width: 80px;
  max-width: 160px;
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

.canvas-toolbar__separator {
  width: 1px;
  height: 20px;
  background: var(--color-border);
  margin: 0 var(--space-1);
}

.canvas-toolbar__btn--active {
  background: var(--color-surface-hover);
  color: var(--color-accent);
}

.canvas-toolbar__search-group {
  display: flex;
  align-items: center;
  gap: var(--space-0-5);
}

.canvas-toolbar__search-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.canvas-toolbar__search-input {
  width: 160px;
  padding: var(--space-0-5) var(--space-1);
  padding-right: var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-canvas);
  color: var(--color-fg);
  font-size: var(--font-size-xs);
}

.canvas-toolbar__search-input:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent);
}

.canvas-toolbar__search-clear {
  position: absolute;
  right: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.canvas-toolbar__search-clear:hover {
  color: var(--color-fg);
}

.canvas-toolbar__icon-sm {
  width: 14px;
  height: 14px;
}
</style>
