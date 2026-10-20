<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SCard, SQueryError, SSelect, SSkeleton } from '@shared/ui'
import StudentWatchlist from './StudentWatchlist.vue'
import type { RoomSummary, WatchlistEntry } from '../types'

defineProps<{
  rooms: Pick<RoomSummary, 'room_id' | 'room_name'>[]
  attentionEntries: WatchlistEntry[]
  attentionCount: number
  roomFilter: string
  roomFilterOptions: { value: string; label: string }[]
  isLoading: boolean
  error: Error | null
  idleMinutes: number
}>()
const emit = defineEmits<{
  retry: []
  'update:roomFilter': [value: string]
}>()
const { t } = useI18n()
</script>

<template>
  <aside class="md:col-span-4">
    <SCard class="h-full p-4">
      <div class="mb-3 flex items-start justify-between gap-3">
        <h2 class="text-lg font-semibold text-[var(--color-fg)]">
          {{ t('dashboard.participantsNeedAttention') }}
        </h2>
        <span class="rounded-full bg-[var(--color-warning-tint)] px-2 py-1 text-xs font-medium text-[var(--color-warning-on)]">
          {{ attentionCount }}
        </span>
      </div>
      <label
        for="dashboard-room-filter"
        class="mb-1 block text-xs font-medium text-[var(--color-muted)]"
      >
        {{ t('dashboard.filterRoom') }}
      </label>
      <SSelect
        id="dashboard-room-filter"
        class="mb-4 w-full"
        :model-value="roomFilter"
        :options="roomFilterOptions"
        @update:model-value="emit('update:roomFilter', String($event))"
      />
      <SQueryError
        v-if="error"
        :message="error.message"
        :retry-label="t('dashboard.retry')"
        @retry="emit('retry')"
      />
      <SSkeleton
        v-else-if="isLoading"
        height="11rem"
      />
      <StudentWatchlist
        v-else-if="attentionEntries.length"
        :entries="attentionEntries"
        :rooms="rooms"
        :idle-minutes="idleMinutes"
      />
      <p
        v-else
        class="py-4 text-sm text-[var(--color-muted)]"
      >
        {{ t('dashboard.noParticipantsNeedAttention') }}
      </p>
    </SCard>
  </aside>
</template>
