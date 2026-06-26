<template>
  <section class="admin-admins">
    <SPageHeader :title="$t('admin.admins.title')" />

    <form
      class="admin-admins__promote"
      @submit.prevent="onPromote"
    >
      <SInput
        v-model="promoteUserId"
        class="admin-admins__input"
        :placeholder="$t('admin.admins.userIdPlaceholder')"
      />
      <SButton
        type="submit"
        variant="primary"
        :loading="actions.promoteAdmin.isPending.value"
      >
        {{ $t('admin.admins.promote') }}
      </SButton>
    </form>

    <SAlert
      v-if="promoteError"
      variant="danger"
      class="mt-2"
      role="alert"
    >
      {{ promoteError }}
    </SAlert>

    <SQueryError
      v-if="query.isError.value"
      class="mt-4"
      :message="$t('admin.common.loadError')"
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
      row-key="user_id"
    >
      <template #cell-user_id="{ row }">
        <code class="font-mono text-[0.8125rem]">{{ row.user_id }}</code>
      </template>

      <template #cell-promoted_by_user_id="{ row }">
        {{ row.promoted_by_user_id ?? '-' }}
      </template>

      <template #cell-promoted_at="{ row }">
        {{ formatDate(row.promoted_at) }}
      </template>

      <template #actions="{ row }">
        <SButton
          variant="danger"
          size="sm"
          :loading="actions.demoteAdmin.isPending.value"
          @click="onDemote(row.user_id)"
        >
          {{ $t('admin.admins.demote') }}
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="ShieldCheckIcon"
          :text="$t('admin.admins.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ShieldCheckIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SInput,
  SAlert,
  SQueryError,
  SEmptyState,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDate } from '@shared/utils/datetime'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useConfirmDialog } from '@shared/composables'
import { useAdminActions } from '../composables/useAdminActions'
import { isProblemWithType } from '@shared/transport'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const promoteUserId = ref('')
const promoteError = ref<string | null>(null)

const columns = computed<Column[]>(() => [
  { key: 'user_id', label: t('admin.admins.userId') },
  { key: 'promoted_by_user_id', label: t('admin.admins.promotedBy') },
  { key: 'promoted_at', label: t('admin.admins.promotedAt'), width: '160px' },
  { key: 'actions', label: t('admin.users.actions'), width: '120px', align: 'right' },
])

const query = useQuery({
  queryKey: adminKeys.admins(),
  queryFn: () => adminApi.listAdmins(),
})

const actions = useAdminActions()

async function onPromote(): Promise<void> {
  promoteError.value = null
  try {
    await actions.promoteAdmin.mutateAsync(promoteUserId.value.trim())
    promoteUserId.value = ''
  } catch {
    promoteError.value = t('admin.users.promotionFailed')
  }
}

async function onDemote(userId: string): Promise<void> {
  promoteError.value = null
  const ok = await confirm({
    title: t('admin.admins.demoteTitle'),
    message: t('admin.admins.demoteMessage'),
    confirmLabel: t('admin.admins.demoteConfirm'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await actions.demoteAdmin.mutateAsync(userId)
  } catch (e) {
    if (isProblemWithType(e, 'admin/last-admin')) {
      promoteError.value = t('admin.users.lastAdminDemote')
    } else {
      promoteError.value = t('admin.users.demotionFailed')
    }
  }
}
</script>

<style scoped>
.admin-admins__promote {
  display: flex;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: center;
}
.admin-admins__input {
  flex: 1 1 24rem;
  max-width: 32rem;
}
</style>
