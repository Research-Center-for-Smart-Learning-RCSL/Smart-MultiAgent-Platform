<template>
  <section class="admin-home">
    <h1>{{ $t('admin.home.title') }}</h1>
    <nav class="admin-home__nav">
      <router-link :to="{ name: 'admin.users' }">
        {{ $t('admin.nav.users') }}
      </router-link>
      <router-link :to="{ name: 'admin.admins' }">
        {{ $t('admin.nav.admins') }}
      </router-link>
      <router-link :to="{ name: 'admin.ipBans' }">
        {{ $t('admin.nav.ipBans') }}
      </router-link>
      <router-link :to="{ name: 'admin.orgs' }">
        {{ $t('admin.nav.orgs') }}
      </router-link>
      <router-link :to="{ name: 'admin.projects' }">
        {{ $t('admin.nav.projects') }}
      </router-link>
      <router-link :to="{ name: 'admin.audit' }">
        {{ $t('admin.nav.audit') }}
      </router-link>
      <router-link :to="{ name: 'admin.ops' }">
        {{ $t('admin.nav.ops') }}
      </router-link>
      <router-link :to="{ name: 'admin.rateLimits' }">
        {{ $t('admin.nav.rateLimits') }}
      </router-link>
      <router-link :to="{ name: 'admin.metrics' }">
        {{ $t('admin.nav.metrics') }}
      </router-link>
    </nav>
    <div
      v-if="metricsQuery.data.value"
      class="admin-home__stats"
    >
      <dl>
        <dt>{{ $t('admin.metrics.totalUsers') }}</dt>
        <dd>{{ metricsQuery.data.value.total_users }}</dd>
        <dt>{{ $t('admin.metrics.totalOrgs') }}</dt>
        <dd>{{ metricsQuery.data.value.total_orgs }}</dd>
        <dt>{{ $t('admin.metrics.totalProjects') }}</dt>
        <dd>{{ metricsQuery.data.value.total_projects }}</dd>
        <dt>{{ $t('admin.metrics.totalAuditEntries') }}</dt>
        <dd>{{ metricsQuery.data.value.total_audit_entries }}</dd>
      </dl>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const metricsQuery = useQuery({
  queryKey: adminKeys.metrics(),
  queryFn: () => adminApi.getMetrics().then(r => r.data),
})
</script>

<style scoped>
.admin-home__nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 1rem 0;
}
.admin-home__nav a {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border, #ccc);
  border-radius: 4px;
  text-decoration: none;
}
.admin-home__stats dl {
  display: grid;
  grid-template-columns: auto auto;
  gap: 0.25rem 1rem;
}
</style>
