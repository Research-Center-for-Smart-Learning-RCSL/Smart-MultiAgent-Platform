<script setup lang="ts">
// The two links an Admin hands over to activate a provisioned account (R6.18).
//
// They are deliberately kept apart and labelled with what each one does,
// because handing over the wrong one is the obvious failure mode: the
// set-password link is equivalent to the password itself, while the
// verification link only proves the Admin vouched for the address (Q-3).
//
// Nothing here is persisted or re-readable. The links exist only for as long as
// this dialog is open; recovering them means re-issuing, which is one click.

import { useI18n } from 'vue-i18n'
import { SAlert, SButton, SCopyField, SModal } from '@shared/ui'
import { formatDateTime } from '@shared/utils/datetime'
import type { ActivationLinks } from '../types'

defineProps<{
  open: boolean
  email: string
  links: ActivationLinks
}>()

defineEmits<{ close: [] }>()

const { t } = useI18n()
</script>

<template>
  <SModal
    :open="open"
    :title="t('admin.users.linksTitle')"
    size="lg"
    @close="$emit('close')"
  >
    <!-- The address is bound, never interpolated into a message string:
         vue-i18n reads a literal `@` as a linked message. -->
    <p class="admin-links__lede">
      {{ t('admin.users.linksLede') }} <strong>{{ email }}</strong>
    </p>

    <SAlert
      variant="warning"
      class="admin-links__warning"
    >
      {{ t('admin.users.linksWarning') }}
    </SAlert>

    <SCopyField
      :label="t('admin.users.setPasswordLabel')"
      name="adminSetPasswordUrl"
      :value="links.set_password_url"
      :help="`${t('admin.users.setPasswordHelp')} ${t('admin.users.linksExpire', {
        when: formatDateTime(links.set_password_expires_at),
      })}`"
    />

    <SCopyField
      :label="t('admin.users.verifyEmailLabel')"
      name="adminVerifyEmailUrl"
      :value="links.verify_email_url"
      :help="`${t('admin.users.verifyEmailHelp')} ${t('admin.users.linksExpire', {
        when: formatDateTime(links.verify_email_expires_at),
      })}`"
    />

    <template #footer>
      <SButton
        variant="primary"
        @click="$emit('close')"
      >
        {{ t('common.close') }}
      </SButton>
    </template>
  </SModal>
</template>

<style scoped>
.admin-links__lede {
  margin: 0 0 var(--space-3);
}

.admin-links__warning {
  margin-bottom: var(--space-4);
}
</style>
