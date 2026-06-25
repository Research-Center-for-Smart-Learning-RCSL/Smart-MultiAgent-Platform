<template>
  <section class="admin-orgs">
    <SPageHeader :title="$t('admin.orgs.title')" />

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
      row-key="id"
    >
      <template #cell-creator_user_id="{ row }">
        <code class="font-mono text-[0.8125rem]">{{ row.creator_user_id }}</code>
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #cell-deleted_at="{ row }">
        <SBadge
          v-if="row.deleted_at"
          variant="danger"
        >
          {{ formatDate(row.deleted_at!) }}
        </SBadge>
        <span v-else>-</span>
      </template>

      <template #actions="{ row }">
        <template v-if="!row.deleted_at">
          <SButton
            variant="danger"
            size="sm"
            @click="onForceDelete(row.id, row.name)"
          >
            {{ $t('admin.orgs.forceDelete') }}
          </SButton>
          <SButton
            variant="secondary"
            size="sm"
            @click="onTransfer(row.id)"
          >
            {{ $t('admin.orgs.forceTransfer') }}
          </SButton>
        </template>
        <SButton
          v-else
          variant="secondary"
          size="sm"
          @click="actions.restoreResource.mutate({ type: 'org', id: row.id })"
        >
          {{ $t('admin.orgs.restore') }}
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="BuildingOffice2Icon"
          :text="$t('admin.orgs.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { BuildingOffice2Icon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SBadge,
  SQueryError,
  SEmptyState,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDate } from '@shared/utils/datetime'
import { useQuery } from '@tanstack/vue-query'
import { useConfirmDialog } from '@shared/composables'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()
const { confirm, prompt } = useConfirmDialog()

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('admin.orgs.name') },
  { key: 'creator_user_id', label: t('admin.orgs.creator') },
  { key: 'created_at', label: t('admin.users.created'), width: '140px' },
  { key: 'deleted_at', label: t('admin.orgs.deleted'), width: '140px' },
  { key: 'actions', label: t('admin.users.actions'), width: '240px', align: 'right' },
])

const query = useQuery({
  queryKey: adminKeys.orgs(),
  queryFn: () => adminApi.listOrgs(),
})

const actions = useAdminActions()

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
  if (targetUserId) actions.forceTransferOrg.mutate({ orgId, targetUserId })
}
</script>
