<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  XCircleIcon,
} from '@heroicons/vue/20/solid'
import type { ActivityValidationStatus } from '../types'

const props = defineProps<{
  status: ActivityValidationStatus
  isValid: boolean | null
}>()

type Tone = 'pending' | 'success' | 'danger' | 'warning'

// The outcome is derived only from the backend status/is_valid — never from any
// client-supplied field (AC-5). `validated` splits on is_valid; when is_valid is
// still unknown (async validation, whose WS event omits it) the neutral
// "validated" label is shown rather than guessing pass/fail.
const view = computed<{ tone: Tone; labelKey: string }>(() => {
  if (props.status === 'pending') return { tone: 'pending', labelKey: 'activities.outcome.pending' }
  if (props.status === 'error') return { tone: 'warning', labelKey: 'activities.outcome.error' }
  if (props.isValid === true) return { tone: 'success', labelKey: 'activities.outcome.valid' }
  if (props.isValid === false) return { tone: 'danger', labelKey: 'activities.outcome.invalid' }
  return { tone: 'success', labelKey: 'activities.outcome.validated' }
})

const icon = computed(() => {
  switch (view.value.tone) {
    case 'pending':
      return ClockIcon
    case 'danger':
      return XCircleIcon
    case 'warning':
      return ExclamationTriangleIcon
    default:
      return CheckCircleIcon
  }
})
</script>

<template>
  <span
    class="outcome-badge"
    :class="`outcome-badge--${view.tone}`"
  >
    <component
      :is="icon"
      class="outcome-badge__icon"
      aria-hidden="true"
    />
    {{ $t(view.labelKey) }}
  </span>
</template>

<style scoped>
.outcome-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 500;
  border: 1px solid transparent;
}
.outcome-badge__icon {
  width: 14px;
  height: 14px;
}
.outcome-badge--pending {
  color: var(--color-muted);
  background: var(--color-surface);
  border-color: var(--color-border);
}
.outcome-badge--success {
  color: var(--color-success, #15803d);
  background: color-mix(in srgb, var(--color-success, #15803d) 12%, transparent);
}
.outcome-badge--danger {
  color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, transparent);
}
.outcome-badge--warning {
  color: var(--color-warning, #b45309);
  background: color-mix(in srgb, var(--color-warning, #b45309) 12%, transparent);
}
</style>
