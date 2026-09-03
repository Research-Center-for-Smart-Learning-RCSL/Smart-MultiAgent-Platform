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

    <!-- F-10: the alert claims an unbinding happened, so it may only speak when
         the roster is actually known. An empty `observerAgents` also means the
         bound-agents query has not answered yet, or failed outright (it retries
         never), or is serving a cache no event invalidated. -->
    <SAlert
      v-if="rosterKnown && !roster.length && observations.length"
      variant="info"
      :title="t('conversation.observers.noObserverBoundTitle')"
    >
      {{ t('conversation.observers.noObserverBoundText') }}
    </SAlert>

    <SDivider />

    <!-- F-7. The banner sits beside the list rather than replacing it: a failed
         background refetch still holds the rows it fetched last time, and
         blanking them would destroy information to report a transport problem.
         What must not survive an error is the EMPTY state, whose copy asserts
         the room has produced nothing — a fact a failed request never
         established. Hence the `!isError` guard on that branch alone. -->
    <SQueryError
      v-if="isError"
      :message="t('conversation.observers.loadError')"
      :retry-label="t('conversation.observers.retry')"
      @retry="emit('retry')"
    />

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

    <ul
      v-else-if="observations.length"
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

    <SEmptyState
      v-else-if="!isError"
      :icon="EyeIcon"
      :title="t('conversation.observers.emptyTitle')"
      :text="t('conversation.observers.emptyText')"
    />

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
import { SAlert, SButton, SDivider, SEmptyState, SQueryError, SSkeleton } from '@shared/ui'
import { agentErrorMessageKey } from '../constants/agentErrors'
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
  /** The observations query failed (F-7). */
  isError: boolean
  /** The bound-agents query has settled successfully, so an empty
   *  `observerAgents` genuinely means "nothing bound" (F-10). */
  rosterKnown: boolean
}>()

const emit = defineEmits<{
  release: [observation: Observation]
  delete: [observation: Observation]
  'load-earlier': []
  retry: []
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
  // F-6: 'unknown' names an absent feed, not a worker state, so the tooltip has
  // to say why — otherwise it reads as a third kind of failure.
  if (a.status === 'unknown') {
    return t('conversation.observers.unknownStatusHint')
  }
  if (a.status === 'error') {
    return t(agentErrorMessageKey(a.errorReason))
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
  gap: var(--space-3);
  padding: var(--space-3);
  height: 100%;
  overflow-y: auto;
}

.obs-panel__roster {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.obs-panel__roster-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-height: 32px;
}

.obs-panel__roster-name {
  font-size: var(--font-size-code);
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

/* Deliberately not the error colour: nothing failed, the reader simply has no
   feed. Dotted underline marks it as the one label carrying an explanation. */
.obs-panel__roster-status--unknown {
  color: var(--color-muted);
  text-decoration: underline dotted;
  text-underline-offset: 2px;
}

.obs-panel__loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.obs-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2-5);
}

.obs-panel__more {
  display: flex;
  justify-content: center;
}
</style>
