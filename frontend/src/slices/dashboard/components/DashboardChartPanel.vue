<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BucketSize, TimeWindow } from '@shared/api-client'
import { SCard, SQueryError, SSkeleton, SSegmentedControl } from '@shared/ui'
import OutputChart from './OutputChart.vue'
import type { RoomSummary } from '../types'
import type { DashboardTimeseriesOut } from '@shared/api-client'

defineProps<{
  modelValue: TimeWindow
  buckets: DashboardTimeseriesOut['buckets']
  rooms: RoomSummary[]
  bucket: BucketSize
  isLoading: boolean
  error: Error | null
}>()
const emit = defineEmits<{
  'update:modelValue': [value: TimeWindow]
  retry: []
}>()
const { t } = useI18n()
const timeWindowOptions = computed(() => [
  { value: '1h', label: t('dashboard.timeWindow.1h') },
  { value: '6h', label: t('dashboard.timeWindow.6h') },
  { value: '24h', label: t('dashboard.timeWindow.24h') },
])
</script>

<template>
  <section aria-labelledby="dashboard-trend-heading">
    <SCard class="p-4 sm:p-5">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2
          id="dashboard-trend-heading"
          class="text-lg font-semibold text-[var(--color-fg)]"
        >
          {{ t('dashboard.timeseries') }}
        </h2>
        <SSegmentedControl
          :model-value="modelValue"
          :options="timeWindowOptions"
          :accessible-label="t('dashboard.timeWindowLabel')"
          @update:model-value="emit('update:modelValue', $event as TimeWindow)"
        />
      </div>
      <SQueryError
        v-if="error"
        :message="error.message"
        :retry-label="t('dashboard.retry')"
        @retry="emit('retry')"
      />
      <SSkeleton
        v-else-if="isLoading"
        height="13rem"
      />
      <OutputChart
        v-else
        :buckets="buckets"
        :rooms="rooms"
        :window="modelValue"
        :bucket="bucket"
      />
    </SCard>
  </section>
</template>
