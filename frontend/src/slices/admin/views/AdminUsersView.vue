<template>
  <section class="admin-users">
    <h1>{{ $t('admin.users.title') }}</h1>
    <form
      class="admin-users__filters"
      @submit.prevent="applySearch"
    >
      <input
        v-model="searchQuery"
        type="text"
        :placeholder="$t('admin.users.searchPlaceholder')"
      >
      <select v-model="statusFilter">
        <option value="">
          {{ $t('admin.users.allStatuses') }}
        </option>
        <option value="active">
          {{ $t('admin.users.statusActive') }}
        </option>
        <option value="banned">
          {{ $t('admin.users.statusBanned') }}
        </option>
        <option value="deleted">
          {{ $t('admin.users.statusDeleted') }}
        </option>
      </select>
      <button type="submit">
        {{ $t('admin.users.search') }}
      </button>
    </form>
    <table v-if="query.data.value">
      <thead>
        <tr>
          <th>{{ $t('admin.users.email') }}</th>
          <th>{{ $t('admin.users.status') }}</th>
          <th>{{ $t('admin.users.verified') }}</th>
          <th>{{ $t('admin.users.created') }}</th>
          <th>{{ $t('admin.users.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="user in query.data.value"
          :key="user.id"
        >
          <td>
            <router-link :to="{ name: 'admin.userDetail', params: { userId: user.id } }">
              {{ user.email }}
            </router-link>
          </td>
          <td>{{ user.status }}</td>
          <td>{{ user.email_verified ? 'Yes' : 'No' }}</td>
          <td>{{ new Date(user.created_at).toLocaleDateString() }}</td>
          <td>
            <button
              v-if="user.status === 'active'"
              @click="onBan(user.id)"
            >
              {{ $t('admin.users.ban') }}
            </button>
            <button
              v-if="user.status === 'banned'"
              @click="actions.unbanUser.mutate(user.id)"
            >
              {{ $t('admin.users.unban') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { ElMessageBox } from 'element-plus'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const searchQuery = ref('')
const statusFilter = ref('')
const appliedQ = ref('')
const appliedStatus = ref('')

function applySearch(): void {
  appliedQ.value = searchQuery.value
  appliedStatus.value = statusFilter.value
}

const query = useQuery({
  queryKey: computed(() => adminKeys.users({ q: appliedQ.value, status: appliedStatus.value })),
  queryFn: () =>
    adminApi.listUsers({
      q: appliedQ.value || undefined,
      status: appliedStatus.value || undefined,
    }).then(r => r.data),
})

const actions = useAdminActions()

async function onBan(userId: string): Promise<void> {
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
</script>

<style scoped>
.admin-users__filters {
  display: flex;
  gap: 0.5rem;
  margin: 1rem 0;
}
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--color-border, #eee); text-align: left; }
</style>
