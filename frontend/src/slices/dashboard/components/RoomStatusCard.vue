<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import {
  ChatBubbleLeftRightIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  MinusCircleIcon,
  PauseCircleIcon,
  UserGroupIcon,
} from '@heroicons/vue/24/outline'
import { formatNumber } from '@shared/utils/number'
import { SCard, SProgressBar, SRelativeTime } from '@shared/ui'
import type { RoomSummary } from '../types'

const props = defineProps<{
  room: RoomSummary
}>()

const { t, locale } = useI18n()

const statusLabels: Record<RoomSummary['status'], string> = {
  green: 'dashboard.statusGreen',
  yellow: 'dashboard.statusYellow',
  red: 'dashboard.statusRed',
  inactive: 'dashboard.statusInactive',
}
const statusIcons: Record<RoomSummary['status'], Component> = {
  green: CheckCircleIcon,
  yellow: PauseCircleIcon,
  red: ExclamationTriangleIcon,
  inactive: MinusCircleIcon,
}
const statusLabel = computed(() => t(statusLabels[props.room.status]))
const statusIcon = computed(() => statusIcons[props.room.status])
const statusRailClass = computed(() => `room-status-card--${props.room.status}`)
const progress = computed(() =>
  props.room.total_submissions > 0
    ? (props.room.valid_count / props.room.total_submissions) * 100
    : 0,
)
const progressVariant = computed(() => {
  if (props.room.status === 'red') return 'danger' as const
  if (props.room.status === 'yellow') return 'warning' as const
  return 'success' as const
})
const messageTotal = computed(() => props.room.message_counts.human + props.room.message_counts.agent)
const humanMessagePercent = computed(() =>
  messageTotal.value > 0 ? (props.room.message_counts.human / messageTotal.value) * 100 : 0,
)
const agentMessagePercent = computed(() =>
  messageTotal.value > 0 ? (props.room.message_counts.agent / messageTotal.value) * 100 : 0,
)
const messageSplitLabel = computed(() =>
  t('dashboard.messageSplitLabel', {
    human: formatNumber(props.room.message_counts.human, locale.value),
    agent: formatNumber(props.room.message_counts.agent, locale.value),
  }),
)
</script>

<template>
  <RouterLink
    class="block rounded-md text-inherit no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
    :to="{ name: 'conversation.chatroom', params: { chatroomId: room.room_id } }"
  >
    <SCard
      class="room-status-card h-full p-4"
      :class="statusRailClass"
    >
      <div class="mb-3 flex items-start justify-between gap-3">
        <h3 class="min-w-0 truncate text-sm font-semibold text-[var(--color-fg)]">
          {{ room.room_name }}
        </h3>
        <span class="flex shrink-0 items-center gap-1.5 text-xs font-medium text-[var(--color-muted)]">
          <component
            :is="statusIcon"
            class="h-4 w-4"
            aria-hidden="true"
          />
          {{ statusLabel }}
        </span>
      </div>

      <div class="flex items-baseline justify-between gap-3 text-xs">
        <span class="text-[var(--color-muted)]">{{ t('dashboard.roundValidity') }}</span>
        <span class="font-semibold tabular-nums text-[var(--color-fg)]">
          {{ formatNumber(room.valid_count, locale) }} / {{ formatNumber(room.total_submissions, locale) }}
        </span>
      </div>
      <SProgressBar
        class="mt-2"
        :value="progress"
        :variant="progressVariant"
        size="sm"
        :aria-label="t('dashboard.roundValidity')"
      />

      <div class="mt-4 flex items-center justify-between gap-3 text-xs">
        <span class="text-[var(--color-muted)]">{{ t('dashboard.lastValidSubmission') }}</span>
        <SRelativeTime
          v-if="room.last_valid_submission_at"
          class="text-right text-[var(--color-fg)]"
          :value="room.last_valid_submission_at"
        />
        <span
          v-else
          class="text-[var(--color-muted)]"
        >{{ t('dashboard.never') }}</span>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-muted)]">
        <span class="inline-flex items-center gap-1">
          <UserGroupIcon
            class="h-4 w-4"
            aria-hidden="true"
          />
          <span>{{ room.online_count === null ? t('dashboard.unavailableShort') : t('dashboard.onlineCount', { count: formatNumber(room.online_count, locale) }) }}</span>
        </span>
        <span>{{ t('dashboard.participantCount', { count: formatNumber(room.participant_count, locale) }) }}</span>
      </div>

      <div class="mt-3">
        <div class="mb-1 flex items-center justify-between gap-2 text-xs">
          <span class="inline-flex items-center gap-1 text-[var(--color-muted)]">
            <ChatBubbleLeftRightIcon
              class="h-4 w-4"
              aria-hidden="true"
            />
            {{ t('dashboard.messages') }}
          </span>
          <span class="text-right tabular-nums text-[var(--color-fg)]">
            {{ t('dashboard.humanMessages', { count: formatNumber(room.message_counts.human, locale) }) }}
            ·
            {{ t('dashboard.agentMessages', { count: formatNumber(room.message_counts.agent, locale) }) }}
          </span>
        </div>
        <div
          class="flex h-2 overflow-hidden rounded-full bg-[var(--color-neutral-tint)]"
          role="img"
          :aria-label="messageSplitLabel"
        >
          <span
            class="rounded-l-full bg-[var(--color-accent)]"
            :style="{ width: `${humanMessagePercent}%` }"
          />
          <span
            class="rounded-r-full bg-[var(--color-chart-3)]"
            :style="{ width: `${agentMessagePercent}%` }"
          />
        </div>
      </div>
    </SCard>
  </RouterLink>
</template>

<style scoped>
.room-status-card {
  border-left: 3px solid var(--color-border-strong);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.room-status-card--green {
  border-left-color: var(--color-success);
}

.room-status-card--yellow {
  border-left-color: var(--color-warning);
}

.room-status-card--red {
  border-left-color: var(--color-danger);
}

.room-status-card--inactive {
  border-left-color: var(--color-border-strong);
}

a:hover .room-status-card {
  box-shadow: var(--shadow-md);
}
</style>
