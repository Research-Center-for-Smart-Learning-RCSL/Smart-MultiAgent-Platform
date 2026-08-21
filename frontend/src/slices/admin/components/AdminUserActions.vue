<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SButton } from '@shared/ui'
import type { UserDetail } from '../types'

defineProps<{
  user: UserDetail
  isPending?: boolean
}>()

defineEmits<{
  ban: []
  unban: []
  'soft-delete': []
  'hard-delete': []
  impersonate: []
  'reissue-links': []
}>()

const { t } = useI18n()
</script>

<template>
  <div class="admin-user-actions">
    <SButton
      v-if="user.status === 'active'"
      variant="danger"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('ban')"
    >
      {{ t('admin.users.ban') }}
    </SButton>
    <SButton
      v-if="user.status === 'banned'"
      variant="secondary"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('unban')"
    >
      {{ t('admin.users.unban') }}
    </SButton>
    <SButton
      v-if="user.status === 'active'"
      variant="danger"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('soft-delete')"
    >
      {{ t('admin.userDetail.softDelete') }}
    </SButton>
    <SButton
      v-if="user.deleted_at"
      variant="danger"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('hard-delete')"
    >
      {{ t('admin.userDetail.hardDelete') }}
    </SButton>
    <SButton
      v-if="user.status === 'active'"
      variant="secondary"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('impersonate')"
    >
      {{ t('admin.userDetail.impersonate') }}
    </SButton>
    <!-- Offered for every live account rather than gated on a client-side guess
         at "still needs activation". The server's own predicate is "verified
         address AND any usable credential", and a linked Google identity counts
         — which the client cannot see. Hiding the button on `email_verified`
         alone would hide it from the one account that legitimately needs it: a
         provisioned user who walked the verification link before the password
         one. The 409 is the answer instead.
         Banned is excluded because the server's guard does not cover it (FU-9):
         a banned account that never set a password reads as "not yet activated"
         there, so the mint succeeds and offers activation to someone the
         operator deliberately locked out. -->
    <SButton
      v-if="!user.deleted_at && user.status !== 'banned'"
      variant="secondary"
      :disabled="isPending ?? false"
      :loading="isPending ?? false"
      @click="$emit('reissue-links')"
    >
      {{ t('admin.userDetail.reissueLinks') }}
    </SButton>
  </div>
</template>

<style scoped>
.admin-user-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
</style>
