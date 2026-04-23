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
        <tr v-for="org in query.data.value" :key="org.id">
          <td>{{ org.name }}</td>
          <td>{{ org.creator_user_id }}</td>
          <td>{{ new Date(org.created_at).toLocaleDateString() }}</td>
          <td>{{ org.deleted_at ? new Date(org.deleted_at).toLocaleDateString() : '-' }}</td>
          <td>
            <button
              v-if="!org.deleted_at"
              @click="actions.forceDeleteOrg.mutate(org.id)"
            >
              {{ $t('admin.orgs.forceDelete') }}
            </button>
            <button
              v-if="org.deleted_at"
              @click="actions.restoreResource.mutate({ type: 'org', id: org.id })"
            >
              {{ $t('admin.orgs.restore') }}
            </button>
            <button v-if="!org.deleted_at" @click="onTransfer(org.id)">
              {{ $t('admin.orgs.forceTransfer') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const qc = useQueryClient()

const query = useQuery({
  queryKey: adminKeys.orgs(),
  queryFn: () => adminApi.listOrgs().then(r => r.data),
})

const actions = useAdminActions()

const transferMutation = useMutation({
  mutationFn: ({ orgId, targetUserId }: { orgId: string; targetUserId: string }) =>
    adminApi.forceTransferOC(orgId, targetUserId),
  onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
})

function onTransfer(orgId: string): void {
  const targetUserId = window.prompt('Target user ID for OC transfer:')
  if (targetUserId) {
    transferMutation.mutate({ orgId, targetUserId })
  }
}
</script>

<style scoped>
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--color-border, #eee); text-align: left; }
td button + button { margin-left: 0.25rem; }
</style>
