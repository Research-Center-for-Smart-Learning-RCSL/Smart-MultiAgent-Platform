<template>
  <section class="admin-ip-bans">
    <h1>{{ $t('admin.ipBans.title') }}</h1>

    <form
      class="admin-ip-bans__create"
      @submit.prevent="onCreate"
    >
      <input
        v-model="cidr"
        :placeholder="$t('admin.ipBans.cidrPlaceholder')"
        required
      >
      <input
        v-model="reason"
        :placeholder="$t('admin.ipBans.reason')"
        required
      >
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="actions.createIpBan.isPending.value"
      >
        {{ $t('admin.ipBans.add') }}
      </button>
    </form>

    <table
      v-if="query.data.value"
      class="table"
    >
      <thead>
        <tr>
          <th>{{ $t('admin.ipBans.cidr') }}</th>
          <th>{{ $t('admin.ipBans.reason') }}</th>
          <th>{{ $t('admin.users.created') }}</th>
          <th>{{ $t('admin.users.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="ban in query.data.value"
          :key="ban.id"
        >
          <td><code>{{ ban.cidr }}</code></td>
          <td>{{ ban.reason }}</td>
          <td>{{ new Date(ban.created_at).toLocaleDateString() }}</td>
          <td>
            <button
              class="btn btn-danger btn-sm"
              @click="actions.deleteIpBan.mutate(ban.id)"
            >
              {{ $t('admin.ipBans.remove') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const cidr = ref('')
const reason = ref('')

const query = useQuery({
  queryKey: adminKeys.ipBans(),
  queryFn: () => adminApi.listIpBans().then(r => r.data),
})

const actions = useAdminActions()

async function onCreate(): Promise<void> {
  try {
    await actions.createIpBan.mutateAsync({ cidr: cidr.value, reason: reason.value })
    cidr.value = ''
    reason.value = ''
  } catch {
    // error toast handled by useAdminActions onError
  }
}
</script>

<style scoped>
.admin-ip-bans__create { display: flex; gap: 0.5rem; margin: 1rem 0; }
</style>
