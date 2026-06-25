<template>
  <section class="admin-audit">
    <SPageHeader :title="$t('admin.audit.title')" />

    <form
      class="admin-audit__filters"
      @submit.prevent="applyFilters"
    >
      <SInput
        v-model="filters.action"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.action')"
        :aria-label="$t('admin.audit.action')"
      />
      <SInput
        v-model="filters.actor_user_id"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.actorUserId')"
        :aria-label="$t('admin.audit.actorUserId')"
      />
      <SInput
        v-model="filters.resource_type"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.resourceType')"
        :aria-label="$t('admin.audit.resourceType')"
      />
      <SInput
        v-model="filters.resource_id"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.resourceId')"
        :aria-label="$t('admin.audit.resourceId')"
      />
      <SInput
        v-model="filters.ip_prefix"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.ipPrefix')"
        :aria-label="$t('admin.audit.ipPrefix')"
      />
      <SInput
        v-model="filters.session_id"
        class="admin-audit__field"
        :placeholder="$t('admin.audit.sessionId')"
        :aria-label="$t('admin.audit.sessionId')"
      />
      <SInput
        v-model="filters.from"
        type="datetime-local"
        class="admin-audit__field"
        :aria-label="$t('admin.audit.from')"
      />
      <SInput
        v-model="filters.to"
        type="datetime-local"
        class="admin-audit__field"
        :aria-label="$t('admin.audit.to')"
      />
      <SButton
        type="submit"
        variant="primary"
      >
        {{ $t('admin.users.search') }}
      </SButton>
      <SButton
        type="button"
        variant="secondary"
        @click="onExport"
      >
        {{ $t('admin.audit.export') }}
      </SButton>
    </form>

    <SQueryError
      v-if="query.isError.value"
      class="mt-2"
      :message="$t('admin.audit.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="query.refetch()"
    />

    <STable
      class="mt-4"
      :columns="columns"
      :data="allItems"
      :loading="query.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="id"
    >
      <template #cell-actor_user_id="{ row }">
        {{ row.actor_user_id ?? '-' }}
      </template>
      <template #cell-resource_type="{ row }">
        {{ row.resource_type ?? '-' }}
      </template>
      <template #cell-resource_id="{ row }">
        {{ row.resource_id ?? '-' }}
      </template>
      <template #cell-actor_ip="{ row }">
        {{ row.actor_ip ?? '-' }}
      </template>
      <template #cell-created_at="{ row }">
        {{ formatDateTime(row.created_at) }}
      </template>

      <template #empty>
        <!-- Table stays mounted during load-more errors to keep loaded rows;
             suppress the empty state when the error alert above is showing. -->
        <SEmptyState
          v-if="!query.isError.value"
          :icon="ClipboardDocumentListIcon"
          :text="$t('admin.audit.empty')"
        />
      </template>
    </STable>

    <div
      v-if="query.hasNextPage.value"
      class="admin-audit__pagination"
    >
      <SButton
        variant="secondary"
        :loading="query.isFetchingNextPage.value"
        @click="query.fetchNextPage()"
      >
        {{ $t('admin.audit.loadMore') }}
      </SButton>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, computed } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { ClipboardDocumentListIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, STable, SButton, SInput, SQueryError, SEmptyState } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDateTime } from '@shared/utils/datetime'
import { useToast } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AuditFilter } from '../types'

const { t } = useI18n()
const toast = useToast()

const filters = reactive<AuditFilter>({})
const appliedFilters = ref<AuditFilter>({})

const columns = computed<Column[]>(() => [
  { key: 'id', label: t('admin.audit.id'), width: '80px' },
  { key: 'action', label: t('admin.audit.action') },
  { key: 'actor_user_id', label: t('admin.audit.actorUserId') },
  { key: 'resource_type', label: t('admin.audit.resourceType'), width: '120px' },
  { key: 'resource_id', label: t('admin.audit.resourceId') },
  { key: 'actor_ip', label: t('admin.audit.ipPrefix'), width: '120px' },
  { key: 'created_at', label: t('admin.users.created'), width: '180px' },
])

function applyFilters(): void {
  const clean: AuditFilter = {}
  if (filters.action) clean.action = filters.action
  if (filters.actor_user_id) clean.actor_user_id = filters.actor_user_id
  if (filters.resource_type) clean.resource_type = filters.resource_type
  if (filters.resource_id) clean.resource_id = filters.resource_id
  if (filters.ip_prefix) clean.ip_prefix = filters.ip_prefix
  if (filters.session_id) clean.session_id = filters.session_id
  if (filters.from) clean.from = filters.from
  if (filters.to) clean.to = filters.to
  // New query key resets the infinite query's pages automatically.
  appliedFilters.value = clean
}

const query = useInfiniteQuery({
  queryKey: computed(() => adminKeys.audit(appliedFilters.value)),
  queryFn: ({ pageParam }) =>
    adminApi.queryAudit({ ...appliedFilters.value, cursor: pageParam }),
  initialPageParam: undefined as number | undefined,
  getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  refetchOnWindowFocus: false,
})

const allItems = computed(() => (query.data.value?.pages ?? []).flatMap((p) => p.items))

async function onExport(): Promise<void> {
  if (!appliedFilters.value.from || !appliedFilters.value.to) {
    toast.warning(t('admin.audit.exportDateRequired'))
    return
  }
  try {
    const result = await adminApi.exportAudit(appliedFilters.value)
    window.open(result.url, '_blank')
  } catch {
    toast.error(t('admin.audit.exportFailed'))
  }
}
</script>

<style scoped>
.admin-audit__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: center;
}
.admin-audit__field {
  max-width: 14rem;
}
.admin-audit__pagination {
  margin: 1rem 0;
  display: flex;
  justify-content: center;
}
</style>
