<template>
  <section
    v-if="query.data.value"
    class="admin-user-detail"
  >
    <h1>{{ query.data.value.email }}</h1>
    <dl>
      <dt>ID</dt><dd>{{ query.data.value.id }}</dd>
      <dt>{{ $t('admin.users.status') }}</dt><dd>{{ query.data.value.status }}</dd>
      <dt>{{ $t('admin.users.verified') }}</dt><dd>{{ query.data.value.email_verified ? 'Yes' : 'No' }}</dd>
      <dt>{{ $t('admin.userDetail.isAdmin') }}</dt><dd>{{ query.data.value.is_admin ? 'Yes' : 'No' }}</dd>
      <dt>{{ $t('admin.userDetail.bannedReason') }}</dt><dd>{{ query.data.value.banned_reason ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.bannedAt') }}</dt><dd>{{ query.data.value.banned_at ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.deletedAt') }}</dt><dd>{{ query.data.value.deleted_at ?? '-' }}</dd>
      <dt>{{ $t('admin.userDetail.lastLogin') }}</dt><dd>{{ query.data.value.last_login_at ?? '-' }}</dd>
      <dt>{{ $t('admin.users.created') }}</dt><dd>{{ query.data.value.created_at }}</dd>
      <dt>{{ $t('admin.userDetail.orgs') }}</dt><dd>{{ query.data.value.org_ids.length }}</dd>
      <dt>{{ $t('admin.userDetail.projects') }}</dt><dd>{{ query.data.value.project_ids.length }}</dd>
    </dl>

    <div class="admin-user-detail__actions">
      <button
        v-if="query.data.value.status === 'active'"
        @click="onBan"
      >
        {{ $t('admin.users.ban') }}
      </button>
      <button
        v-if="query.data.value.status === 'banned'"
        @click="actions.unbanUser.mutate(userId)"
      >
        {{ $t('admin.users.unban') }}
      </button>
      <button
        v-if="query.data.value.status === 'active'"
        @click="onSoftDelete"
      >
        {{ $t('admin.userDetail.softDelete') }}
      </button>
      <button
        v-if="query.data.value.deleted_at"
        class="admin-user-detail__danger"
        @click="onHardDelete"
      >
        {{ $t('admin.userDetail.hardDelete') }}
      </button>
      <button
        v-if="query.data.value.status === 'active'"
        @click="onImpersonate"
      >
        {{ $t('admin.userDetail.impersonate') }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { useImpersonation } from '../composables/useImpersonation'

const route = useRoute()
const userId = route.params.userId as string

const query = useQuery({
  queryKey: adminKeys.user(userId),
  queryFn: () => adminApi.getUser(userId).then(r => r.data),
})

const actions = useAdminActions()
const { startImpersonation } = useImpersonation()

async function onBan(): Promise<void> {
  try {
    const { value: reason } = await ElMessageBox.prompt(
      'Provide a reason for banning this user:',
      'Ban User',
      { confirmButtonText: 'Ban', cancelButtonText: 'Cancel', inputPattern: /\S+/, inputErrorMessage: 'Reason is required' },
    )
    if (reason) actions.banUser.mutate({ userId, reason })
  } catch {
    // cancelled
  }
}

async function onSoftDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      'This will deactivate the account. The user will not be able to log in.',
      'Delete User',
      { confirmButtonText: 'Delete', cancelButtonText: 'Cancel', type: 'warning' },
    )
    actions.softDeleteUser.mutate(userId)
  } catch {
    // cancelled
  }
}

async function onHardDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      'This will permanently destroy all user data. This action cannot be undone.',
      'Permanently Delete User',
      { confirmButtonText: 'Delete Forever', cancelButtonText: 'Cancel', type: 'error' },
    )
    actions.hardDeleteUser.mutate(userId)
  } catch {
    // cancelled
  }
}

async function onImpersonate(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      'Start a read-only impersonation session for this user?',
      'Impersonate User',
      { confirmButtonText: 'Start', cancelButtonText: 'Cancel', type: 'warning' },
    )
    startImpersonation.mutate(userId)
  } catch {
    // cancelled
  }
}
</script>

<style scoped>
dl { display: grid; grid-template-columns: 12rem 1fr; gap: 0.25rem 1rem; }
dt { font-weight: 600; }
.admin-user-detail__actions { display: flex; gap: 0.5rem; margin-top: 1rem; }
.admin-user-detail__danger { color: var(--color-danger, #dc2626); }
</style>
