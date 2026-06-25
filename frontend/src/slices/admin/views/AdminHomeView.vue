<template>
  <section class="admin-home">
    <SPageHeader :title="$t('admin.home.title')" />

    <nav class="admin-home__nav">
      <router-link
        v-for="item in navItems"
        :key="item.name"
        :to="{ name: item.name }"
        class="admin-home__nav-link"
      >
        <component
          :is="item.icon"
          class="w-5 h-5"
          aria-hidden="true"
        />
        <span>{{ $t(item.label) }}</span>
      </router-link>
    </nav>

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
      <SCard
        v-for="stat in stats"
        :key="stat.label"
        class="admin-home__card"
      >
        <span class="admin-home__value">{{ stat.value }}</span>
        <span class="admin-home__label">{{ $t(stat.label) }}</span>
      </SCard>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  UsersIcon,
  ShieldCheckIcon,
  NoSymbolIcon,
  BuildingOffice2Icon,
  FolderIcon,
  ClipboardDocumentListIcon,
  WrenchScrewdriverIcon,
  AdjustmentsHorizontalIcon,
  ChartBarIcon,
} from '@heroicons/vue/24/outline'
import { SPageHeader, SLoadingSpinner, SCard, SAlert, SButton } from '@shared/ui'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const navItems = [
  { name: 'admin.users', label: 'admin.nav.users', icon: UsersIcon },
  { name: 'admin.admins', label: 'admin.nav.admins', icon: ShieldCheckIcon },
  { name: 'admin.ipBans', label: 'admin.nav.ipBans', icon: NoSymbolIcon },
  { name: 'admin.orgs', label: 'admin.nav.orgs', icon: BuildingOffice2Icon },
  { name: 'admin.projects', label: 'admin.nav.projects', icon: FolderIcon },
  { name: 'admin.audit', label: 'admin.nav.audit', icon: ClipboardDocumentListIcon },
  { name: 'admin.ops', label: 'admin.nav.ops', icon: WrenchScrewdriverIcon },
  { name: 'admin.rateLimits', label: 'admin.nav.rateLimits', icon: AdjustmentsHorizontalIcon },
  { name: 'admin.metrics', label: 'admin.nav.metrics', icon: ChartBarIcon },
] as const

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
.admin-home__nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 1rem 0;
}
.admin-home__nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-fg);
  text-decoration: none;
  transition: background var(--transition-fast);
}
.admin-home__nav-link:hover {
  background: var(--color-sidebar-hover);
}
.admin-home__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: 1rem;
  margin-top: 1rem;
}
.admin-home__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.admin-home__value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--color-fg);
}
.admin-home__label {
  font-size: 0.875rem;
  color: var(--color-muted);
}

@media (max-width: 768px) {
  .admin-home__nav {
    flex-direction: column;
  }
  .admin-home__nav-link {
    width: 100%;
  }
}
</style>
