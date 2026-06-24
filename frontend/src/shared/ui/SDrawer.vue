<script setup lang="ts">
import { watch, ref, nextTick, onBeforeUnmount, useSlots } from 'vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'

const props = withDefaults(defineProps<{
  open?: boolean
  title?: string
  side?: 'left' | 'right'
  size?: 'sm' | 'md' | 'lg'
}>(), {
  open: false,
  title: undefined,
  side: 'right',
  size: 'md',
})

const emit = defineEmits<{
  close: []
}>()

const slots = useSlots()

const panelRef = ref<HTMLElement | null>(null)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  }
}

function onBackdropClick() {
  emit('close')
}

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    document.body.style.overflow = 'hidden'
    await nextTick()
    panelRef.value?.focus()
  } else {
    document.body.style.overflow = ''
  }
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
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
        <Transition :name="side === 'right' ? 's-drawer-slide-right' : 's-drawer-slide-left'">
          <div
            v-if="open"
            ref="panelRef"
            class="s-drawer__panel"
            :class="[
              `s-drawer__panel--${side}`,
              `s-drawer__panel--${size}`,
            ]"
            role="dialog"
            aria-modal="true"
            tabindex="-1"
          >
            <div class="s-drawer__header">
              <h2
                v-if="title"
                class="s-drawer__title"
              >
                {{ title }}
              </h2>
              <button
                class="s-drawer__close"
                type="button"
                aria-label="Close"
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

.s-drawer__panel {
  position: fixed;
  top: 0;
  bottom: 0;
  background: var(--color-bg);
  box-shadow: var(--shadow-xl);
  display: flex;
  flex-direction: column;
  outline: none;
}

.s-drawer__panel--right {
  right: 0;
}

.s-drawer__panel--left {
  left: 0;
}

.s-drawer__panel--sm {
  width: 320px;
  max-width: 100vw;
}

.s-drawer__panel--md {
  width: 420px;
  max-width: 100vw;
}

.s-drawer__panel--lg {
  width: 560px;
  max-width: 100vw;
}

.s-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 0;
  flex-shrink: 0;
}

.s-drawer__title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
  line-height: 1.4;
}

.s-drawer__close {
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

.s-drawer__close:hover {
  color: var(--color-fg);
}

.s-drawer__close:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-drawer__close-icon {
  width: 24px;
  height: 24px;
}

.s-drawer__body {
  padding: 24px;
  flex: 1;
  overflow-y: auto;
}

.s-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

/* -- Backdrop fade -- */
.s-drawer-backdrop-enter-active,
.s-drawer-backdrop-leave-active {
  transition: opacity var(--transition-normal) ease;
}

.s-drawer-backdrop-enter-from,
.s-drawer-backdrop-leave-to {
  opacity: 0;
}

/* -- Slide right -- */
.s-drawer-slide-right-enter-active,
.s-drawer-slide-right-leave-active {
  transition: transform var(--transition-slow) ease;
}

.s-drawer-slide-right-enter-from,
.s-drawer-slide-right-leave-to {
  transform: translateX(100%);
}

/* -- Slide left -- */
.s-drawer-slide-left-enter-active,
.s-drawer-slide-left-leave-active {
  transition: transform var(--transition-slow) ease;
}

.s-drawer-slide-left-enter-from,
.s-drawer-slide-left-leave-to {
  transform: translateX(-100%);
}
</style>
