<template>
  <section class="admin-ip-bans">
    <SPageHeader :title="$t('admin.ipBans.title')" />

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

    <div
      v-if="query.data.value"
      class="overflow-x-auto"
    >
      <table class="table">
        <thead>
          <tr>
            <th scope="col">
              {{ $t('admin.ipBans.cidr') }}
            </th>
            <th scope="col">
              {{ $t('admin.ipBans.reason') }}
            </th>
            <th scope="col">
              {{ $t('admin.users.created') }}
            </th>
            <th scope="col">
              {{ $t('admin.users.actions') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="ban in query.data.value"
            :key="ban.id"
          >
            <td><code>{{ ban.cidr }}</code></td>
            <td>{{ ban.reason }}</td>
            <td>{{ new Date(ban.banned_at).toLocaleDateString() }}</td>
            <td>
              <button
                class="btn btn-danger btn-sm"
                :disabled="actions.deleteIpBan.isPending.value"
                @click="onDeleteBan(ban.id)"
              >
                {{ $t('admin.ipBans.remove') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { useConfirmDialog } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const cidr = ref('')
const reason = ref('')

const query = useQuery({
  queryKey: adminKeys.ipBans(),
  queryFn: () => adminApi.listIpBans().then(r => r.data),
})

const actions = useAdminActions()

async function onDeleteBan(id: string): Promise<void> {
  const ok = await confirm({
    title: t('admin.ipBans.removeConfirmTitle'),
    message: t('admin.ipBans.removeConfirm'),
    confirmLabel: t('admin.ipBans.remove'),
    variant: 'error',
  })
  if (!ok) return
  actions.deleteIpBan.mutate(id)
}

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
