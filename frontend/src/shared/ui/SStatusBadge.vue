<script setup lang="ts">
defineProps<{
  status: string
}>()

const colorMap: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700',
  waiting: 'bg-yellow-100 text-yellow-700',
  succeeded: 'bg-green-100 text-green-700',
  completed: 'bg-green-100 text-green-700',
  approved: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  rejected: 'bg-red-100 text-red-700',
  error: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-600',
  skipped: 'bg-gray-100 text-gray-600',
  pending: 'bg-gray-50 text-gray-400',
  idle: 'bg-gray-50 text-gray-400',
  timeout: 'bg-orange-100 text-orange-600',
  timeout_leader: 'bg-orange-100 text-orange-600',
}
</script>

<template>
  <span
    class="inline-flex items-center px-1.5 py-0.5 text-xs font-medium rounded"
    :class="colorMap[status] ?? 'bg-gray-100 text-gray-600'"
  >
    <slot>{{ status }}</slot>
  </span>
</template>
