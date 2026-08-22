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
})

const tag = computed(() => {
  if (props.as === 'router-link') return RouterLink
  return props.as
})

const isDisabled = computed(() => props.disabled || props.loading)

// Built as an object rather than as separate `:href`/`:to`/`:type` bindings so
// the keys that do not apply are ABSENT, not `undefined`. On a component tag
// (RouterLink) an `undefined` fallthrough attr still wins over what the
// component renders, which silently stripped the href off every
// `as="router-link"` button and left it without the link role.
const tagAttrs = computed<Record<string, unknown>>(() => {
  if (props.as === 'button') {
    return { type: props.type, disabled: isDisabled.value }
  }
  const attrs: Record<string, unknown> = props.as === 'a' ? { href: props.to } : { to: props.to }
  if (isDisabled.value) attrs['aria-disabled'] = 'true'
  return attrs
})
</script>

<template>
  <component
    :is="tag"
    v-bind="tagAttrs"
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
  gap: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-weight: var(--weight-medium);
  line-height: 1.25;
  cursor: pointer;
  text-decoration: none;
  position: relative;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast),
    box-shadow var(--transition-fast),
    transform var(--transition-fast);
}

/* -- Press --
   The mirror of the hover lift, per the motion language in main.css. Before
   this, `:active` appeared nowhere in the entire codebase, so a button being
   held down looked exactly like one merely hovered.

   The depression is universal; the fill deepening is per variant below,
   because "one step darker" means something different for a filled button
   than for a transparent one. `link` is excluded: it renders as inline text
   with no padding, and nudging a word down a pixel reads as a glitch.

   Under prefers-reduced-motion the global freeze zeroes the duration, so this
   snaps into place instead of animating - the feedback survives, the
   movement does not. Observed rather than assumed: under
   `prefers-reduced-motion: reduce` the computed transition-duration is 1e-05s
   and the pressed transform is still `matrix(1, 0, 0, 1, 0, 1)`.

   The drop to `--elevation-0` reads only against the resting `--elevation-1`
   the filled variants now carry (below). `ghost` and `link` have no resting
   shadow, so it stays a no-op for them by design rather than by oversight. */
.s-btn:active:not(.s-btn--disabled):not(.s-btn--link) {
  transform: translateY(1px);
  box-shadow: var(--elevation-0);
}

/* -- Sizes -- */
.s-btn--sm {
  min-height: var(--control-h-sm);
  padding: var(--space-1-5) var(--space-3);
  font-size: var(--font-size-xs);
}

.s-btn--md {
  min-height: var(--control-h-md);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-sm);
}

.s-btn--lg {
  min-height: var(--control-h-lg);
  padding: var(--space-2-5) var(--space-6);
  font-size: var(--font-size-md);
}

/* -- Icon-only -- */
.s-btn--icon-only.s-btn--sm {
  width: var(--control-h-sm);
  padding: 0;
}

.s-btn--icon-only.s-btn--md {
  width: var(--control-h-md);
  padding: 0;
}

.s-btn--icon-only.s-btn--lg {
  width: var(--control-h-lg);
  padding: 0;
}

/* -- Variants -- */

/* Resting elevation for the filled variants, so the press below is a step and
   not a no-op.

   The press rule sets `box-shadow: var(--elevation-0)`, which resolves to
   `none`, and nothing declared a resting shadow - so "one elevation step down"
   moved from nothing to nothing and only the 1px translate was ever expressed.
   Either half of that pair had to go; this is the half that keeps the motion
   language `main.css` describes.

   Filled variants only. `ghost` and `link` render as transparent text: a
   shadow under them would be a shadow cast by nothing, and their `:active`
   already deepens a fill that `ghost` only acquires on interaction. They are
   excluded from the press's shadow for the same reason `link` is excluded from
   its translate.

   Declared per variant rather than as one grouped selector so the source sweep
   in `focus-and-press.test.ts` can ask each variant the question separately. */
.s-btn--primary {
  background: var(--color-accent);
  color: var(--color-on-accent);
  border-color: var(--color-accent);
  box-shadow: var(--elevation-1);
}

.s-btn--primary:hover:not(.s-btn--disabled) {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}

.s-btn--primary:active:not(.s-btn--disabled) {
  background: color-mix(in srgb, var(--color-accent), black 14%);
  border-color: color-mix(in srgb, var(--color-accent), black 14%);
}

.s-btn--secondary {
  background: var(--color-surface);
  color: var(--color-fg);
  border-color: var(--color-border);
  box-shadow: var(--elevation-1);
}

.s-btn--secondary:hover:not(.s-btn--disabled) {
  background: var(--color-surface-hover);
}

.s-btn--secondary:active:not(.s-btn--disabled) {
  background: var(--color-surface-active);
}

.s-btn--danger {
  background: var(--color-danger);
  color: var(--color-on-danger);
  border-color: var(--color-danger);
  box-shadow: var(--elevation-1);
}

.s-btn--danger:hover:not(.s-btn--disabled) {
  background: color-mix(in srgb, var(--color-danger), black 12%);
  border-color: color-mix(in srgb, var(--color-danger), black 12%);
}

.s-btn--danger:active:not(.s-btn--disabled) {
  background: color-mix(in srgb, var(--color-danger), black 24%);
  border-color: color-mix(in srgb, var(--color-danger), black 24%);
}

.s-btn--ghost {
  background: transparent;
  color: var(--color-fg);
  border-color: transparent;
}

.s-btn--ghost:hover:not(.s-btn--disabled) {
  background: var(--color-surface-hover);
}

.s-btn--ghost:active:not(.s-btn--disabled) {
  background: var(--color-surface-active);
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

/* No depression here - see the base :active rule. The colour shift is the
   whole press signal for a control that renders as a word in a sentence. */
.s-btn--link:active:not(.s-btn--disabled) {
  color: var(--color-accent-hover);
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
