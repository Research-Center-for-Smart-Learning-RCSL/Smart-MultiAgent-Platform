<template>
  <section class="admin-projects">
    <SPageHeader :title="$t('admin.projects.title')" />
    <div
      v-if="query.data.value"
      class="overflow-x-auto"
    >
    <table class="table">
      <thead>
        <tr>
          <th scope="col">{{ $t('admin.projects.name') }}</th>
          <th scope="col">{{ $t('admin.projects.ownerUser') }}</th>
          <th scope="col">{{ $t('admin.projects.ownerOrg') }}</th>
          <th scope="col">{{ $t('admin.users.created') }}</th>
          <th scope="col">{{ $t('admin.orgs.deleted') }}</th>
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
    </div>
  </section>
</template>

<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

const query = useQuery({
  queryKey: adminKeys.projects(),
  queryFn: () => adminApi.listProjects().then(r => r.data),
})
</script>

