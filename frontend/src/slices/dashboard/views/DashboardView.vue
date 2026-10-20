<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  SEmptyState,
  SPageHeader,
  SQueryError,
  SRelativeTime,
  SSkeleton,
} from '@shared/ui'
import { useDashboardViewModel } from '../composables/useDashboardViewModel'
import RoomStatusCard from '../components/RoomStatusCard.vue'
import ResearchExportButton from '../components/ResearchExportButton.vue'
import DashboardOverview from '../components/DashboardOverview.vue'
import DashboardAttentionPanel from '../components/DashboardAttentionPanel.vue'
import DashboardChartPanel from '../components/DashboardChartPanel.vue'
import DashboardParticipantsPanel from '../components/DashboardParticipantsPanel.vue'

const route = useRoute()
const { t } = useI18n()
const workspaceId = computed(() => String(route.params.workspaceId ?? ''))
const {
  connected,
  workspace,
  workspaceError,
  refetchWorkspace,
  canExport,
  roleDecided,
  rooms,
  sortedRooms,
  buckets,
  watchlistEntries,
  thresholds,
  summaryLoading,
  summaryError,
  summaryUpdatedAt,
  refetchSummary,
  timeseriesLoading,
  timeseriesError,
  refetchTimeseries,
  watchlistLoading,
  watchlistError,
  refetchWatchlist,
  roomFilter,
  roomFilterOptions,
  filteredEntries,
  attentionEntries,
  timeWindow,
  bucketSize,
  attentionCount,
} = useDashboardViewModel(workspaceId)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6">
    <SPageHeader
      :title="t('dashboard.title')"
      :subtitle="workspace?.name ?? ''"
    >
      <template #actions>
        <div class="flex flex-wrap items-center gap-3">
          <div
            class="flex items-center gap-2 text-xs text-[var(--color-muted)]"
            role="status"
            :aria-label="connected ? t('dashboard.live') : t('dashboard.reconnecting')"
          >
            <span
              class="h-2 w-2 rounded-full"
              :class="connected ? 'bg-[var(--color-success)]' : 'bg-[var(--color-muted)]'"
              aria-hidden="true"
            />
            <span>{{ connected ? t('dashboard.live') : t('dashboard.reconnecting') }}</span>
            <span aria-hidden="true">·</span>
            <SRelativeTime
              v-if="summaryUpdatedAt"
              :value="summaryUpdatedAt"
            />
            <span v-else>{{ t('dashboard.waitingForUpdate') }}</span>
          </div>
          <ResearchExportButton
            v-if="roleDecided && canExport"
            :workspace-id="workspaceId"
          />
        </div>
      </template>
    </SPageHeader>

    <SQueryError
      v-if="workspaceError"
      :message="workspaceError.message"
      :retry-label="t('dashboard.retry')"
      @retry="refetchWorkspace()"
    />

    <template v-if="summaryLoading">
      <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SSkeleton
          v-for="index in 4"
          :key="`kpi-${index}`"
          height="6.5rem"
        />
      </div>
      <div class="grid grid-cols-1 gap-4 md:grid-cols-12">
        <SSkeleton
          class="md:col-span-8"
          height="18rem"
        />
        <SSkeleton
          class="md:col-span-4"
          height="18rem"
        />
      </div>
      <SSkeleton height="17rem" />
      <SSkeleton height="14rem" />
    </template>

    <SQueryError
      v-else-if="summaryError"
      :message="summaryError.message"
      :retry-label="t('dashboard.retry')"
      @retry="refetchSummary()"
    />

    <SEmptyState
      v-else-if="rooms.length === 0"
      :text="t('dashboard.noRooms')"
    />

    <template v-else>
      <DashboardOverview
        :rooms="rooms"
        :watchlist-entries="watchlistEntries"
        :watchlist-loading="watchlistLoading"
        :watchlist-error="watchlistError"
        :thresholds="thresholds"
      />

      <div class="grid grid-cols-1 gap-4 md:grid-cols-12">
        <section
          class="space-y-3 md:col-span-8"
          :aria-labelledby="'dashboard-room-status-heading'"
        >
          <h2
            id="dashboard-room-status-heading"
            class="text-lg font-semibold text-[var(--color-fg)]"
          >
            {{ t('dashboard.roomStatus') }}
          </h2>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <RoomStatusCard
              v-for="room in sortedRooms"
              :key="room.room_id"
              :room="room"
            />
          </div>
        </section>

        <DashboardAttentionPanel
          :rooms="rooms"
          :attention-entries="attentionEntries"
          :attention-count="attentionCount"
          :room-filter="roomFilter"
          :room-filter-options="roomFilterOptions"
          :is-loading="watchlistLoading"
          :error="watchlistError"
          :idle-minutes="thresholds?.idle_minutes ?? 10"
          @update:room-filter="roomFilter = $event"
          @retry="refetchWatchlist()"
        />
      </div>

      <DashboardChartPanel
        v-model="timeWindow"
        :buckets="buckets"
        :rooms="rooms"
        :bucket="bucketSize"
        :is-loading="timeseriesLoading"
        :error="timeseriesError"
        @retry="refetchTimeseries()"
      />

      <DashboardParticipantsPanel
        :entries="filteredEntries"
        :rooms="rooms"
        :is-loading="watchlistLoading"
        :error="watchlistError"
        :idle-minutes="thresholds?.idle_minutes ?? 10"
        @retry="refetchWatchlist()"
      />
    </template>
  </div>
</template>
