<script setup lang="ts">
// Provision an account for someone who cannot self-register (R6.18).
//
// There is no password field by design: the account holder sets their own
// password through the link this produces, so no plaintext password is ever
// known to the Admin or crosses an HTTP response.

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SAlert, SButton, SFormField, SInput, SModal } from '@shared/ui'

const props = defineProps<{
  open: boolean
  pending: boolean
}>()

const emit = defineEmits<{
  close: []
  submit: [payload: { email: string; displayName: string | null }]
}>()

const { t } = useI18n()

const email = ref('')
const displayName = ref('')

// Reset on open, not on close: the dialog stays mounted, and clearing on close
// would wipe the fields under a failed submit the operator is about to retry.
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      email.value = ''
      displayName.value = ''
    }
  },
)

function onSubmit(): void {
  const address = email.value.trim()
  if (!address || props.pending) return
  emit('submit', { email: address, displayName: displayName.value.trim() || null })
}
</script>

<template>
  <SModal
    :open="open"
    :title="t('admin.users.createTitle')"
    size="md"
    @close="$emit('close')"
  >
    <SAlert
      variant="info"
      class="admin-create-user__note"
    >
      {{ t('admin.users.createNote') }}
    </SAlert>

    <!-- SModal renders the footer slot outside this <form>, so the submit
         button reaches it through the HTML5 `form` attribute rather than by
         calling onSubmit directly. That is what makes the browser run its own
         constraint validation — without it `type="email"` below is inert for
         everyone who clicks instead of pressing Enter, and a malformed address
         is POSTed for the backend to reject as a 422 the operator cannot act
         on. -->
    <form
      id="adminCreateUserForm"
      class="admin-create-user__form"
      @submit.prevent="onSubmit"
    >
      <SFormField
        :label="t('admin.users.email')"
        name="adminCreateUserEmail"
        required
      >
        <SInput
          v-model="email"
          type="email"
          placeholder="user@example.com"
          :disabled="pending"
        />
      </SFormField>

      <SFormField
        :label="t('admin.users.displayName')"
        name="adminCreateUserDisplayName"
        :help="t('admin.users.createDisplayNameHelp')"
      >
        <SInput
          v-model="displayName"
          :maxlength="50"
          :disabled="pending"
        />
      </SFormField>
    </form>

    <template #footer>
      <SButton
        variant="secondary"
        :disabled="pending"
        @click="emit('close')"
      >
        {{ t('common.cancel') }}
      </SButton>
      <SButton
        type="submit"
        form="adminCreateUserForm"
        variant="primary"
        :loading="pending"
        :disabled="pending || !email.trim()"
      >
        {{ t('admin.users.createSubmit') }}
      </SButton>
    </template>
  </SModal>
</template>

<style scoped>
.admin-create-user__note {
  margin-bottom: var(--space-4);
}
</style>
