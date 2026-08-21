<script setup lang="ts">
// The accept link returned by an invite-create call (R6.09).
//
// It exists so a deployment with no outbound mail can still deliver an
// invitation: the inviter copies the link and hands it over themselves. The
// backend returns it on creation only, so once this card is dismissed the link
// is unrecoverable and the invite has to be revoked and re-sent — the help text
// says so rather than leaving the owner to find out.

import { useI18n } from 'vue-i18n'
import { SAlert, SButton, SCopyField } from '@shared/ui'

defineProps<{
  email: string
  acceptUrl: string
}>()

defineEmits<{ dismiss: [] }>()

const { t } = useI18n()
</script>

<template>
  <SAlert
    variant="success"
    :title="t('tenancy.member.inviteLinkTitle')"
    class="invite-link"
  >
    <!-- The address is bound, never interpolated into the message string:
         vue-i18n reads a literal `@` as a linked message and only fails in a
         production build. -->
    <p class="invite-link__lede">
      {{ t('tenancy.member.inviteLinkLede') }} <strong>{{ email }}</strong>
    </p>
    <SCopyField
      :label="t('tenancy.member.inviteLinkLabel')"
      name="inviteAcceptUrl"
      :value="acceptUrl"
      :help="t('tenancy.member.inviteLinkHelp')"
    />
    <template #actions>
      <SButton
        variant="secondary"
        size="sm"
        @click="$emit('dismiss')"
      >
        {{ t('tenancy.member.inviteLinkDismiss') }}
      </SButton>
    </template>
  </SAlert>
</template>

<style scoped>
.invite-link__lede {
  margin: 0 0 0.75rem;
}
</style>
