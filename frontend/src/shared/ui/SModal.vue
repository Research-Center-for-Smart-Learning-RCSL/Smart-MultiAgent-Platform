<script setup lang="ts">
import { watch, ref, nextTick, onBeforeUnmount, useSlots } from 'vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'

const props = withDefaults(defineProps<{
  open?: boolean
  title?: string
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
  closable?: boolean
  persistent?: boolean
}>(), {
  open: false,
  title: undefined,
  size: 'md',
  closable: true,
  persistent: false,
})

const emit = defineEmits<{
  close: []
}>()

const slots = useSlots()

const panelRef = ref<HTMLElement | null>(null)
let previouslyFocused: HTMLElement | null = null

function getFocusableElements(): HTMLElement[] {
  if (!panelRef.value) return []
  return Array.from(
    panelRef.value.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  )
}

function trapFocus(e: KeyboardEvent) {
  if (e.key !== 'Tab') return
  const focusable = getFocusableElements()
  if (focusable.length === 0) {
    e.preventDefault()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (e.shiftKey) {
    if (document.activeElement === first) {
      e.preventDefault()
      last.focus()
    }
  } else {
    if (document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && !props.persistent) {
    emit('close')
  }
  trapFocus(e)
}

function onBackdropClick() {
  if (!props.persistent) {
    emit('close')
  }
}

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    previouslyFocused = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'
    await nextTick()
    const focusable = getFocusableElements()
    if (focusable.length > 0) {
      focusable[0].focus()
    } else {
      panelRef.value?.focus()
    }
  } else {
    document.body.style.overflow = ''
    if (previouslyFocused) {
      previouslyFocused.focus()
      previouslyFocused = null
    }
  }
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
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
          role="dialog"
          aria-modal="true"
          tabindex="-1"
        >
          <div class="s-modal__header">
            <slot name="header">
              <h2
                v-if="title"
                class="s-modal__title"
              >
                {{ title }}
              </h2>
            </slot>
            <button
              v-if="closable"
              class="s-modal__close"
              type="button"
              aria-label="Close"
              @click="emit('close')"
            >
              <XMarkIcon class="s-modal__close-icon" />
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
</style>
