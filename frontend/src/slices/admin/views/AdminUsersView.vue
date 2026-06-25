<template>
  <section class="admin-users">
    <SPageHeader :title="$t('admin.users.title')" />

    <form
      class="admin-users__filters"
      @submit.prevent="applySearch"
    >
      <SSearchInput
        v-model="searchQuery"
        class="admin-users__search"
        :placeholder="$t('admin.users.searchPlaceholder')"
        @search="applySearch"
      />
      <SSelect
        v-model="statusFilter"
        class="admin-users__status-select"
        :options="statusOptions"
        :aria-label="$t('admin.users.status')"
      />
      <SButton
        type="submit"
        variant="primary"
      >
        {{ $t('admin.users.search') }}
      </SButton>
    </form>

    <SQueryError
      v-if="query.isError.value"
      class="mt-4"
      :message="$t('admin.users.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="query.refetch()"
    />

    <STable
      v-else
      class="mt-4"
      :columns="columns"
      :data="query.data.value ?? []"
      :loading="query.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="id"
    >
      <template #cell-email="{ row }">
        <router-link :to="{ name: 'admin.userDetail', params: { userId: row.id } }">
          {{ row.email }}
        </router-link>
      </template>

      <template #cell-status="{ row }">
        <SStatusBadge :status="row.status">
          {{ $t(userStatusLabelKey(row.status)) }}
        </SStatusBadge>
      </template>

      <template #cell-email_verified="{ row }">
        {{ row.email_verified ? $t('admin.common.yes') : $t('admin.common.no') }}
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #actions="{ row }">
        <SButton
          v-if="row.status === 'active'"
          variant="danger"
          size="sm"
          @click="actions.promptBan(row.id)"
        >
          {{ $t('admin.users.ban') }}
        </SButton>
        <SButton
          v-else-if="row.status === 'banned'"
          variant="secondary"
          size="sm"
          @click="actions.unbanUser.mutate(row.id)"
        >
          {{ $t('admin.users.unban') }}
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="UsersIcon"
          :text="appliedQ || appliedStatus ? $t('admin.users.emptyFiltered') : $t('admin.users.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { UsersIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SStatusBadge,
  SSelect,
  SSearchInput,
  SEmptyState,
  SQueryError,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDate } from '@shared/utils/datetime'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { userStatusLabelKey } from '../utils/userStatus'

const { t } = useI18n()

const searchQuery = ref('')
const statusFilter = ref('')
const appliedQ = ref('')
const appliedStatus = ref('')

const statusOptions = computed(() => [
  { value: '', label: t('admin.users.allStatuses') },
  { value: 'active', label: t('admin.users.statusActive') },
  { value: 'pending', label: t('admin.users.statusPending') },
  { value: 'banned', label: t('admin.users.statusBanned') },
  { value: 'deleted', label: t('admin.users.statusDeleted') },
])

const columns = computed<Column[]>(() => [
  { key: 'email', label: t('admin.users.email') },
  { key: 'status', label: t('admin.users.status'), width: '120px' },
  { key: 'email_verified', label: t('admin.users.verified'), width: '100px', align: 'center' },
  { key: 'created_at', label: t('admin.users.created'), width: '140px' },
  { key: 'actions', label: t('admin.users.actions'), width: '120px', align: 'right' },
])

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
    })
  },
})

const actions = useAdminActions()
</script>

<style scoped>
.admin-users__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: center;
}
.admin-users__search {
  flex: 1 1 18rem;
  max-width: 24rem;
}
.admin-users__status-select {
  width: 12rem;
}
</style>
