<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'info' | 'success' | 'warning' | 'danger'
type Size = 'sm' | 'md'

const props = withDefaults(
  defineProps<{
    value?: number
    variant?: Variant
    indeterminate?: boolean
    size?: Size
  }>(),
  {
    value: 0,
    variant: 'info',
    indeterminate: false,
    size: 'md',
  },
)

const clampedValue = computed(() => Math.min(100, Math.max(0, props.value)))

const fillStyle = computed(() =>
  props.indeterminate ? {} : { width: `${clampedValue.value}%` },
)
</script>

<template>
  <div
    class="s-progress"
    :class="`s-progress--${props.size}`"
    role="progressbar"
    :aria-valuenow="props.indeterminate ? undefined : clampedValue"
    :aria-valuemin="props.indeterminate ? undefined : 0"
    :aria-valuemax="props.indeterminate ? undefined : 100"
  >
    <div
      class="s-progress__fill"
      :class="[
        `s-progress__fill--${props.variant}`,
        { 's-progress__fill--indeterminate': props.indeterminate },
      ]"
      :style="fillStyle"
    />
  </div>
</template>

<style scoped>
.s-progress {
  width: 100%;
  border-radius: var(--radius-full);
  background-color: var(--color-neutral-tint);
  overflow: hidden;
}

/* Sizes */
.s-progress--sm {
  height: 4px;
}
.s-progress--md {
  height: 8px;
}

/* Fill */
.s-progress__fill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width var(--transition-normal);
}

/* Variant colors */
.s-progress__fill--info {
  background-color: var(--color-accent);
}
.s-progress__fill--success {
  background-color: var(--color-success);
}
.s-progress__fill--warning {
  background-color: var(--color-warning);
}
.s-progress__fill--danger {
  background-color: var(--color-danger);
}

/* Indeterminate animation */
.s-progress__fill--indeterminate {
  width: 40%;
  animation: progress-slide 1.5s ease-in-out infinite;
}

@keyframes progress-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(350%);
  }
}
</style>
