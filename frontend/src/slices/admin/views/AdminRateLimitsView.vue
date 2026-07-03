<template>
  <section class="admin-rate-limits">
    <SPageHeader :title="$t('admin.rateLimits.title')" />

    <SQueryError
      v-if="query.isError.value"
      class="mt-4"
      :message="$t('admin.common.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="query.refetch()"
    />

    <STable
      v-else
      class="mt-4"
      :columns="columns"
      :data="rows"
      :loading="query.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="key"
    >
      <template #cell-key="{ row }">
        <code class="font-mono text-[0.8125rem]">{{ row.key }}</code>
      </template>

      <template #cell-window_sec="{ row }">
        <!-- The `v-if` on this element guarantees `edits[row.key]` is defined
             whenever the v-model binding below runs (TS can't narrow a
             dynamic index access, so the `!` documents that invariant). -->
        <SInput
          v-if="edits[row.key]"
          v-model="edits[row.key]!.window_sec"
          type="number"
          size="sm"
          class="admin-rate-limits__input"
          :aria-label="$t('admin.rateLimits.window')"
        />
      </template>

      <template #cell-max_count="{ row }">
        <!-- Same invariant as the window_sec cell above: `v-if` guarantees
             `edits[row.key]` is defined for the v-model binding. -->
        <SInput
          v-if="edits[row.key]"
          v-model="edits[row.key]!.max_count"
          type="number"
          size="sm"
          class="admin-rate-limits__input"
          :aria-label="$t('admin.rateLimits.maxCount')"
        />
      </template>

      <template #cell-updated_at="{ row }">
        {{ formatDateTime(row.updated_at) }}
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
import { SPageHeader, STable, SButton, SInput, SQueryError, SEmptyState } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDateTime } from '@shared/utils/datetime'
import { useToast, useConfirmDialog } from '@shared/composables'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'
import type { RateLimitPolicy } from '../types'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()

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

// STable's generic constrains T to Record<string, unknown>; RateLimitPolicy has
// no index signature by design, so intersect it in only for this cast (the
// runtime shape is unchanged — plain rate-limit-policy objects from the API).
type RateLimitRow = RateLimitPolicy & Record<string, unknown>

const rows = computed<RateLimitRow[]>(
  () => (query.data.value ?? []) as unknown as RateLimitRow[],
)

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

async function onPatch(key: string): Promise<void> {
  const edit = edits[key]
  if (!edit) return
  if (
    !Number.isFinite(edit.window_sec) || edit.window_sec < 1
    || !Number.isFinite(edit.max_count) || edit.max_count < 1
  ) {
    toast.warning(t('admin.rateLimits.invalid'))
    return
  }
  const ok = await confirm({
    title: t('admin.rateLimits.confirmTitle'),
    message: t('admin.rateLimits.confirmMessage', { key }),
    confirmLabel: t('admin.rateLimits.save'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
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
