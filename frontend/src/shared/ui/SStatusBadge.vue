<script setup lang="ts">
import { STATUS_BG_MAP, STATUS_BG_DEFAULT } from './statusColors'

defineProps<{
  status: string
}>()
</script>

<template>
  <span
    class="inline-flex items-center px-1.5 py-0.5 text-xs font-medium rounded"
    :class="STATUS_BG_MAP[status] ?? STATUS_BG_DEFAULT"
  >
    <slot>{{ status }}</slot>
  </span>
</template>
