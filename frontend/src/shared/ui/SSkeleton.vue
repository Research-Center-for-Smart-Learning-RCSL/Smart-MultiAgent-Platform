<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'text' | 'circle' | 'rect'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    width?: string
    height?: string
    lines?: number
  }>(),
  {
    variant: 'text',
    width: '100%',
    height: undefined,
    lines: 1,
  },
)

const lineCount = computed(() =>
  props.variant === 'text' ? Math.max(1, props.lines) : 1,
)
</script>

<template>
  <div
    v-if="props.variant === 'text' && lineCount > 1"
    class="s-skeleton__stack"
    role="status"
    aria-label="Loading"
  >
    <span
      v-for="i in lineCount"
      :key="i"
      class="s-skeleton s-skeleton--text"
      :style="{
        width: i === lineCount ? '60%' : props.width,
        height: props.height || '1em',
      }"
      aria-hidden="true"
    />
  </div>
  <span
    v-else
    class="s-skeleton"
    :class="`s-skeleton--${props.variant}`"
    :style="{
      width: props.variant === 'circle' ? (props.width || '32px') : props.width,
      height: props.variant === 'circle'
        ? (props.width || '32px')
        : props.variant === 'text'
          ? (props.height || '1em')
          : props.height,
    }"
    role="status"
    aria-label="Loading"
  />
</template>

<style scoped>
.s-skeleton {
  display: inline-block;
  background-color: var(--color-neutral-tint);
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}

.s-skeleton--text {
  border-radius: var(--radius-sm);
}

.s-skeleton--circle {
  border-radius: 50%;
}

.s-skeleton--rect {
  border-radius: var(--radius-md);
}

.s-skeleton__stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

@keyframes skeleton-pulse {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}
</style>
