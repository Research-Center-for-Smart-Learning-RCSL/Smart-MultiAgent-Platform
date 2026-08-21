<script setup lang="ts">
import { computed, onMounted, useTemplateRef } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  InformationCircleIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  XCircleIcon,
} from '@heroicons/vue/24/solid'
import { XMarkIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n()

type Variant = 'info' | 'success' | 'warning' | 'danger'

const props = withDefaults(
  defineProps<{
    variant: Variant
    title?: string
    dismissible?: boolean
    /** When true, on mount the alert scrolls itself into view and takes focus.
     *  Use for transient submit/server errors so they never surface below the
     *  fold in long forms. Requires the alert to remount on each error — reset
     *  the gating condition to falsy before re-triggering. */
    focusOnMount?: boolean
  }>(),
  {
    dismissible: false,
    focusOnMount: false,
  },
)

const emit = defineEmits<{
  dismiss: []
}>()

const rootRef = useTemplateRef<HTMLDivElement>('root')

onMounted(() => {
  if (!props.focusOnMount) return
  const el = rootRef.value
  if (!el) return
  // jsdom (tests) implements neither scrollIntoView nor matchMedia; guard both.
  if (typeof el.scrollIntoView === 'function') {
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
  }
  el.focus()
})

// 11-responsive-a11y.md:293. role="alert" implies aria-live="assertive", which
// is right for a danger or warning the user must act on and wrong for a static
// informational panel: it pre-empts the page heading on mount. role="status"
// still announces, politely. Deliberately no override prop - one would invite
// back the assertive-by-default usage this mapping exists to stop.
const liveRole = computed(() =>
  props.variant === 'danger' || props.variant === 'warning' ? 'alert' : 'status',
)

const iconComponent = computed(() => {
  const map = {
    info: InformationCircleIcon,
    success: CheckCircleIcon,
    warning: ExclamationTriangleIcon,
    danger: XCircleIcon,
  } as const
  return map[props.variant]
})
</script>

<template>
  <div
    ref="root"
    class="s-alert"
    :class="`s-alert--${variant}`"
    :role="liveRole"
    v-bind="focusOnMount ? { tabindex: -1 } : {}"
  >
    <component
      :is="iconComponent"
      class="s-alert__icon"
      aria-hidden="true"
    />
    <div class="s-alert__content">
      <p
        v-if="title"
        class="s-alert__title"
      >
        {{ title }}
      </p>
      <div
        v-if="$slots.default"
        class="s-alert__desc"
      >
        <slot />
      </div>
      <div
        v-if="$slots.actions"
        class="s-alert__actions"
      >
        <slot name="actions" />
      </div>
    </div>
    <button
      v-if="dismissible"
      type="button"
      class="s-alert__dismiss"
      :aria-label="t('app.dismiss')"
      @click="emit('dismiss')"
    >
      <XMarkIcon
        class="s-alert__dismiss-icon"
        aria-hidden="true"
      />
    </button>
  </div>
</template>

<style scoped>
.s-alert {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  width: 100%;
  padding: 12px 16px;
  border-left: 4px solid;
  border-radius: var(--radius-md);
}

/* Programmatic focus (focusOnMount) must not draw a ring for mouse users; only
   a real keyboard landing (focus-visible) does. */
.s-alert:focus {
  outline: none;
}

.s-alert:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* Variant colors */
.s-alert--info {
  background: var(--color-info-tint);
  border-left-color: var(--color-info-on);
  color: var(--color-info-on);
}

.s-alert--success {
  background: var(--color-success-tint);
  border-left-color: var(--color-success-on);
  color: var(--color-success-on);
}

.s-alert--warning {
  background: var(--color-warning-tint);
  border-left-color: var(--color-warning-on);
  color: var(--color-warning-on);
}

.s-alert--danger {
  background: var(--color-danger-tint);
  border-left-color: var(--color-danger-on);
  color: var(--color-danger-on);
}

/* Icon */
.s-alert__icon {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  margin-right: 12px;
}

/* Content */
.s-alert__content {
  flex: 1;
  min-width: 0;
}

.s-alert__title {
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  line-height: 24px;
}

.s-alert__desc {
  font-size: 14px;
  font-weight: 400;
  margin-top: 2px;
  line-height: 1.5;
}

.s-alert__title + .s-alert__desc {
  margin-top: 4px;
}

.s-alert__actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

/* Dismiss button */
.s-alert__dismiss {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  margin-left: 12px;
  border: none;
  background: none;
  color: currentColor;
  cursor: pointer;
  border-radius: var(--radius-sm);
  opacity: 0.7;
  transition: opacity var(--transition-fast);
}

.s-alert__dismiss:hover {
  opacity: 1;
}

.s-alert__dismiss:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  opacity: 1;
}

.s-alert__dismiss-icon {
  width: 20px;
  height: 20px;
}
</style>
