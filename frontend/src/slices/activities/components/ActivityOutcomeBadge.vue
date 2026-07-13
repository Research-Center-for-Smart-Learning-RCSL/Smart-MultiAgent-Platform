<script setup lang="ts">
import { computed, type FunctionalComponent } from 'vue'
import {
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon,
} from '@heroicons/vue/20/solid'
import { SBadge } from '@shared/ui'
import type { ActivityValidationStatus } from '../types'

const props = defineProps<{
  status: ActivityValidationStatus
  isValid: boolean | null
}>()

type Variant = 'info' | 'success' | 'warning' | 'danger' | 'neutral'

// Derived only from backend status/is_valid — never from a client-supplied field
// (AC-5). When is_valid is unknown (async validation, whose WS event omits it),
// the neutral variant is used rather than green success so a possibly-failed
// attempt is not shown as passed (see FU-2).
const view = computed<{ variant: Variant; labelKey: string; icon: FunctionalComponent }>(() => {
  if (props.status === 'pending') {
    return { variant: 'neutral', labelKey: 'activities.outcome.pending', icon: ClockIcon }
  }
  if (props.status === 'error') {
    return { variant: 'warning', labelKey: 'activities.outcome.error', icon: ExclamationTriangleIcon }
  }
  if (props.isValid === true) {
    return { variant: 'success', labelKey: 'activities.outcome.valid', icon: CheckCircleIcon }
  }
  if (props.isValid === false) {
    return { variant: 'danger', labelKey: 'activities.outcome.invalid', icon: XCircleIcon }
  }
  return { variant: 'neutral', labelKey: 'activities.outcome.validated', icon: InformationCircleIcon }
})
</script>

<template>
  <SBadge
    :variant="view.variant"
    size="sm"
  >
    <component
      :is="view.icon"
      class="outcome-badge__icon"
      aria-hidden="true"
    />
    {{ $t(view.labelKey) }}
  </SBadge>
</template>

<style scoped>
.outcome-badge__icon {
  width: 14px;
  height: 14px;
  margin-right: 4px;
}
</style>
