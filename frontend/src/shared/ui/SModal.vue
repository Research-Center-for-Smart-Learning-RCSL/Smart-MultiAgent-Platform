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
   *  an accidental backdrop click while staying keyboard-dismissible (禮3.1). */
  closeOnBackdrop?: boolean
  role?: 'dialog' | 'alertdialog'
}>(), {
  open: false,
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
// aria-labelledby is genuinely optional; omit the attr entirely rather than
// passing an explicit `undefined` value, which exactOptionalPropertyTypes
// forbids.
const labelledByAttrs = computed(() =>
  props.title && !slots.header ? { 'aria-labelledby': titleId } : {},
)

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
          v-bind="labelledByAttrs"
          ref="panelRef"
          class="s-modal__panel"
          :class="`s-modal__panel--${size}`"
          :role="props.role"
          aria-modal="true"
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
  /* Only the body is height-capped, so header + body + footer can exceed the
     viewport. align-items: center in a container that neither scrolls nor pads
     put half of that excess above y = 0, where nothing could reach it - and
     the clipped element is the dialog's aria-labelledby target. flex-start
     plus the panel's margin: auto still centres, but a scroll container
     honours the auto margins rather than clipping past its start edge. The
     --space-6 per side is what .s-modal__panel--full already subtracts. */
  align-items: flex-start;
  justify-content: center;
  overflow-y: auto;
  /* Teleports to body outside the shell's padding box, so it carries its own.
     max() keeps the designed gutter wherever the inset is smaller; the mobile
     block below drops this to 0 and moves the insets onto the panel instead. */
  padding: max(var(--space-6), env(safe-area-inset-top, 0px))
    max(var(--space-6), env(safe-area-inset-right, 0px))
    max(var(--space-6), env(safe-area-inset-bottom, 0px))
    max(var(--space-6), env(safe-area-inset-left, 0px));
}

.s-modal__backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
}

.s-modal__panel {
  position: relative;
  /* Centres inside .s-modal's scroll container; see the note there. */
  margin: auto;
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
  padding: var(--space-5) var(--space-6) 0;
}

.s-modal__title {
  font-size: var(--font-size-xl);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0;
  line-height: var(--line-snug);
}

.s-modal__close {
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

.s-modal__close:hover {
  color: var(--color-fg);
}

.s-modal__close-icon {
  width: 24px;
  height: 24px;
}

.s-modal__body {
  padding: var(--space-6);
  max-height: 70vh;
  overflow-y: auto;
}

.s-modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border-subtle);
}

/* -- Enter/Leave transitions -- */
.s-modal-enter-active,
.s-modal-leave-active {
  transition: opacity var(--transition-normal);
}

.s-modal-enter-active .s-modal__panel,
.s-modal-leave-active .s-modal__panel {
  transition: transform var(--transition-normal), opacity var(--transition-normal);
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
    /* Full screen means edge to edge; the desktop inset would draw a border. */
    padding: 0;
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
    /* Auto cross-axis margins outrank align-items: stretch; zero them so the
       full-screen branch does not depend on the explicit height above. */
    margin: 0;
    /* The insets belong to the panel here, not to .s-modal: full screen means
       the panel's own background covers the cutout and the home-indicator
       strip, and only its CONTENT steps clear of them. Insetting .s-modal
       instead would show the dark backdrop as a border around the dialog. */
    padding: env(safe-area-inset-top, 0px) env(safe-area-inset-right, 0px)
      env(safe-area-inset-bottom, 0px) env(safe-area-inset-left, 0px);
  }

  .s-modal__body {
    max-height: none;
    flex: 1;
  }

  .s-modal__header {
    padding-top: var(--space-4);
    /* Back arrow then title, both left-aligned. */
    justify-content: flex-start;
    gap: var(--space-2);
  }

  .s-modal__close {
    order: -1;
    margin-left: 0;
    margin-right: 0;
  }
}
</style>
