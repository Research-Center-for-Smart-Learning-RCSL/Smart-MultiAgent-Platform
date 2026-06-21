<template>
  <section
    v-if="query.data.value"
    class="admin-user-detail"
  >
    <h1>{{ query.data.value.email }}</h1>
    <dl>
      <dt>{{ $t('admin.userDetail.id') }}</dt><dd>{{ query.data.value.id }}</dd>
      <dt>{{ $t('admin.users.status') }}</dt><dd>{{ query.data.value.status }}</dd>
      <dt>{{ $t('admin.users.verified') }}</dt><dd>{{ query.data.value.email_verified ? $t('admin.common.yes') : $t('admin.common.no') }}</dd>
      <dt>{{ $t('admin.userDetail.isAdmin') }}</dt><dd>{{ query.data.value.is_admin ? $t('admin.common.yes') : $t('admin.common.no') }}</dd>
      <dt>{{ $t('admin.userDetail.bannedReason') }}</dt><dd>{{ query.data.value.banned_reason ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.bannedAt') }}</dt><dd>{{ query.data.value.banned_at ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.deletedAt') }}</dt><dd>{{ query.data.value.deleted_at ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.lastLogin') }}</dt><dd>{{ query.data.value.last_login_at ?? '-' }}</dd>
      <dt>{{ $t('admin.users.created') }}</dt><dd>{{ query.data.value.created_at }}</dd>
      <dt>{{ $t('admin.userDetail.orgs') }}</dt><dd>{{ query.data.value.org_ids.length }}</dd>
      <dt>{{ $t('admin.userDetail.projects') }}</dt><dd>{{ query.data.value.project_ids.length }}</dd>
    </dl>

    <AdminUserActions
      :user="query.data.value"
      :is-pending="actionPending"
      @ban="actions.promptBan(userId)"
      @unban="actions.unbanUser.mutate(userId)"
      @soft-delete="onSoftDelete"
      @hard-delete="onHardDelete"
      @impersonate="onImpersonate"
    />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { useRoute } from 'vue-router'
import { useConfirmDialog } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { useImpersonation } from '../composables/useImpersonation'
import AdminUserActions from '../components/AdminUserActions.vue'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const route = useRoute()
const userId = route.params.userId as string

const query = useQuery({
  queryKey: adminKeys.user(userId),
  queryFn: () => adminApi.getUser(userId).then(r => r.data),
})

const actions = useAdminActions()
const { startImpersonation } = useImpersonation()

const actionPending = computed(() =>
  actions.banUser.isPending.value
  || actions.unbanUser.isPending.value
  || actions.softDeleteUser.isPending.value
  || actions.hardDeleteUser.isPending.value
  || startImpersonation.isPending.value,
)

async function onSoftDelete(): Promise<void> {
  const ok = await confirm({
    title: t('admin.userDetail.softDeleteTitle'),
    message: t('admin.userDetail.softDeleteMessage'),
    confirmLabel: t('admin.userDetail.softDeleteConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  actions.softDeleteUser.mutate(userId)
}

async function onHardDelete(): Promise<void> {
  const ok = await confirm({
    title: t('admin.userDetail.hardDeleteTitle'),
    message: t('admin.userDetail.hardDeleteMessage'),
    confirmLabel: t('admin.userDetail.hardDeleteConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'error',
  })
  if (!ok) return
  actions.hardDeleteUser.mutate(userId)
}

async function onImpersonate(): Promise<void> {
  const ok = await confirm({
    title: t('admin.userDetail.impersonateTitle'),
    message: t('admin.userDetail.impersonateMessage'),
    confirmLabel: t('admin.userDetail.impersonateConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  startImpersonation.mutate(userId)
}
</script>

<style scoped>
dl { display: grid; grid-template-columns: 12rem 1fr; gap: 0.25rem 1rem; }
dt { font-weight: 600; }
</style>
