<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import SModal from './SModal.vue'
import SButton from './SButton.vue'

const { t } = useI18n()
const { state, handleConfirm, handleCancel } = useConfirmDialog()

const inputRef = ref<HTMLInputElement | null>(null)
</script>

<template>
  <SModal
    :open="state.open"
    :title="state.title"
    size="sm"
    role="alertdialog"
    :closable="false"
    @close="handleCancel"
  >
    <p class="s-confirm__message">
      {{ state.message }}
    </p>

    <div
      v-if="state.promptMode"
      class="s-confirm__prompt"
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
        class="s-confirm__input"
        @keydown.enter.prevent="handleConfirm"
      >
      <p
        v-if="state.inputError"
        class="s-confirm__input-error"
      >
        {{ state.inputError }}
      </p>
    </div>

    <template #footer>
      <SButton
        variant="secondary"
        @click="handleCancel"
      >
        {{ state.cancelLabel || t('app.cancel') }}
      </SButton>
      <SButton
        :variant="state.variant === 'error' ? 'danger' : 'primary'"
        @click="handleConfirm"
      >
        {{ state.confirmLabel || t('app.confirm') }}
      </SButton>
    </template>
  </SModal>
</template>

<style scoped>
.s-confirm__message {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
  line-height: 1.5;
}

.s-confirm__prompt {
  margin-top: 1rem;
}

.s-confirm__input {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  background: var(--color-bg);
  color: var(--color-fg);
}

.s-confirm__input:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: -1px;
}

.s-confirm__input-error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin: 0.25rem 0 0;
}
</style>
