<template>
  <section class="admin-user-detail">
    <SLoadingSpinner
      v-if="query.isPending.value"
      class="my-4"
      :label="$t('admin.common.loading')"
    />
    <SAlert
      v-else-if="query.isError.value || !query.data.value"
      variant="danger"
      class="my-4"
      role="alert"
    >
      {{ $t('admin.userDetail.notFound') }}
    </SAlert>
    <template v-else>
      <SPageHeader :title="query.data.value.email" />

      <SCard class="mt-4">
        <dl class="admin-user-detail__fields">
          <dt>{{ $t('admin.userDetail.id') }}</dt>
          <dd><code class="font-mono text-[0.8125rem]">{{ query.data.value.id }}</code></dd>

          <dt>{{ $t('admin.users.status') }}</dt>
          <dd>
            <SStatusBadge :status="query.data.value.status">
              {{ $t(userStatusLabelKey(query.data.value.status)) }}
            </SStatusBadge>
          </dd>

          <dt>{{ $t('admin.users.verified') }}</dt>
          <dd>{{ query.data.value.email_verified ? $t('admin.common.yes') : $t('admin.common.no') }}</dd>

          <dt>{{ $t('admin.userDetail.isAdmin') }}</dt>
          <dd>{{ query.data.value.is_admin ? $t('admin.common.yes') : $t('admin.common.no') }}</dd>

          <dt>{{ $t('admin.userDetail.bannedReason') }}</dt>
          <dd>{{ query.data.value.banned_reason ?? '-' }}</dd>

          <dt>{{ $t('admin.userDetail.bannedAt') }}</dt>
          <dd>{{ query.data.value.banned_at ?? '-' }}</dd>

          <dt>{{ $t('admin.userDetail.deletedAt') }}</dt>
          <dd>{{ query.data.value.deleted_at ?? '-' }}</dd>

          <dt>{{ $t('admin.userDetail.lastLogin') }}</dt>
          <dd>{{ query.data.value.last_login_at ?? '-' }}</dd>

          <dt>{{ $t('admin.users.created') }}</dt>
          <dd>{{ query.data.value.created_at }}</dd>

          <dt>{{ $t('admin.userDetail.orgs') }}</dt>
          <dd>{{ query.data.value.org_ids.length }}</dd>

          <dt>{{ $t('admin.userDetail.projects') }}</dt>
          <dd>{{ query.data.value.project_ids.length }}</dd>
        </dl>

        <AdminUserActions
          :user="query.data.value"
          :is-pending="actionPending"
          @ban="actions.promptBan(userId)"
          @unban="onUnban"
          @soft-delete="onSoftDelete"
          @hard-delete="onHardDelete"
          @impersonate="onImpersonate"
        />
      </SCard>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { useRoute } from 'vue-router'
import { useConfirmDialog } from '@shared/composables'
import { SPageHeader, SCard, SStatusBadge, SLoadingSpinner, SAlert } from '@shared/ui'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { useImpersonation } from '../composables/useImpersonation'
import { userStatusLabelKey } from '../utils/userStatus'
import AdminUserActions from '../components/AdminUserActions.vue'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const route = useRoute()
const userId = route.params.userId as string

const query = useQuery({
  queryKey: adminKeys.user(userId),
  queryFn: () => adminApi.getUser(userId),
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

async function onUnban(): Promise<void> {
  const ok = await confirm({
    title: t('admin.users.unbanTitle'),
    message: t('admin.users.unbanMessage'),
    confirmLabel: t('admin.users.unbanConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  actions.unbanUser.mutate(userId)
}

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
.admin-user-detail__fields {
  display: grid;
  grid-template-columns: 12rem 1fr;
  gap: 0.5rem 1rem;
  align-items: center;
}
.admin-user-detail__fields dt {
  font-weight: 600;
  color: var(--color-muted);
}
.admin-user-detail__fields dd {
  margin: 0;
  word-break: break-all;
}
</style>
