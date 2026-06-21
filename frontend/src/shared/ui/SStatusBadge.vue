<script setup lang="ts">
defineProps<{
  status: string
}>()

const colorMap: Record<string, string> = {
  running: 'bg-info-tint text-info-on',
  waiting: 'bg-warning-tint text-warning-on',
  succeeded: 'bg-success-tint text-success-on',
  completed: 'bg-success-tint text-success-on',
  approved: 'bg-success-tint text-success-on',
  failed: 'bg-danger-tint text-danger-on',
  rejected: 'bg-danger-tint text-danger-on',
  error: 'bg-danger-tint text-danger-on',
  cancelled: 'bg-neutral-tint text-neutral-on',
  skipped: 'bg-neutral-tint text-neutral-on',
  pending: 'bg-neutral-tint text-muted',
  idle: 'bg-neutral-tint text-muted',
  timeout: 'bg-warning-tint text-warning-on',
  timeout_leader: 'bg-warning-tint text-warning-on',
}
</script>

<template>
  <span
    class="inline-flex items-center px-1.5 py-0.5 text-xs font-medium rounded"
    :class="colorMap[status] ?? 'bg-neutral-tint text-neutral-on'"
  >
    <slot>{{ status }}</slot>
  </span>
</template>
