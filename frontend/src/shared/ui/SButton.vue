<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'link'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
  loading?: boolean
  iconOnly?: boolean
  type?: 'button' | 'submit' | 'reset'
  as?: 'button' | 'a' | 'router-link'
  to?: RouteLocationRaw | undefined
}>(), {
  variant: 'secondary',
  size: 'md',
  disabled: false,
  loading: false,
  iconOnly: false,
  type: 'button',
  as: 'button',
  to: undefined,
})

const tag = computed(() => {
  if (props.as === 'router-link') return RouterLink
  return props.as
})

const isDisabled = computed(() => props.disabled || props.loading)
</script>

<template>
  <component
    :is="tag"
    :type="as === 'button' ? type : undefined"
    :to="as === 'router-link' ? to : undefined"
    :href="as === 'a' ? (to as string) : undefined"
    :disabled="as === 'button' ? isDisabled : undefined"
    :aria-disabled="as !== 'button' && isDisabled ? 'true' : undefined"
    class="s-btn"
    :class="[
      `s-btn--${variant}`,
      `s-btn--${size}`,
      {
        's-btn--icon-only': iconOnly,
        's-btn--loading': loading,
        's-btn--disabled': isDisabled,
      },
    ]"
  >
    <span
      v-if="loading"
      class="s-btn__spinner"
      aria-hidden="true"
    >
      <svg
        class="s-btn__spinner-svg"
        viewBox="0 0 20 20"
        fill="none"
      >
        <circle
          cx="10"
          cy="10"
          r="8"
          stroke="currentColor"
          stroke-width="2"
          opacity="0.25"
        />
        <path
          d="M10 2a8 8 0 0 1 8 8"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
    </span>
    <span
      v-else-if="$slots['icon-left']"
      class="s-btn__icon"
    >
      <slot name="icon-left" />
    </span>
    <span
      v-if="!iconOnly"
      class="s-btn__label"
    >
      <slot />
    </span>
    <span
      v-else-if="!loading && !$slots['icon-left'] && !$slots['icon-right']"
      class="s-btn__icon"
    >
      <slot />
    </span>
    <span
      v-if="$slots['icon-right']"
      class="s-btn__icon"
    >
      <slot name="icon-right" />
    </span>
  </component>
</template>

<style scoped>
.s-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-weight: 500;
  line-height: 1.25;
  cursor: pointer;
  text-decoration: none;
  position: relative;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast);
}

.s-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* -- Sizes -- */
.s-btn--sm {
  min-height: 32px;
  padding: 6px 12px;
  font-size: 0.75rem;
}

.s-btn--md {
  min-height: 40px;
  padding: 8px 16px;
  font-size: 0.875rem;
}

.s-btn--lg {
  min-height: 48px;
  padding: 10px 24px;
  font-size: 1rem;
}

/* -- Icon-only -- */
.s-btn--icon-only.s-btn--sm {
  width: 32px;
  padding: 0;
}

.s-btn--icon-only.s-btn--md {
  width: 40px;
  padding: 0;
}

.s-btn--icon-only.s-btn--lg {
  width: 48px;
  padding: 0;
}

/* -- Variants -- */
.s-btn--primary {
  background: var(--color-accent);
  color: #fff;
  border-color: var(--color-accent);
}

.s-btn--primary:hover:not(.s-btn--disabled) {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}

.s-btn--secondary {
  background: var(--color-surface);
  color: var(--color-fg);
  border-color: var(--color-border);
}

.s-btn--secondary:hover:not(.s-btn--disabled) {
  background: var(--color-border);
}

.s-btn--danger {
  background: var(--color-danger);
  color: #fff;
  border-color: var(--color-danger);
}

.s-btn--danger:hover:not(.s-btn--disabled) {
  background: color-mix(in srgb, var(--color-danger), black 12%);
  border-color: color-mix(in srgb, var(--color-danger), black 12%);
}

.s-btn--ghost {
  background: transparent;
  color: var(--color-fg);
  border-color: transparent;
}

.s-btn--ghost:hover:not(.s-btn--disabled) {
  background: var(--color-surface);
}

.s-btn--link {
  background: transparent;
  color: var(--color-accent);
  border-color: transparent;
  min-height: unset;
  padding: 0;
}

.s-btn--link:hover:not(.s-btn--disabled) {
  text-decoration: underline;
}

/* -- Disabled -- */
.s-btn--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

a.s-btn--disabled {
  pointer-events: none;
}

/* -- Loading -- */
.s-btn--loading .s-btn__label {
  opacity: 0.6;
}

.s-btn__spinner {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.s-btn__spinner-svg {
  width: 1.25em;
  height: 1.25em;
  animation: s-btn-spin 0.75s linear infinite;
}

@keyframes s-btn-spin {
  to {
    transform: rotate(360deg);
  }
}

/* -- Icon slots -- */
.s-btn__icon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.s-btn__icon :deep(svg) {
  width: 1.25em;
  height: 1.25em;
}
</style>
