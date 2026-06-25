<template>
  <section class="admin-rate-limits">
    <SPageHeader :title="$t('admin.rateLimits.title')" />

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
      row-key="key"
    >
      <template #cell-key="{ row }">
        <code class="font-mono text-[0.8125rem]">{{ row.key }}</code>
      </template>

      <template #cell-window_sec="{ row }">
        <SInput
          v-if="edits[row.key]"
          v-model="edits[row.key].window_sec"
          type="number"
          size="sm"
          class="admin-rate-limits__input"
          :aria-label="$t('admin.rateLimits.window')"
        />
      </template>

      <template #cell-max_count="{ row }">
        <SInput
          v-if="edits[row.key]"
          v-model="edits[row.key].max_count"
          type="number"
          size="sm"
          class="admin-rate-limits__input"
          :aria-label="$t('admin.rateLimits.maxCount')"
        />
      </template>

      <template #cell-updated_at="{ row }">
        {{ new Date(row.updated_at).toLocaleString() }}
      </template>

      <template #actions="{ row }">
        <SButton
          variant="primary"
          size="sm"
          :loading="actions.patchRateLimit.isPending.value"
          @click="onPatch(row.key)"
        >
          {{ $t('admin.rateLimits.save') }}
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="AdjustmentsHorizontalIcon"
          :text="$t('admin.rateLimits.empty')"
        />
      </template>
    </STable>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { AdjustmentsHorizontalIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, STable, SButton, SInput, SAlert, SEmptyState } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()

interface EditRow {
  window_sec: number
  max_count: number
}

const edits = reactive<Record<string, EditRow>>({})

const columns = computed<Column[]>(() => [
  { key: 'key', label: t('admin.rateLimits.key') },
  { key: 'window_sec', label: t('admin.rateLimits.window'), width: '120px' },
  { key: 'max_count', label: t('admin.rateLimits.maxCount'), width: '120px' },
  { key: 'scope', label: t('admin.rateLimits.scope'), width: '120px' },
  { key: 'updated_at', label: t('admin.rateLimits.updated'), width: '180px' },
  { key: 'actions', label: t('admin.users.actions'), width: '100px', align: 'right' },
])

const query = useQuery({
  queryKey: adminKeys.rateLimits(),
  queryFn: () => adminApi.listRateLimits(),
})

watch(
  () => query.data.value,
  (policies) => {
    if (!policies) return
    for (const p of policies) {
      if (!edits[p.key]) {
        edits[p.key] = { window_sec: p.window_sec, max_count: p.max_count }
      }
    }
  },
  { immediate: true },
)

const actions = useAdminActions()

function onPatch(key: string): void {
  const edit = edits[key]
  if (!edit) return
  actions.patchRateLimit.mutate({
    key,
    patch: { window_sec: edit.window_sec, max_count: edit.max_count },
  })
}
</script>

<style scoped>
.admin-rate-limits__input {
  width: 6rem;
}
</style>
