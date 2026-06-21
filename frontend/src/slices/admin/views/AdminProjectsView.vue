<template>
  <section class="admin-projects">
    <h1>{{ $t('admin.projects.title') }}</h1>
    <table
      v-if="query.data.value"
      class="table"
    >
      <thead>
        <tr>
          <th>{{ $t('admin.projects.name') }}</th>
          <th>{{ $t('admin.projects.ownerUser') }}</th>
          <th>{{ $t('admin.projects.ownerOrg') }}</th>
          <th>{{ $t('admin.users.created') }}</th>
          <th>{{ $t('admin.orgs.deleted') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="proj in query.data.value"
          :key="proj.id"
        >
          <td>{{ proj.name }}</td>
          <td>{{ proj.owner_user_id ?? '-' }}</td>
          <td>{{ proj.owner_org_id ?? '-' }}</td>
          <td>{{ new Date(proj.created_at).toLocaleDateString() }}</td>
          <td>{{ proj.deleted_at ? new Date(proj.deleted_at).toLocaleDateString() : '-' }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const query = useQuery({
  queryKey: adminKeys.projects(),
  queryFn: () => adminApi.listProjects().then(r => r.data),
})
</script>

