<template>
  <div class="obs-panel">
    <ul class="obs-panel__roster">
      <li
        v-for="a in observerAgents"
        :key="a.id"
        class="obs-panel__roster-item"
        v-bind="
          a.status === 'error' ? { title: t('conversation.observers.status.error') } : {}
        "
      >
        <span class="obs-panel__roster-name">{{ a.name }}</span>
        <span
          class="obs-panel__roster-status"
          :class="`obs-panel__roster-status--${a.status}`"
        >{{ t(`conversation.observers.status.${a.status}`) }}</span>
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
import { useI18n } from 'vue-i18n'
import { EyeIcon } from '@heroicons/vue/24/outline'
import { SButton, SDivider, SEmptyState, SSkeleton } from '@shared/ui'
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

const { t } = useI18n()

function nameFor(agentId: string): string {
  return props.agentNames[agentId] ?? agentId.slice(0, 8)
}
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
