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
        :aria-label="$t('admin.users.search')"
      >
      <select
        v-model="statusFilter"
        :aria-label="$t('admin.users.status')"
      >
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
      <button
        type="submit"
        class="btn"
      >
        {{ $t('admin.users.search') }}
      </button>
    </form>
    <p
      v-if="query.isPending.value"
      class="admin-users__status"
      role="status"
    >
      {{ $t('admin.users.loading') }}
    </p>
    <p
      v-else-if="query.isError.value"
      class="admin-users__status admin-users__status--error"
      role="alert"
    >
      {{ $t('admin.users.loadError') }}
      <button
        type="button"
        class="btn"
        @click="query.refetch()"
      >
        {{ $t('admin.users.retry') }}
      </button>
    </p>
    <table v-else-if="query.data.value && query.data.value.length">
      <thead>
        <tr>
          <th scope="col">
            {{ $t('admin.users.email') }}
          </th>
          <th scope="col">
            {{ $t('admin.users.status') }}
          </th>
          <th scope="col">
            {{ $t('admin.users.verified') }}
          </th>
          <th scope="col">
            {{ $t('admin.users.created') }}
          </th>
          <th scope="col">
            {{ $t('admin.users.actions') }}
          </th>
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
          <td>{{ user.email_verified ? $t('admin.common.yes') : $t('admin.common.no') }}</td>
          <td>{{ new Date(user.created_at).toLocaleDateString() }}</td>
          <td>
            <button
              v-if="user.status === 'active'"
              class="btn btn-danger btn-sm"
              @click="actions.promptBan(user.id)"
            >
              {{ $t('admin.users.ban') }}
            </button>
            <button
              v-if="user.status === 'banned'"
              class="btn btn-sm"
              @click="actions.unbanUser.mutate(user.id)"
            >
              {{ $t('admin.users.unban') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p
      v-else
      class="admin-users__status"
    >
      {{ appliedQ || appliedStatus ? $t('admin.users.emptyFiltered') : $t('admin.users.empty') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
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
  queryFn: ({ queryKey }) => {
    const params = queryKey[2] as { q?: string; status?: string } | undefined
    return adminApi.listUsers({
      q: params?.q || undefined,
      status: params?.status || undefined,
    }).then(r => r.data)
  },
})

const actions = useAdminActions()
</script>

<style scoped>
.admin-users__filters {
  display: flex;
  gap: 0.5rem;
  margin: 1rem 0;
}
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--color-border); text-align: left; }
</style>
