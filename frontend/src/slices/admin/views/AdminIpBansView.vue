<template>
  <section class="admin-ip-bans">
    <SPageHeader :title="$t('admin.ipBans.title')" />

    <form
      class="admin-ip-bans__create"
      @submit.prevent="onCreate"
    >
      <SInput
        v-model="cidr"
        class="admin-ip-bans__cidr"
        :placeholder="$t('admin.ipBans.cidrPlaceholder')"
        :aria-label="$t('admin.ipBans.cidr')"
      />
      <SInput
        v-model="reason"
        class="admin-ip-bans__reason"
        :placeholder="$t('admin.ipBans.reason')"
        :aria-label="$t('admin.ipBans.reason')"
      />
      <SButton
        type="submit"
        variant="primary"
        :loading="actions.createIpBan.isPending.value"
      >
        {{ $t('admin.ipBans.add') }}
      </SButton>
    </form>

    <SAlert
      v-if="query.isError.value"
      variant="danger"
      class="mt-4"
      role="alert"
    >
      {{ $t('admin.common.loadError') }}
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

    <STable
      v-else
      class="mt-4"
      :columns="columns"
      :data="query.data.value ?? []"
      :loading="query.isPending.value"
      row-key="id"
    >
      <template #cell-cidr="{ row }">
        <code class="font-mono text-[0.8125rem]">{{ row.cidr }}</code>
      </template>

      <template #cell-banned_at="{ row }">
        {{ new Date(row.banned_at).toLocaleDateString() }}
      </template>

      <template #actions="{ row }">
        <SButton
          variant="danger"
          size="sm"
          :loading="actions.deleteIpBan.isPending.value"
          @click="onDeleteBan(row.id)"
        >
          {{ $t('admin.ipBans.remove') }}
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="NoSymbolIcon"
          :text="$t('admin.ipBans.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NoSymbolIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SInput,
  SAlert,
  SEmptyState,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { useQuery } from '@tanstack/vue-query'
import { useConfirmDialog } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const cidr = ref('')
const reason = ref('')

const columns = computed<Column[]>(() => [
  { key: 'cidr', label: t('admin.ipBans.cidr'), width: '200px' },
  { key: 'reason', label: t('admin.ipBans.reason') },
  { key: 'banned_at', label: t('admin.users.created'), width: '140px' },
  { key: 'actions', label: t('admin.users.actions'), width: '120px', align: 'right' },
])

const query = useQuery({
  queryKey: adminKeys.ipBans(),
  queryFn: () => adminApi.listIpBans(),
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
.admin-ip-bans__create {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: center;
}
.admin-ip-bans__cidr {
  flex: 0 1 16rem;
}
.admin-ip-bans__reason {
  flex: 1 1 20rem;
}
</style>
