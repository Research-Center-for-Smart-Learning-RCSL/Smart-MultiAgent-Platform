<template>
  <div class="obs-panel">
    <ul class="obs-panel__roster">
      <li
        v-for="a in roster"
        :key="a.id"
        class="obs-panel__roster-item"
        v-bind="a.detail ? { title: a.detail } : {}"
      >
        <span class="obs-panel__roster-name">{{ a.name }}</span>
        <span
          class="obs-panel__roster-status"
          :class="`obs-panel__roster-status--${a.status}`"
        >{{ a.label }}</span>
      </li>
    </ul>

    <SDivider />

    <div
      v-if="loading"
      class="obs-panel__loading"
    >
      <SSkeleton
        variant="rect"
        height="72px"
      />
      <SSkeleton
        variant="rect"
        height="72px"
      />
    </div>

    <SEmptyState
      v-else-if="!observations.length"
      :icon="EyeIcon"
      :title="t('conversation.observers.emptyTitle')"
      :text="t('conversation.observers.emptyText')"
    />

    <ul
      v-else
      class="obs-panel__list"
    >
      <ObservationCard
        v-for="o in observations"
        :key="o.id"
        :observation="o"
        :agent-name="nameFor(o.agent_id)"
        @release="emit('release', o)"
        @delete="emit('delete', o)"
      />
    </ul>

    <div
      v-if="hasMore"
      class="obs-panel__more"
    >
      <SButton
        variant="ghost"
        size="sm"
        :loading="loadingMore"
        @click="emit('load-earlier')"
      >
        {{ t('conversation.observers.loadEarlier') }}
      </SButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { EyeIcon } from '@heroicons/vue/24/outline'
import { SButton, SDivider, SEmptyState, SSkeleton } from '@shared/ui'
import { AGENT_ERROR_FALLBACK_KEY, AGENT_ERROR_MESSAGE_KEYS } from '../constants/agentErrors'
import ObservationCard from './ObservationCard.vue'
import type { ObserverEntry } from '../composables/useObservations'
import type { Observation } from '../types'

const props = defineProps<{
  observerAgents: ObserverEntry[]
  observations: Observation[]
  loading: boolean
  hasMore: boolean
  loadingMore: boolean
  agentNames: Record<string, string>
}>()

const emit = defineEmits<{
  release: [observation: Observation]
  delete: [observation: Observation]
  'load-earlier': []
}>()

const { t, te } = useI18n()

function nameFor(agentId: string): string {
  return props.agentNames[agentId] ?? agentId.slice(0, 8)
}

// W-4 (B.3): the roster shows a short status label inline (kept narrow so it
// never overflows the rail) and the full kind sentence in the row tooltip.
// Failure kinds mirror agent.finished — reuse the room path's kind→label map;
// benign skips get their own muted copy, never the error one.
function detailFor(a: ObserverEntry): string {
  if (a.status === 'error') {
    return t(AGENT_ERROR_MESSAGE_KEYS[a.errorReason ?? ''] ?? AGENT_ERROR_FALLBACK_KEY)
  }
  if (a.status === 'skipped' && a.skipReason) {
    const key = `conversation.observers.skip.${a.skipReason}`
    return te(key) ? t(key) : ''
  }
  return ''
}

const roster = computed(() =>
  props.observerAgents.map((a) => ({
    id: a.id,
    name: a.name,
    status: a.status,
    label: t(`conversation.observers.status.${a.status}`),
    detail: detailFor(a),
  })),
)
</script>

<style scoped>
.obs-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  height: 100%;
  overflow-y: auto;
}

.obs-panel__roster {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.obs-panel__roster-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 32px;
}

.obs-panel__roster-name {
  font-size: 13px;
  color: var(--color-fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.obs-panel__roster-status {
  font-size: 11px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.obs-panel__roster-status--analyzing {
  color: var(--color-accent);
}

.obs-panel__roster-status--error {
  color: var(--color-danger);
}

.obs-panel__roster-status--skipped {
  color: var(--color-muted);
  font-style: italic;
}

.obs-panel__loading {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.obs-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.obs-panel__more {
  display: flex;
  justify-content: center;
}
</style>
