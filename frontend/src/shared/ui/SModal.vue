<script setup lang="ts">
import { ref, useSlots, useId, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon, ArrowLeftIcon } from '@heroicons/vue/24/outline'
import { useBreakpoint, useFocusTrap } from '@shared/composables'

const props = withDefaults(defineProps<{
  open?: boolean
  title?: string
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
  closable?: boolean
  persistent?: boolean
  /** When false, clicking the backdrop does NOT close the modal, but Escape
   *  still does (unless `persistent`). Lets a destructive-confirm dialog resist
   *  an accidental backdrop click while staying keyboard-dismissible (§3.1). */
  closeOnBackdrop?: boolean
  role?: 'dialog' | 'alertdialog'
}>(), {
  open: false,
  title: undefined,
  size: 'md',
  closable: true,
  persistent: false,
  closeOnBackdrop: true,
  role: 'dialog',
})

const emit = defineEmits<{
  close: []
}>()

const slots = useSlots()
const { t } = useI18n()
const { isMobile } = useBreakpoint()

const titleId = useId()
// Only label by the rendered <h2>; a custom header slot owns its own labelling.
const labelledBy = computed(() => (props.title && !slots.header ? titleId : undefined))

const panelRef = ref<HTMLElement | null>(null)
const { trapTab } = useFocusTrap(panelRef, () => props.open)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && !props.persistent) {
    emit('close')
  }
  trapTab(e)
}

function onBackdropClick() {
  if (!props.persistent && props.closeOnBackdrop) {
    emit('close')
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="s-modal">
      <div
        v-if="open"
        class="s-modal"
        role="none"
        @keydown="onKeydown"
      >
        <div
          class="s-modal__backdrop"
          role="none"
          @click="onBackdropClick"
          @keydown.enter="onBackdropClick"
        />
        <div
          ref="panelRef"
          class="s-modal__panel"
          :class="`s-modal__panel--${size}`"
          :role="role"
          aria-modal="true"
          :aria-labelledby="labelledBy"
          tabindex="-1"
        >
          <div class="s-modal__header">
            <slot name="header">
              <h2
                v-if="title"
                :id="titleId"
                class="s-modal__title"
              >
                {{ title }}
              </h2>
            </slot>
            <button
              v-if="closable"
              class="s-modal__close"
              type="button"
              :aria-label="isMobile ? t('app.back') : t('app.close')"
              @click="emit('close')"
            >
              <ArrowLeftIcon
                v-if="isMobile"
                class="s-modal__close-icon"
              />
              <XMarkIcon
                v-else
                class="s-modal__close-icon"
              />
            </button>
          </div>
          <div class="s-modal__body">
            <slot />
          </div>
          <div
            v-if="slots.footer"
            class="s-modal__footer"
          >
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.s-modal {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
}

.s-modal__backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
}

.s-modal__panel {
  position: relative;
  background: var(--color-bg);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  width: 100%;
  display: flex;
  flex-direction: column;
  outline: none;
}

.s-modal__panel--sm {
  max-width: 400px;
}

.s-modal__panel--md {
  max-width: 560px;
}

.s-modal__panel--lg {
  max-width: 720px;
}

.s-modal__panel--xl {
  max-width: 960px;
}

.s-modal__panel--full {
  max-width: calc(100vw - 48px);
}

.s-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 0;
}

.s-modal__title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
  line-height: 1.4;
}

.s-modal__close {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: var(--color-muted);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast);
  flex-shrink: 0;
  margin-left: auto;
}

.s-modal__close:hover {
  color: var(--color-fg);
}

.s-modal__close:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-modal__close-icon {
  width: 24px;
  height: 24px;
}

.s-modal__body {
  padding: 24px;
  max-height: 70vh;
  overflow-y: auto;
}

.s-modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--color-border);
}

/* -- Enter/Leave transitions -- */
.s-modal-enter-active,
.s-modal-leave-active {
  transition: opacity var(--transition-normal) ease;
}

.s-modal-enter-active .s-modal__panel,
.s-modal-leave-active .s-modal__panel {
  transition: transform var(--transition-normal) ease, opacity var(--transition-normal) ease;
}

.s-modal-enter-from,
.s-modal-leave-to {
  opacity: 0;
}

.s-modal-enter-from .s-modal__panel {
  transform: scale(0.95);
  opacity: 0;
}

.s-modal-leave-to .s-modal__panel {
  transform: scale(0.95);
  opacity: 0;
}

/* Mobile: every modal becomes a full-page view with a back arrow (left of
   the title) instead of a close X, regardless of the `size` prop. */
@media (max-width: 767px) {
  .s-modal {
    align-items: stretch;
    justify-content: stretch;
  }

  .s-modal__panel,
  .s-modal__panel--sm,
  .s-modal__panel--md,
  .s-modal__panel--lg,
  .s-modal__panel--xl,
  .s-modal__panel--full {
    max-width: 100%;
    width: 100%;
    height: 100%;
    border-radius: 0;
  }

  .s-modal__body {
    max-height: none;
    flex: 1;
  }

  .s-modal__header {
    padding-top: 16px;
    /* Back arrow then title, both left-aligned. */
    justify-content: flex-start;
    gap: 8px;
  }

  .s-modal__close {
    order: -1;
    margin-left: 0;
    margin-right: 0;
  }
}
</style>
