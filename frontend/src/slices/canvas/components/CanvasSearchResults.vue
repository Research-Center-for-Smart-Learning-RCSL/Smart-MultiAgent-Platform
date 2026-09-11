<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  DocumentTextIcon,
  Square2StackIcon,
} from '@heroicons/vue/24/outline'
import type { SanitizedSearchResult } from '../composables/useCanvasSearch'
import SLoadingSpinner from '@shared/ui/SLoadingSpinner.vue'

const { t } = useI18n()

defineProps<{
  results: SanitizedSearchResult[]
  isSearching: boolean
}>()

const emit = defineEmits<{
  selectObject: [objectId: string, positionX: number, positionY: number]
}>()

function kindIcon(kind: string) {
  return kind === 'text' ? DocumentTextIcon : Square2StackIcon
}
</script>

<template>
  <div class="canvas-search-results">
    <div
      v-if="isSearching"
      class="canvas-search-results__loading"
    >
      <SLoadingSpinner />
    </div>
    <div
      v-else-if="results.length === 0"
      class="canvas-search-results__empty"
    >
      {{ t('canvas.searchNoResults') }}
    </div>
    <ul
      v-else
      class="canvas-search-results__list"
    >
      <li
        v-for="result in results"
        :key="result.object_id"
        class="canvas-search-results__item"
        role="button"
        tabindex="0"
        @click="emit('selectObject', result.object_id, result.position_x, result.position_y)"
        @keydown.enter="emit('selectObject', result.object_id, result.position_x, result.position_y)"
      >
        <component
          :is="kindIcon(result.kind)"
          class="canvas-search-results__icon"
        />
        <div class="canvas-search-results__content">
          <span
            class="canvas-search-results__snippet"
            v-html="result.sanitizedSnippet"
          />
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.canvas-search-results {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  box-shadow: var(--elevation-2, 0 4px 6px -1px rgb(0 0 0 / 0.1));
}

.canvas-search-results__loading,
.canvas-search-results__empty {
  padding: var(--space-3);
  text-align: center;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}

.canvas-search-results__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.canvas-search-results__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  border-bottom: 1px solid var(--color-border);
  transition: background 0.15s;
}

.canvas-search-results__item:last-child {
  border-bottom: none;
}

.canvas-search-results__item:hover,
.canvas-search-results__item:focus-visible {
  background: var(--color-surface-hover);
}

.canvas-search-results__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--color-muted);
}

.canvas-search-results__content {
  flex: 1;
  min-width: 0;
}

.canvas-search-results__snippet {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  line-height: 1.4;
}

.canvas-search-results__snippet :deep(mark) {
  background: var(--color-warning-bg, #fef3c7);
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}
</style>
