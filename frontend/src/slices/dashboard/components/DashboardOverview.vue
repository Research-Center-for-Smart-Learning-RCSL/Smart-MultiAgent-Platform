<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  UserGroupIcon,
} from '@heroicons/vue/24/outline'
import { formatPercent } from '@shared/utils/number'
import { SAlert, SStatTile } from '@shared/ui'
import type { RoomSummary, WatchlistEntry } from '../types'
import type { DashboardSummaryOut } from '@shared/api-client'

const props = defineProps<{
  rooms: RoomSummary[]
  watchlistEntries: WatchlistEntry[]
  watchlistLoading: boolean
  watchlistError: Error | null
  thresholds: DashboardSummaryOut['thresholds'] | undefined
}>()

const { t, locale } = useI18n()
const activeRounds = computed(
  () => props.rooms.filter((room) => room.round?.state === 'active').length,
)
const validCount = computed(() => props.rooms.reduce((sum, room) => sum + room.valid_count, 0))
const submissionCount = computed(() =>
  props.rooms.reduce((sum, room) => sum + room.total_submissions, 0),
)
const validityHint = computed(() => {
  const percent = formatPercent(validCount.value, submissionCount.value, locale.value)
  return percent
    ? t('dashboard.validityHint', { percent })
    : t('dashboard.noValidityData')
})
const stalledRooms = computed(() => props.rooms.filter((room) => room.status === 'red'))
const attentionCount = computed(
  () => props.watchlistEntries.filter((entry) => entry.needs_attention).length,
)
</script>

<template>
  <section
    class="grid grid-cols-2 gap-3 lg:grid-cols-4"
    :aria-label="t('dashboard.classSummary')"
  >
    <SStatTile
      :label="t('dashboard.activeRounds')"
      :value="`${activeRounds} / ${rooms.length}`"
      :icon="ClockIcon"
    />
    <SStatTile
      :label="t('dashboard.validThisRound')"
      :value="validCount"
      :hint="validityHint"
      :icon="CheckCircleIcon"
      tone="success"
    />
    <SStatTile
      :label="t('dashboard.stalledRooms')"
      :value="stalledRooms.length"
      :icon="ExclamationTriangleIcon"
      :tone="stalledRooms.length ? 'danger' : 'neutral'"
    />
    <SStatTile
      :label="t('dashboard.participantsNeedAttention')"
      :value="watchlistLoading || watchlistError ? t('dashboard.unavailable') : attentionCount"
      :icon="UserGroupIcon"
      :tone="attentionCount ? 'warning' : 'neutral'"
    />
  </section>

  <SAlert
    v-if="stalledRooms.length"
    variant="danger"
    :title="t('dashboard.attentionRequired')"
  >
    <ul class="list-disc space-y-1 pl-5">
      <li
        v-for="room in stalledRooms"
        :key="room.room_id"
      >
        {{ t('dashboard.alertStalled', { room: room.room_name, minutes: thresholds?.yellow_minutes ?? 15 }) }}
        <RouterLink
          class="ml-1 font-medium underline"
          :to="{ name: 'conversation.chatroom', params: { chatroomId: room.room_id } }"
        >
          {{ t('dashboard.openRoom') }}
        </RouterLink>
      </li>
    </ul>
  </SAlert>
  <p
    v-else
    class="text-sm text-[var(--color-muted)]"
    role="status"
  >
    {{ t('dashboard.noStalledRooms') }}
  </p>
</template>
