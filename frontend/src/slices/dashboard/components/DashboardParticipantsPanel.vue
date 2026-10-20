<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SCard, SEmptyState, SQueryError, SSkeleton } from '@shared/ui'
import StudentWatchlist from './StudentWatchlist.vue'
import type { RoomSummary, WatchlistEntry } from '../types'

defineProps<{
  entries: WatchlistEntry[]
  rooms: Pick<RoomSummary, 'room_id' | 'room_name'>[]
  isLoading: boolean
  error: Error | null
  idleMinutes: number
}>()
const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
</script>

<template>
  <section aria-labelledby="dashboard-all-participants-heading">
    <SCard class="p-4 sm:p-5">
      <h2
        id="dashboard-all-participants-heading"
        class="mb-4 text-lg font-semibold text-[var(--color-fg)]"
      >
        {{ t('dashboard.allParticipants') }}
      </h2>
      <SQueryError
        v-if="error"
        :message="error.message"
        :retry-label="t('dashboard.retry')"
        @retry="emit('retry')"
      />
      <SSkeleton
        v-else-if="isLoading"
        height="12rem"
      />
      <StudentWatchlist
        v-else-if="entries.length"
        :entries="entries"
        :rooms="rooms"
        :idle-minutes="idleMinutes"
      />
      <SEmptyState
        v-else
        :text="t('dashboard.watchlistEmpty')"
      />
    </SCard>
  </section>
</template>
