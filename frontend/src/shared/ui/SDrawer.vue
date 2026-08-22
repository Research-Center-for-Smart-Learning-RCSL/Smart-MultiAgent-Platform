<script setup lang="ts">
import { ref, useSlots, useId, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { useFocusTrap } from '@shared/composables'

const props = withDefaults(defineProps<{
  open?: boolean
  title?: string
  side?: 'left' | 'right'
  size?: 'sm' | 'md' | 'lg'
}>(), {
  open: false,
  side: 'right',
  size: 'md',
})

const emit = defineEmits<{
  close: []
}>()

const slots = useSlots()
const { t } = useI18n()

const titleId = useId()
// aria-labelledby is genuinely optional (only when a title exists); omit the
// attr entirely rather than passing an explicit `undefined` value, which
// exactOptionalPropertyTypes forbids.
const labelledByAttrs = computed(() => (props.title ? { 'aria-labelledby': titleId } : {}))

const panelRef = ref<HTMLElement | null>(null)
const { trapTab } = useFocusTrap(panelRef, () => props.open)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
    return
  }
  trapTab(e)
}

function onBackdropClick() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="s-drawer-backdrop">
      <div
        v-if="open"
        class="s-drawer"
        role="none"
        @keydown="onKeydown"
      >
        <div
          class="s-drawer__backdrop"
          role="none"
          @click="onBackdropClick"
          @keydown.enter="onBackdropClick"
        />
        <Transition
          :name="side === 'right' ? 's-drawer-slide-right' : 's-drawer-slide-left'"
          appear
        >
          <div
            v-if="open"
            ref="panelRef"
            class="s-drawer__panel"
            :class="[
              `s-drawer__panel--${side}`,
              `s-drawer__panel--${size}`,
            ]"
            v-bind="labelledByAttrs"
            role="dialog"
            aria-modal="true"
            tabindex="-1"
          >
            <div class="s-drawer__header">
              <h2
                v-if="title"
                :id="titleId"
                class="s-drawer__title"
              >
                {{ title }}
              </h2>
              <button
                class="s-drawer__close"
                type="button"
                :aria-label="t('app.close')"
                @click="emit('close')"
              >
                <XMarkIcon class="s-drawer__close-icon" />
              </button>
            </div>
            <div class="s-drawer__body">
              <slot />
            </div>
            <div
              v-if="slots.footer"
              class="s-drawer__footer"
            >
              <slot name="footer" />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.s-drawer {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
}

.s-drawer__backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
}

/* Teleports to body and is position: fixed, so it sits outside the shell's
   padding box and carries its own insets. Padding rather than inset offsets,
   so the panel's background still paints across the strips. */
.s-drawer__panel {
  position: fixed;
  top: 0;
  bottom: 0;
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  background: var(--color-bg);
  box-shadow: var(--shadow-xl);
  display: flex;
  flex-direction: column;
  outline: none;
}

/* Only the edge each variant is anchored to; the opposite side is off-screen. */
.s-drawer__panel--right {
  right: 0;
  padding-right: env(safe-area-inset-right, 0px);
}

.s-drawer__panel--left {
  left: 0;
  padding-left: env(safe-area-inset-left, 0px);
}

/* 11-responsive-a11y.md:58. The min() carries its own cap, so the separate
   max-width this replaced is redundant rather than removed. Note that 280px
   cannot contain a 260px child plus the body's 24px gutters (308px needed), so
   any fixed-width content here must also be able to shrink - see .sidebar's
   max-width in AppSidebar.vue. */
.s-drawer__panel--sm {
  width: min(280px, 85vw);
}

.s-drawer__panel--md {
  width: 420px;
  max-width: 100vw;
}

.s-drawer__panel--lg {
  width: 560px;
  max-width: 100vw;
}

/* Responsive width per 11-responsive-a11y.md section 8:
   md+ keeps the nominal width, sm caps to 85vw, xs goes full-width. */
@media (max-width: 767px) {
  .s-drawer__panel--md,
  .s-drawer__panel--lg {
    width: 85vw;
  }
}

@media (max-width: 479px) {
  .s-drawer__panel--md,
  .s-drawer__panel--lg {
    width: 100vw;
  }
}

.s-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-5) var(--space-6) 0;
  flex-shrink: 0;
}

.s-drawer__title {
  font-size: var(--font-size-xl);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0;
  line-height: var(--line-snug);
}

.s-drawer__close {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: var(--space-1);
  cursor: pointer;
  color: var(--color-muted);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast);
  flex-shrink: 0;
  margin-left: auto;
}

.s-drawer__close:hover {
  color: var(--color-fg);
}

.s-drawer__close-icon {
  width: 24px;
  height: 24px;
}

.s-drawer__body {
  padding: var(--space-6);
  flex: 1;
  overflow-y: auto;
}

.s-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border-subtle);
  flex-shrink: 0;
}

/* -- Backdrop fade -- */
.s-drawer-backdrop-enter-active,
.s-drawer-backdrop-leave-active {
  transition: opacity var(--transition-normal);
}

.s-drawer-backdrop-enter-from,
.s-drawer-backdrop-leave-to {
  opacity: 0;
}

/* -- Slide right -- */
.s-drawer-slide-right-enter-active,
.s-drawer-slide-right-leave-active {
  transition: transform var(--transition-slow);
}

.s-drawer-slide-right-enter-from,
.s-drawer-slide-right-leave-to {
  transform: translateX(100%);
}

/* -- Slide left -- */
.s-drawer-slide-left-enter-active,
.s-drawer-slide-left-leave-active {
  transition: transform var(--transition-slow);
}

.s-drawer-slide-left-enter-from,
.s-drawer-slide-left-leave-to {
  transform: translateX(-100%);
}
</style>
