<script setup lang="ts">
import { useI18n } from 'vue-i18n'
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
    <button
      v-if="user.status === 'active'"
      class="btn btn-danger"
      :disabled="isPending"
      @click="$emit('ban')"
    >
      {{ t('admin.users.ban') }}
    </button>
    <button
      v-if="user.status === 'banned'"
      class="btn"
      :disabled="isPending"
      @click="$emit('unban')"
    >
      {{ t('admin.users.unban') }}
    </button>
    <button
      v-if="user.status === 'active'"
      class="btn btn-danger"
      :disabled="isPending"
      @click="$emit('soft-delete')"
    >
      {{ t('admin.userDetail.softDelete') }}
    </button>
    <button
      v-if="user.deleted_at"
      class="btn btn-danger"
      :disabled="isPending"
      @click="$emit('hard-delete')"
    >
      {{ t('admin.userDetail.hardDelete') }}
    </button>
    <button
      v-if="user.status === 'active'"
      class="btn"
      :disabled="isPending"
      @click="$emit('impersonate')"
    >
      {{ t('admin.userDetail.impersonate') }}
    </button>
  </div>
</template>

<style scoped>
.admin-user-actions { display: flex; gap: 0.5rem; margin-top: 1rem; }
</style>
