<template>
  <div class="search-panel">
    <div class="search-panel__bar">
      <SSearchInput
        :model-value="query"
        :placeholder="t('conversation.chatroom.searchPlaceholder')"
        :loading="searching"
        class="search-panel__input"
        @update:model-value="emit('update:query', $event)"
        @search="emit('search')"
      />
      <SButton
        variant="ghost"
        icon-only
        size="sm"
        :aria-label="t('conversation.chatroom.close')"
        @click="emit('close')"
      >
        <XMarkIcon class="w-5 h-5" />
      </SButton>
    </div>

    <p
      v-if="query && !searching"
      class="search-panel__count"
    >
      {{ t('conversation.chatroom.searchResultsCount', { count: hits.length, query }) }}
    </p>

    <SLoadingSpinner
      v-if="searching"
      size="sm"
      class="search-panel__spinner"
    />

    <ul
      v-else-if="hits.length"
      class="search-panel__results"
    >
      <li
        v-for="h in hits"
        :key="h.message_id"
      >
        <button
          type="button"
          class="result"
          @click="emit('select', h)"
        >
          <span class="result__meta">
            {{ h.sender_id ? h.sender_id.slice(0, 8) : h.sender_type }} · {{ formatDateTime(h.created_at) }}
          </span>
          <!-- Snippet sanitised via sanitizeSnippet (eslint allowlist). -->
          <span
            class="result__snippet"
            v-html="renderedSnippets[h.message_id]"
          />
        </button>
      </li>
    </ul>

    <p
      v-else-if="query"
      class="search-panel__empty"
    >
      {{ t('conversation.chatroom.searchNoResults') }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { SSearchInput, SButton, SLoadingSpinner } from '@shared/ui'
import { formatDateTime } from '../utils/format'
import type { SearchHit } from '../types'

defineProps<{
  query: string
  hits: SearchHit[]
  renderedSnippets: Record<string, string>
  searching: boolean
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  search: []
  close: []
  select: [hit: SearchHit]
}>()

const { t } = useI18n()
</script>

<style scoped>
.search-panel {
  position: absolute;
  top: 48px;
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
  padding: 16px;
  max-height: 50vh;
  overflow-y: auto;
}

.search-panel__bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-panel__input {
  flex: 1;
}

.search-panel__count {
  margin-top: 12px;
  font-size: 13px;
  color: var(--color-muted);
}

.search-panel__spinner {
  margin-top: 12px;
}

.search-panel__results {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.result {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-md);
  padding: 8px;
  cursor: pointer;
}

.result:hover {
  background: var(--color-surface);
}

.result__meta {
  font-size: 12px;
  color: var(--color-muted);
}

.result__snippet {
  font-size: 14px;
  color: var(--color-fg);
}

.result__snippet :deep(mark) {
  background: var(--color-warning-tint, #fef3c7);
  border-radius: var(--radius-sm);
  padding: 0 2px;
}

.search-panel__empty {
  margin-top: 12px;
  font-size: 14px;
  color: var(--color-muted);
}
</style>
