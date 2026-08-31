<template>
  <section
    ref="panelRef"
    class="search-panel"
    role="search"
    :aria-label="t('conversation.chatroom.search')"
    tabindex="-1"
  >
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
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { SSearchInput, SButton, SLoadingSpinner } from '@shared/ui'
import { useFocusTrap } from '@shared/composables'
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

const panelRef = ref<HTMLElement | null>(null)
// The panel is `v-if`-mounted only while open, so `isOpen` is constant and the
// trap's immediate watch does the work: focus lands on the search field, which
// is the first focusable child, satisfying the documented auto-focus. Neither
// the scroll lock nor the restoration applies — this is an in-page panel whose
// backdrop and focus return are owned by the view's surface coordinator, which
// is the only place that can tell a close from a hand-off to another surface.
const { trapTab } = useFocusTrap(panelRef, () => true, {
  lockScroll: false,
  restoreFocus: false,
})

// Attached imperatively rather than with `@keydown`: gate #11's
// `no-static-element-interactions` rejects a key handler on a non-interactive
// element, and this panel is a container, not a control. Mounted only while
// open, so there is no open/closed state to track here.
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    emit('close')
    return
  }
  trapTab(e)
}

onMounted(() => panelRef.value?.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => panelRef.value?.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.search-panel {
  position: absolute;
  /* Flush with the containing block, which is `.chatroom__feed` -- grid-row 2,
     so it already starts below the 48px header. Offsetting by the header here
     would count it twice. */
  top: 0;
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
  padding: var(--space-4);
  max-height: 50vh;
  overflow-y: auto;
}

/* 07-conversation.md:749 — slides down from below the header over the
   documented 200ms token. The classes are applied by the view's <Transition>,
   but they live here so the panel's motion sits with the panel's geometry. */
.search-panel-enter-active,
.search-panel-leave-active {
  transition:
    transform var(--transition-normal),
    opacity var(--transition-normal);
}

.search-panel-enter-from,
.search-panel-leave-to {
  transform: translateY(-100%);
  opacity: 0;
}

/* R24.49: motion is removed, not shortened — the panel still appears and still
   takes focus, it just does not travel. */
@media (prefers-reduced-motion: reduce) {
  .search-panel-enter-active,
  .search-panel-leave-active {
    transition: none;
  }
}

.search-panel__bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.search-panel__input {
  flex: 1;
}

.search-panel__count {
  margin-top: var(--space-3);
  font-size: var(--font-size-code);
  color: var(--color-muted);
}

.search-panel__spinner {
  margin-top: var(--space-3);
}

.search-panel__results {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.result {
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-2);
  cursor: pointer;
}

.result:hover {
  background: var(--color-surface);
}

.result__meta {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.result__snippet {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.result__snippet :deep(mark) {
  background: var(--color-warning-tint);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-0-5);
}

.search-panel__empty {
  margin-top: var(--space-3);
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
</style>
