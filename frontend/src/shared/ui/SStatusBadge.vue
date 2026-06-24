<script setup lang="ts">
import SBadge from './SBadge.vue'

const props = defineProps<{
  status: string
}>()

type BadgeVariant = 'info' | 'success' | 'warning' | 'danger' | 'neutral'

const VARIANT_MAP: Record<string, BadgeVariant> = {
  running: 'info',
  waiting: 'warning',
  succeeded: 'success',
  completed: 'success',
  approved: 'success',
  failed: 'danger',
  rejected: 'danger',
  error: 'danger',
  cancelled: 'neutral',
  skipped: 'neutral',
  pending: 'neutral',
  idle: 'neutral',
  timeout: 'warning',
  timeout_leader: 'warning',
}

function getVariant(): BadgeVariant {
  return VARIANT_MAP[props.status] ?? 'neutral'
}
</script>

<template>
  <SBadge
    :variant="getVariant()"
    size="sm"
    dot
  >
    <slot>{{ status }}</slot>
  </SBadge>
</template>
