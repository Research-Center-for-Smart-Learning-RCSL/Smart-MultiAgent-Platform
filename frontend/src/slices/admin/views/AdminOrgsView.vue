<template>
  <section class="admin-orgs">
    <h1>{{ $t('admin.orgs.title') }}</h1>
    <table v-if="query.data.value">
      <thead>
        <tr>
          <th>{{ $t('admin.orgs.name') }}</th>
          <th>{{ $t('admin.orgs.creator') }}</th>
          <th>{{ $t('admin.users.created') }}</th>
          <th>{{ $t('admin.orgs.deleted') }}</th>
          <th>{{ $t('admin.users.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="org in query.data.value"
          :key="org.id"
        >
          <td>{{ org.name }}</td>
          <td>{{ org.creator_user_id }}</td>
          <td>{{ new Date(org.created_at).toLocaleDateString() }}</td>
          <td>{{ org.deleted_at ? new Date(org.deleted_at).toLocaleDateString() : '-' }}</td>
          <td>
            <button
              v-if="!org.deleted_at"
              class="btn btn-danger btn-sm"
              @click="onForceDelete(org.id, org.name)"
            >
              {{ $t('admin.orgs.forceDelete') }}
            </button>
            <button
              v-if="org.deleted_at"
              class="btn btn-sm"
              @click="actions.restoreResource.mutate({ type: 'org', id: org.id })"
            >
              {{ $t('admin.orgs.restore') }}
            </button>
            <button
              v-if="!org.deleted_at"
              class="btn btn-sm"
              @click="onTransfer(org.id)"
            >
              {{ $t('admin.orgs.forceTransfer') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useConfirmDialog, useToast } from '@shared/composables'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()
const qc = useQueryClient()
const toast = useToast()
const { confirm, prompt } = useConfirmDialog()

const query = useQuery({
  queryKey: adminKeys.orgs(),
  queryFn: () => adminApi.listOrgs().then(r => r.data),
})

const actions = useAdminActions()

const transferMutation = useMutation({
  mutationFn: ({ orgId, targetUserId }: { orgId: string; targetUserId: string }) =>
    adminApi.forceTransferOC(orgId, targetUserId),
  onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
  onError: () => toast.error(t('admin.orgs.transferFailed')),
})

async function onForceDelete(orgId: string, orgName: string): Promise<void> {
  const ok = await confirm({
    title: t('admin.orgs.forceDeleteTitle'),
    message: t('admin.orgs.forceDeleteMessage', { name: orgName }),
    confirmLabel: t('admin.orgs.forceDeleteConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'error',
  })
  if (!ok) return
  actions.forceDeleteOrg.mutate(orgId)
}

async function onTransfer(orgId: string): Promise<void> {
  const targetUserId = await prompt({
    title: t('admin.orgs.forceTransferTitle'),
    message: t('admin.orgs.forceTransferMessage'),
    confirmLabel: t('admin.orgs.forceTransferConfirm'),
    cancelLabel: t('app.cancel'),
    inputPattern: /\S+/,
    inputErrorMessage: t('admin.orgs.forceTransferUserIdRequired'),
    variant: 'warning',
  })
  if (targetUserId) transferMutation.mutate({ orgId, targetUserId })
}
</script>

<style scoped>
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--color-border); text-align: left; }
td button + button { margin-left: 0.25rem; }
</style>
