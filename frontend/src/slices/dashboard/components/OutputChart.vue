<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
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

const isDark = computed(() => {
  return (
    document.documentElement.dataset.theme === 'dark' ||
    (!document.documentElement.dataset.theme &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  )
})

const chartOptions = computed<ChartOptions<'line'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      labels: { color: isDark.value ? '#e2e8f0' : '#334155' },
    },
    title: {
      display: true,
      text: t('dashboard.chartTitle'),
      color: isDark.value ? '#e2e8f0' : '#334155',
    },
  },
  scales: {
    x: {
      ticks: { color: isDark.value ? '#94a3b8' : '#64748b' },
      grid: { color: isDark.value ? '#334155' : '#e2e8f0' },
    },
    y: {
      beginAtZero: true,
      ticks: { color: isDark.value ? '#94a3b8' : '#64748b', precision: 0 },
      grid: { color: isDark.value ? '#334155' : '#e2e8f0' },
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
