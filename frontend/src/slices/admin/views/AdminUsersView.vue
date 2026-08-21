<template>
  <section class="admin-users">
    <SPageHeader :title="$t('admin.users.title')">
      <template #actions>
        <SButton
          variant="primary"
          size="sm"
          @click="showCreate = true"
        >
          {{ $t('admin.users.create') }}
        </SButton>
      </template>
    </SPageHeader>

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
      :data="rows"
      :loading="query.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="id"
    >
      <template #cell-email="{ row }">
        <router-link :to="{ name: 'admin.userDetail', params: { userId: row.id } }">
          {{ row.email }}
        </router-link>
      </template>

      <template #cell-display_name="{ row }">
        {{ row.display_name ?? '-' }}
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
          @click="onUnban(row.id)"
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

    <AdminCreateUserDialog
      :open="showCreate"
      :pending="actions.createUser.isPending.value"
      @close="showCreate = false"
      @submit="onCreate"
    />

    <AdminActivationLinksDialog
      v-if="provisioned"
      :open="true"
      :email="provisioned.user.email"
      :links="provisioned.activation_links"
      @close="dismissLinks"
    />
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
import { useConfirmDialog, useToast } from '@shared/composables'
import type { Column } from '@shared/ui/STable.vue'
import { formatDate } from '@shared/utils/datetime'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { userStatusLabelKey } from '../utils/userStatus'
import AdminActivationLinksDialog from '../components/AdminActivationLinksDialog.vue'
import AdminCreateUserDialog from '../components/AdminCreateUserDialog.vue'
import type { ProvisionedUser, UserSummary } from '../types'

const { t } = useI18n()
const toast = useToast()

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
  { key: 'display_name', label: t('admin.users.displayName'), width: '160px' },
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
      // Omit falsy keys entirely rather than assigning an explicit
      // `undefined` value (exactOptionalPropertyTypes).
      ...(params?.q ? { q: params.q } : {}),
      ...(params?.status ? { status: params.status } : {}),
    })
  },
})

// STable's generic constrains T to Record<string, unknown>; UserSummary has no
// index signature by design, so intersect it in only for this cast (the
// runtime shape is unchanged — plain user-summary objects from the API).
type UserRow = UserSummary & Record<string, unknown>

const rows = computed<UserRow[]>(() => (query.data.value ?? []) as unknown as UserRow[])

const actions = useAdminActions()
const { confirm } = useConfirmDialog()

const showCreate = ref(false)
// Held in memory only, for as long as the links dialog is open. The backend
// returns the two links once and never again from a read path (R6.18), so
// caching them anywhere would be a second copy of a bearer credential.
const provisioned = ref<ProvisionedUser | null>(null)

// The mutation's own `data` is that second copy: vue-query keeps the last
// result for the lifetime of the mounted view, outliving the dialog. Reset it
// with the ref so the links really do live only as long as they are shown.
function dismissLinks(): void {
  provisioned.value = null
  actions.createUser.reset()
}

function onCreate(payload: { email: string; displayName: string | null }): void {
  actions.createUser.mutate(payload, {
    onSuccess: (result) => {
      showCreate.value = false
      provisioned.value = result
      toast.success(t('admin.users.created'))
    },
  })
}

async function onUnban(userId: string): Promise<void> {
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
