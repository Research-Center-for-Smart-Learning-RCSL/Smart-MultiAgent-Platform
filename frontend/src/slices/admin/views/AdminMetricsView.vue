<template>
  <section class="admin-metrics">
    <h1>{{ $t('admin.metrics.title') }}</h1>
    <div
      v-if="query.data.value"
      class="admin-metrics__grid"
    >
      <div class="admin-metrics__card">
        <span class="admin-metrics__value">{{ query.data.value.total_users }}</span>
        <span class="admin-metrics__label">{{ $t('admin.metrics.totalUsers') }}</span>
      </div>
      <div class="admin-metrics__card">
        <span class="admin-metrics__value">{{ query.data.value.total_orgs }}</span>
        <span class="admin-metrics__label">{{ $t('admin.metrics.totalOrgs') }}</span>
      </div>
      <div class="admin-metrics__card">
        <span class="admin-metrics__value">{{ query.data.value.total_projects }}</span>
        <span class="admin-metrics__label">{{ $t('admin.metrics.totalProjects') }}</span>
      </div>
      <div class="admin-metrics__card">
        <span class="admin-metrics__value">{{ query.data.value.total_audit_entries }}</span>
        <span class="admin-metrics__label">{{ $t('admin.metrics.totalAuditEntries') }}</span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const query = useQuery({
  queryKey: adminKeys.metrics(),
  queryFn: () => adminApi.getMetrics().then(r => r.data),
})
</script>

<style scoped>
.admin-metrics__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: 1rem;
  margin: 1rem 0;
}
.admin-metrics__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1.5rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.admin-metrics__value { font-size: 2rem; font-weight: 700; }
.admin-metrics__label { font-size: 0.875rem; color: var(--color-muted); }
</style>
