<script setup lang="ts">
import { watch, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'

const { t } = useI18n()
const { state, handleConfirm, handleCancel } = useConfirmDialog()

const dialogRef = ref<HTMLDialogElement | null>(null)

watch(() => state.open, (open) => {
  if (open) {
    dialogRef.value?.showModal()
  } else {
    dialogRef.value?.close()
  }
})

function onDialogCancel(e: Event) {
  e.preventDefault()
  handleCancel()
}

const variantClasses: Record<string, string> = {
  warning: 'confirm-dialog--warning',
  error: 'confirm-dialog--error',
  info: 'confirm-dialog--info',
}
</script>

<template>
  <dialog
    ref="dialogRef"
    class="confirm-dialog"
    :class="variantClasses[state.variant]"
    @cancel="onDialogCancel"
  >
    <div class="confirm-dialog__content">
      <h2 class="confirm-dialog__title">
        {{ state.title }}
      </h2>
      <p class="confirm-dialog__message">
        {{ state.message }}
      </p>
      <div
        v-if="state.promptMode"
        class="confirm-dialog__input"
      >
        <label
          for="confirm-dialog-input"
          class="sr-only"
        >{{ state.title }}</label>
        <input
          id="confirm-dialog-input"
          v-model="state.inputValue"
          type="text"
          @keydown.enter.prevent="handleConfirm"
        >
        <p
          v-if="state.inputError"
          class="confirm-dialog__input-error"
        >
          {{ state.inputError }}
        </p>
      </div>
      <div class="confirm-dialog__actions">
        <button
          class="btn"
          @click="handleCancel"
        >
          {{ state.cancelLabel || t('app.cancel') }}
        </button>
        <button
          class="btn"
          :class="{
            'btn-danger': state.variant === 'error',
            'btn-primary': state.variant !== 'error',
          }"
          @click="handleConfirm"
        >
          {{ state.confirmLabel || t('app.confirm') }}
        </button>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.confirm-dialog {
  border: none;
  border-radius: var(--radius-md);
  padding: 0;
  max-width: 28rem;
  width: calc(100% - 2rem);
  background: var(--color-bg);
  color: var(--color-fg);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
}

.confirm-dialog::backdrop {
  background: rgba(0, 0, 0, 0.4);
}

.confirm-dialog__content {
  padding: 1.5rem;
}

.confirm-dialog__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 0.5rem 0;
}

.confirm-dialog__message {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0 0 1.5rem 0;
  line-height: 1.5;
}

.confirm-dialog__input {
  margin-bottom: 1rem;
}
.confirm-dialog__input input {
  width: 100%;
}
.confirm-dialog__input-error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin: 0.25rem 0 0;
}
.confirm-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
