<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTheme } from '@shared/composables'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  type ChartData,
  type ChartOptions,
} from 'chart.js'
import type { TimeseriesBucket, RoomSummary } from '../types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend)

const props = defineProps<{
  buckets: TimeseriesBucket[]
  rooms: RoomSummary[]
}>()

const { t } = useI18n()

const ROOM_COLORS = [
  'rgb(59, 130, 246)',
  'rgb(16, 185, 129)',
  'rgb(245, 158, 11)',
  'rgb(239, 68, 68)',
  'rgb(139, 92, 246)',
  'rgb(236, 72, 153)',
  'rgb(14, 165, 233)',
]

const roomMap = computed(() => {
  const map = new Map<string, string>()
  props.rooms.forEach((r) => map.set(r.room_id, r.room_name))
  return map
})

const chartData = computed<ChartData<'line'>>(() => {
  const bucketsByRoom = new Map<string, Map<string, number>>()
  const allBuckets = new Set<string>()

  for (const b of props.buckets) {
    allBuckets.add(b.bucket)
    if (!bucketsByRoom.has(b.room_id)) bucketsByRoom.set(b.room_id, new Map())
    bucketsByRoom.get(b.room_id)!.set(b.bucket, b.count)
  }

  const labels = [...allBuckets].sort()
  const datasets = [...bucketsByRoom.entries()].map(([roomId, data], i) => ({
    label: roomMap.value.get(roomId) || roomId.slice(0, 8),
    data: labels.map((l) => data.get(l) || 0),
    borderColor: ROOM_COLORS[i % ROOM_COLORS.length],
    backgroundColor: 'transparent',
    tension: 0.3,
    pointRadius: 2,
  }))

  return {
    labels: labels.map((l) =>
      new Date(l).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    ),
    datasets,
  }
})

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const { theme } = useTheme()

const chartOptions = computed<ChartOptions<'line'>>(() => {
  // Force recomputation when theme changes
  void theme.value
  const fg = token('--color-fg')
  const muted = token('--color-muted')
  const grid = token('--color-border-subtle')

  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: { color: fg },
      },
      title: {
        display: true,
        text: t('dashboard.chartTitle'),
        color: fg,
      },
    },
    scales: {
      x: {
        ticks: { color: muted },
        grid: { color: grid },
      },
      y: {
        beginAtZero: true,
        ticks: { color: muted, precision: 0 },
        grid: { color: grid },
      },
    },
  }
})
</script>

<template>
  <div class="min-h-[200px] w-full">
    <Line
      :data="chartData"
      :options="chartOptions"
    />
  </div>
</template>
