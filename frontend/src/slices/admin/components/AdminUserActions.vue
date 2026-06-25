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
}>()

const { t } = useI18n()
</script>

<template>
  <div class="admin-user-actions">
    <SButton
      v-if="user.status === 'active'"
      variant="danger"
      :disabled="isPending"
      @click="$emit('ban')"
    >
      {{ t('admin.users.ban') }}
    </SButton>
    <SButton
      v-if="user.status === 'banned'"
      variant="secondary"
      :disabled="isPending"
      @click="$emit('unban')"
    >
      {{ t('admin.users.unban') }}
    </SButton>
    <SButton
      v-if="user.status === 'active'"
      variant="danger"
      :disabled="isPending"
      @click="$emit('soft-delete')"
    >
      {{ t('admin.userDetail.softDelete') }}
    </SButton>
    <SButton
      v-if="user.deleted_at"
      variant="danger"
      :disabled="isPending"
      @click="$emit('hard-delete')"
    >
      {{ t('admin.userDetail.hardDelete') }}
    </SButton>
    <SButton
      v-if="user.status === 'active'"
      variant="secondary"
      :disabled="isPending"
      @click="$emit('impersonate')"
    >
      {{ t('admin.userDetail.impersonate') }}
    </SButton>
  </div>
</template>

<style scoped>
.admin-user-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1rem;
}
</style>
