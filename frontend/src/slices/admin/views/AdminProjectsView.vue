<template>
  <section class="admin-projects">
    <SPageHeader :title="$t('admin.projects.title')" />

    <SAlert
      v-if="query.isError.value"
      variant="danger"
      class="mt-4"
      role="alert"
    >
      {{ $t('admin.common.loadError') }}
      <template #actions>
        <SButton
          size="sm"
          variant="secondary"
          @click="query.refetch()"
        >
          {{ $t('admin.common.retry') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      v-else
      class="mt-4"
      :columns="columns"
      :data="query.data.value ?? []"
      :loading="query.isPending.value"
      row-key="id"
    >
      <template #cell-owner_user_id="{ row }">
        {{ row.owner_user_id ?? '-' }}
      </template>

      <template #cell-owner_org_id="{ row }">
        {{ row.owner_org_id ?? '-' }}
      </template>

      <template #cell-created_at="{ row }">
        {{ new Date(row.created_at).toLocaleDateString() }}
      </template>

      <template #cell-deleted_at="{ row }">
        {{ row.deleted_at ? new Date(row.deleted_at).toLocaleDateString() : '-' }}
      </template>

      <template #empty>
        <SEmptyState
          :icon="FolderIcon"
          :text="$t('admin.projects.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { FolderIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, STable, SButton, SAlert, SEmptyState } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const { t } = useI18n()

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('admin.projects.name') },
  { key: 'owner_user_id', label: t('admin.projects.ownerUser') },
  { key: 'owner_org_id', label: t('admin.projects.ownerOrg') },
  { key: 'created_at', label: t('admin.users.created'), width: '140px' },
  { key: 'deleted_at', label: t('admin.orgs.deleted'), width: '140px' },
])

const query = useQuery({
  queryKey: adminKeys.projects(),
  queryFn: () => adminApi.listProjects(),
})
</script>
