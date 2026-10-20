<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useChartTheme } from '@shared/composables'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  type ChartData,
  type ChartOptions,
} from 'chart.js'
import type { BucketSize, TimeWindow } from '@shared/api-client'
import type { TimeseriesBucket, RoomSummary } from '../types'
import { buildDashboardChartData } from '../chartData'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend)

const props = defineProps<{
  buckets: TimeseriesBucket[]
  rooms: RoomSummary[]
  window: TimeWindow
  bucket: BucketSize
}>()

const { locale } = useI18n()
const { tickColor, gridColor, legendColor, seriesColor } = useChartTheme()

const chartData = computed<ChartData<'line'>>(() =>
  buildDashboardChartData(props, locale.value, seriesColor),
)

const chartOptions = computed<ChartOptions<'line'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { intersect: false, mode: 'index' },
  plugins: {
    legend: {
      position: 'bottom',
      labels: {
        color: legendColor.value,
        usePointStyle: true,
        pointStyle: 'circle',
        boxWidth: 8,
        boxHeight: 8,
      },
    },
  },
  scales: {
    x: {
      ticks: { color: tickColor.value, maxTicksLimit: 6, autoSkip: true },
      grid: { color: gridColor.value },
    },
    y: {
      beginAtZero: true,
      ticks: { color: tickColor.value, precision: 0 },
      grid: { color: gridColor.value },
    },
  },
}))
</script>

<template>
  <div class="min-h-[200px] w-full">
    <Line
      :data="chartData"
      :options="chartOptions"
    />
  </div>
</template>
