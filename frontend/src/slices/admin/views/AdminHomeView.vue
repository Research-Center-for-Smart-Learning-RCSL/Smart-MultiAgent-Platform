<template>
  <section class="admin-home">
    <SPageHeader :title="$t('admin.home.title')" />

    <SLoadingSpinner
      v-if="metricsQuery.isLoading.value"
      class="my-4"
      :label="$t('admin.common.loading')"
    />
    <SAlert
      v-else-if="metricsQuery.isError.value"
      variant="danger"
      class="my-4"
      role="alert"
    >
      {{ $t('admin.home.metricsError') }}
      <template #actions>
        <SButton
          size="sm"
          variant="secondary"
          @click="metricsQuery.refetch()"
        >
          {{ $t('admin.common.retry') }}
        </SButton>
      </template>
    </SAlert>
    <div
      v-else-if="metricsQuery.data.value"
      class="admin-home__stats"
    >
      <SStatTile
        v-for="stat in stats"
        :key="stat.label"
        class="admin-home__card"
        :label="$t(stat.label)"
        :value="stat.value"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { SPageHeader, SLoadingSpinner, SStatTile, SAlert, SButton } from '@shared/ui'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const metricsQuery = useQuery({
  queryKey: adminKeys.metrics(),
  queryFn: () => adminApi.getMetrics(),
})

const stats = computed(() => {
  const m = metricsQuery.data.value
  if (!m) return []
  return [
    { label: 'admin.metrics.totalUsers', value: m.total_users },
    { label: 'admin.metrics.totalOrgs', value: m.total_orgs },
    { label: 'admin.metrics.totalProjects', value: m.total_projects },
    { label: 'admin.metrics.totalAuditEntries', value: m.total_audit_entries },
  ]
})
</script>

<style scoped>
.admin-home__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-4);
}
.admin-home__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
</style>
