<template>
  <section class="admin-metrics">
    <SPageHeader :title="$t('admin.metrics.title')" />

    <SLoadingSpinner
      v-if="query.isLoading.value"
      class="my-4"
      :label="$t('admin.common.loading')"
    />
    <SAlert
      v-else-if="query.isError.value"
      variant="danger"
      class="my-4"
      role="alert"
    >
      {{ $t('admin.metrics.error') }}
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
    <div
      v-else-if="query.data.value"
      class="admin-metrics__grid"
    >
      <SStatTile
        v-for="stat in stats"
        :key="stat.label"
        class="admin-metrics__card"
        :icon="stat.icon"
        :label="$t(stat.label)"
        :value="stat.value"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  UsersIcon,
  BuildingOffice2Icon,
  FolderIcon,
  ClipboardDocumentListIcon,
} from '@heroicons/vue/24/outline'
import { SPageHeader, SStatTile, SLoadingSpinner, SAlert, SButton } from '@shared/ui'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const query = useQuery({
  queryKey: adminKeys.metrics(),
  queryFn: () => adminApi.getMetrics(),
})

const stats = computed(() => {
  const m = query.data.value
  if (!m) return []
  return [
    { label: 'admin.metrics.totalUsers', value: m.total_users, icon: UsersIcon },
    { label: 'admin.metrics.totalOrgs', value: m.total_orgs, icon: BuildingOffice2Icon },
    { label: 'admin.metrics.totalProjects', value: m.total_projects, icon: FolderIcon },
    { label: 'admin.metrics.totalAuditEntries', value: m.total_audit_entries, icon: ClipboardDocumentListIcon },
  ]
})
</script>

<style scoped>
.admin-metrics__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-4);
  margin: var(--space-4) 0;
}
.admin-metrics__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-1);
}
</style>
