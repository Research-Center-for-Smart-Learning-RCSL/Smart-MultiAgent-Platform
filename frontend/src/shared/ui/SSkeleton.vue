<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

type Variant = 'text' | 'circle' | 'rect'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    width?: string
    height?: string
    lines?: number
    // 'shimmer' sweeps a highlight across the placeholder; 'pulse' keeps the
    // original opacity breathing. Both collapse to a static block under the
    // global reduced-motion freeze.
    animation?: 'pulse' | 'shimmer'
  }>(),
  {
    variant: 'text',
    width: '100%',
    lines: 1,
    animation: 'shimmer',
  },
)

const { t } = useI18n()

const lineCount = computed(() =>
  props.variant === 'text' ? Math.max(1, props.lines) : 1,
)
</script>

<template>
  <div
    v-if="props.variant === 'text' && lineCount > 1"
    class="s-skeleton__stack"
    role="status"
    :aria-label="t('shared.skeleton.loading')"
  >
    <span
      v-for="i in lineCount"
      :key="i"
      class="s-skeleton s-skeleton--text"
      :class="`s-skeleton--anim-${props.animation}`"
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
    :class="[`s-skeleton--${props.variant}`, `s-skeleton--anim-${props.animation}`]"
    :style="{
      width: props.variant === 'circle' ? (props.width || '32px') : props.width,
      height: props.variant === 'circle'
        ? (props.width || '32px')
        : props.variant === 'text'
          ? (props.height || '1em')
          : props.height,
    }"
    role="status"
    :aria-label="t('shared.skeleton.loading')"
  />
</template>

<style scoped>
.s-skeleton {
  display: inline-block;
  background-color: var(--color-neutral-tint);
}

.s-skeleton--anim-pulse {
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}

/* Sweeping highlight: a wide gradient slides across via background-position,
   so the effect is compositor-cheap and needs no extra elements. */
.s-skeleton--anim-shimmer {
  background: linear-gradient(
    90deg,
    var(--color-neutral-tint) 30%,
    color-mix(in srgb, var(--color-neutral-tint) 55%, var(--color-bg)) 50%,
    var(--color-neutral-tint) 70%
  );
  background-size: 300% 100%;
  animation: skeleton-shimmer 1.4s var(--ease-out-soft) infinite;
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

@keyframes skeleton-shimmer {
  from {
    background-position: 100% 50%;
  }
  to {
    background-position: 0% 50%;
  }
}
</style>
