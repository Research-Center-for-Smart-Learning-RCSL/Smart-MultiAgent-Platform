<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SPageHeader from '@shared/ui/SPageHeader.vue'
import SCard from '@shared/ui/SCard.vue'
import SSkeleton from '@shared/ui/SSkeleton.vue'
import SAlert from '@shared/ui/SAlert.vue'
import SEmptyState from '@shared/ui/SEmptyState.vue'
import { useDashboardSummary } from '../composables/useDashboardSummary'
import { useDashboardTimeseries } from '../composables/useDashboardTimeseries'
import { useDashboardWatchlist } from '../composables/useDashboardWatchlist'
import type { TimeWindow, BucketSize } from '@shared/api-client'
import { useProjectSocket } from '../composables/useProjectSocket'
import RoomStatusCard from '../components/RoomStatusCard.vue'
import OutputChart from '../components/OutputChart.vue'
import StudentWatchlist from '../components/StudentWatchlist.vue'
import DashboardAlerts from '../components/DashboardAlerts.vue'
import TimeWindowSelector from '../components/TimeWindowSelector.vue'

const { t } = useI18n()
const route = useRoute()

const projectId = computed(() => String(route.params.projectId ?? ''))
const timeWindow = ref<TimeWindow>('1h')
const bucketSize = ref<BucketSize>('5m')

const pid = () => projectId.value
const {
  data: summary,
  isLoading: summaryLoading,
  error: summaryError,
} = useDashboardSummary(pid)
const {
  data: timeseries,
  isLoading: tsLoading,
} = useDashboardTimeseries(
  pid,
  () => timeWindow.value,
  () => bucketSize.value,
)
const {
  data: watchlist,
  isLoading: wlLoading,
} = useDashboardWatchlist(pid)

useProjectSocket(pid)

const rooms = computed(() => summary.value?.rooms ?? [])
const buckets = computed(() => timeseries.value?.buckets ?? [])
const watchlistEntries = computed(() => watchlist.value?.entries ?? [])
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-6">
    <SPageHeader :title="t('dashboard.title')" />

    <SAlert
      v-if="summaryError"
      variant="danger"
    >
      {{ (summaryError as Error).message }}
    </SAlert>

    <template v-if="summaryLoading">
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <SSkeleton
          v-for="i in 3"
          :key="i"
          height="8rem"
        />
      </div>
    </template>

    <template v-else-if="rooms.length === 0">
      <SEmptyState :text="t('dashboard.noRooms')" />
    </template>

    <template v-else>
      <section>
        <h2 class="text-lg font-semibold text-[var(--color-fg)] mb-3">
          {{ t('dashboard.alerts') }}
        </h2>
        <DashboardAlerts :rooms="rooms" />
      </section>

      <section>
        <h2 class="text-lg font-semibold text-[var(--color-fg)] mb-3">
          {{ t('dashboard.roomStatus') }}
        </h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <RoomStatusCard
            v-for="room in rooms"
            :key="room.room_id"
            :room="room"
          />
        </div>
      </section>

      <section>
        <SCard class="p-4">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-lg font-semibold text-[var(--color-fg)]">
              {{ t('dashboard.timeseries') }}
            </h2>
            <TimeWindowSelector v-model="timeWindow" />
          </div>
          <SSkeleton
            v-if="tsLoading"
            height="13rem"
          />
          <OutputChart
            v-else
            :buckets="buckets"
            :rooms="rooms"
          />
        </SCard>
      </section>

      <section>
        <SCard class="p-4">
          <h2 class="text-lg font-semibold text-[var(--color-fg)] mb-4">
            {{ t('dashboard.watchlist') }}
          </h2>
          <SSkeleton
            v-if="wlLoading"
            height="8rem"
          />
          <StudentWatchlist
            v-else
            :entries="watchlistEntries"
          />
        </SCard>
      </section>
    </template>
  </div>
</template>
