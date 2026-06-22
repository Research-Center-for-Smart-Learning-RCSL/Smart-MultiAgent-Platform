<template>
  <section class="admin-admins">
    <SPageHeader :title="$t('admin.admins.title')" />

    <form
      class="admin-admins__promote"
      @submit.prevent="onPromote"
    >
      <input
        v-model="promoteUserId"
        :placeholder="$t('admin.admins.userIdPlaceholder')"
        required
      >
      <button
        type="submit"
        class="btn btn-primary"
      >
        {{ $t('admin.admins.promote') }}
      </button>
    </form>

    <p
      v-if="promoteError"
      class="admin-admins__error"
    >
      {{ promoteError }}
    </p>

    <div
      v-if="query.data.value"
      class="overflow-x-auto"
    >
    <table class="table">
      <thead>
        <tr>
          <th scope="col">{{ $t('admin.admins.userId') }}</th>
          <th scope="col">{{ $t('admin.admins.promotedBy') }}</th>
          <th scope="col">{{ $t('admin.admins.promotedAt') }}</th>
          <th scope="col">{{ $t('admin.users.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="admin in query.data.value"
          :key="admin.user_id"
        >
          <td>{{ admin.user_id }}</td>
          <td>{{ admin.promoted_by_user_id ?? '-' }}</td>
          <td>{{ new Date(admin.promoted_at).toLocaleDateString() }}</td>
          <td>
            <button
              class="btn btn-danger btn-sm"
              @click="onDemote(admin.user_id)"
            >
              {{ $t('admin.admins.demote') }}
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
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import { isProblemWithType } from '@shared/transport'

const { t } = useI18n()
const promoteUserId = ref('')
const promoteError = ref<string | null>(null)

const query = useQuery({
  queryKey: adminKeys.admins(),
  queryFn: () => adminApi.listAdmins().then(r => r.data),
})

const actions = useAdminActions()

async function onPromote(): Promise<void> {
  promoteError.value = null
  try {
    await actions.promoteAdmin.mutateAsync(promoteUserId.value.trim())
    promoteUserId.value = ''
  } catch {
    promoteError.value = t('admin.users.promotionFailed')
  }
}

async function onDemote(userId: string): Promise<void> {
  promoteError.value = null
  try {
    await actions.demoteAdmin.mutateAsync(userId)
  } catch (e) {
    if (isProblemWithType(e, 'admin/last-admin')) {
      promoteError.value = t('admin.users.lastAdminDemote')
    } else {
      promoteError.value = t('admin.users.demotionFailed')
    }
  }
}
</script>

<style scoped>
.admin-admins__promote { display: flex; gap: 0.5rem; margin: 1rem 0; }
.admin-admins__error { color: var(--color-danger); }
</style>
