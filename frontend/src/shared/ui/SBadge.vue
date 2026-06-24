<script setup lang="ts">
import { XMarkIcon } from '@heroicons/vue/20/solid'

type Variant = 'info' | 'success' | 'warning' | 'danger' | 'neutral'
type Size = 'sm' | 'md'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    dot?: boolean
    removable?: boolean
  }>(),
  {
    variant: 'neutral',
    size: 'md',
    dot: false,
    removable: false,
  },
)

const emit = defineEmits<{
  remove: []
}>()
</script>

<template>
  <span
    class="s-badge"
    :class="[`s-badge--${props.variant}`, `s-badge--${props.size}`]"
  >
    <span
      v-if="props.dot"
      class="s-badge__dot"
      aria-hidden="true"
    />
    <span class="s-badge__label">
      <slot />
    </span>
    <button
      v-if="props.removable"
      type="button"
      class="s-badge__remove"
      aria-label="Remove"
      @click="emit('remove')"
    >
      <XMarkIcon
        class="s-badge__remove-icon"
        aria-hidden="true"
      />
    </button>
  </span>
</template>

<style scoped>
.s-badge {
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius-full);
  font-weight: 500;
  white-space: nowrap;
  line-height: 1;
  vertical-align: middle;
}

/* Sizes */
.s-badge--sm {
  height: 20px;
  font-size: 10px;
  padding: 0 6px;
}
.s-badge--md {
  height: 24px;
  font-size: 12px;
  padding: 0 8px;
}

/* Variants */
.s-badge--info {
  background-color: var(--color-info-tint);
  color: var(--color-info-on);
}
.s-badge--success {
  background-color: var(--color-success-tint);
  color: var(--color-success-on);
}
.s-badge--warning {
  background-color: var(--color-warning-tint);
  color: var(--color-warning-on);
}
.s-badge--danger {
  background-color: var(--color-danger-tint);
  color: var(--color-danger-on);
}
.s-badge--neutral {
  background-color: var(--color-neutral-tint);
  color: var(--color-neutral-on);
}

/* Dot */
.s-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: currentColor;
  flex-shrink: 0;
  margin-right: 4px;
}

/* Label */
.s-badge__label {
  display: inline-flex;
  align-items: center;
}

/* Remove button */
.s-badge__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: 4px;
  padding: 0;
  border: none;
  background: none;
  color: currentColor;
  cursor: pointer;
  border-radius: 50%;
  opacity: 0.7;
  min-width: 44px;
  min-height: 44px;
  /* Visually 12px but touch target 44px via negative margin */
  width: 12px;
  height: 12px;
  min-width: unset;
  min-height: unset;
  position: relative;
  transition: opacity var(--transition-fast);
}
.s-badge__remove::before {
  content: '';
  position: absolute;
  width: 44px;
  height: 44px;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
}
.s-badge__remove:hover {
  opacity: 1;
}
.s-badge__remove:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  opacity: 1;
}
.s-badge__remove-icon {
  width: 12px;
  height: 12px;
}
</style>
