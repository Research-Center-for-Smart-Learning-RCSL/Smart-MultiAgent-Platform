<script setup lang="ts">
import { watch, ref, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'

const { t } = useI18n()
const { state, handleConfirm, handleCancel } = useConfirmDialog()

const inputRef = ref<HTMLInputElement | null>(null)

watch(() => state.open, async (open) => {
  if (open && state.promptMode) {
    await nextTick()
    inputRef.value?.focus()
  }
})
</script>

<template>
  <Teleport to="body">
    <Transition name="s-confirm">
      <div
        v-if="state.open"
        class="s-confirm-backdrop"
        role="none"
        @click.self="handleCancel"
        @keydown.escape="handleCancel"
      >
        <div
          class="s-confirm-panel"
          role="alertdialog"
          :aria-label="state.title"
          tabindex="-1"
        >
          <h2 class="s-confirm-panel__title">
            {{ state.title }}
          </h2>
          <p class="s-confirm-panel__message">
            {{ state.message }}
          </p>
          <div
            v-if="state.promptMode"
            class="s-confirm-panel__input"
          >
            <label
              for="confirm-dialog-input"
              class="visually-hidden"
            >{{ state.title }}</label>
            <input
              id="confirm-dialog-input"
              ref="inputRef"
              v-model="state.inputValue"
              type="text"
              class="s-confirm-panel__input-field"
              @keydown.enter.prevent="handleConfirm"
            >
            <p
              v-if="state.inputError"
              class="s-confirm-panel__input-error"
            >
              {{ state.inputError }}
            </p>
          </div>
          <div class="s-confirm-panel__actions">
            <button
              type="button"
              class="s-confirm-panel__btn s-confirm-panel__btn--cancel"
              @click="handleCancel"
            >
              {{ state.cancelLabel || t('app.cancel') }}
            </button>
            <button
              type="button"
              class="s-confirm-panel__btn"
              :class="state.variant === 'error'
                ? 's-confirm-panel__btn--danger'
                : 's-confirm-panel__btn--primary'"
              @click="handleConfirm"
            >
              {{ state.confirmLabel || t('app.confirm') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.s-confirm-backdrop {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-overlay);
  z-index: var(--z-modal);
}

.s-confirm-panel {
  background: var(--color-bg);
  color: var(--color-fg);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  padding: 1.5rem;
  max-width: 28rem;
  width: calc(100% - 2rem);
}

.s-confirm-panel__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 0.5rem 0;
}

.s-confirm-panel__message {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0 0 1.5rem 0;
  line-height: 1.5;
}

.s-confirm-panel__input {
  margin-bottom: 1rem;
}

.s-confirm-panel__input-field {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  background: var(--color-bg);
  color: var(--color-fg);
}

.s-confirm-panel__input-field:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: -1px;
}

.s-confirm-panel__input-error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin: 0.25rem 0 0;
}

.s-confirm-panel__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.s-confirm-panel__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 16px;
  font-size: 0.875rem;
  font-weight: 500;
  border-radius: var(--radius-md);
  border: none;
  cursor: pointer;
  transition: background var(--transition-fast);
  min-height: 40px;
}

.s-confirm-panel__btn--cancel {
  background: var(--color-surface);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
}

.s-confirm-panel__btn--cancel:hover {
  background: var(--color-border);
}

.s-confirm-panel__btn--primary {
  background: var(--color-accent);
  color: #fff;
}

.s-confirm-panel__btn--primary:hover {
  background: var(--color-accent-hover);
}

.s-confirm-panel__btn--danger {
  background: var(--color-danger);
  color: #fff;
}

.s-confirm-panel__btn--danger:hover {
  background: color-mix(in srgb, var(--color-danger), black 12%);
}

.s-confirm-panel__btn:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.s-confirm-enter-active,
.s-confirm-leave-active {
  transition: opacity var(--transition-normal);
}

.s-confirm-enter-active .s-confirm-panel,
.s-confirm-leave-active .s-confirm-panel {
  transition: transform var(--transition-normal), opacity var(--transition-normal);
}

.s-confirm-enter-from,
.s-confirm-leave-to {
  opacity: 0;
}

.s-confirm-enter-from .s-confirm-panel {
  transform: scale(0.95);
  opacity: 0;
}

.s-confirm-leave-to .s-confirm-panel {
  transform: scale(0.95);
  opacity: 0;
}
</style>
