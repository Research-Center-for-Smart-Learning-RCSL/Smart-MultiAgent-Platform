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

    <SAlert
      v-if="query.isError.value"
      variant="danger"
      class="mt-2"
      role="alert"
    >
      {{ $t('admin.audit.loadError') }}
    </SAlert>

    <STable
      class="mt-4"
      :columns="columns"
      :data="allItems"
      :loading="query.isPending.value && allItems.length === 0"
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
        {{ new Date(row.created_at).toLocaleString() }}
      </template>

      <template #empty>
        <SEmptyState
          v-if="!query.isError.value"
          :icon="ClipboardDocumentListIcon"
          :text="$t('admin.audit.empty')"
        />
      </template>
    </STable>

    <div
      v-if="nextCursor"
      class="admin-audit__pagination"
    >
      <SButton
        variant="secondary"
        @click="loadMore"
      >
        {{ $t('admin.audit.loadMore') }}
      </SButton>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { ClipboardDocumentListIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, STable, SButton, SInput, SAlert, SEmptyState } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { useToast } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AuditFilter, AuditEntry } from '../types'

const { t } = useI18n()
const toast = useToast()

const filters = reactive<AuditFilter>({})
const appliedFilters = ref<AuditFilter>({})
const allItems = ref<AuditEntry[]>([])
const nextCursor = ref<number | null>(null)

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
  allItems.value = []
  nextCursor.value = null
  appliedFilters.value = clean
}

const query = useQuery({
  queryKey: computed(() => adminKeys.audit(appliedFilters.value)),
  queryFn: () => adminApi.queryAudit(appliedFilters.value),
  refetchOnWindowFocus: false,
})

watch(() => query.data.value, (data) => {
  if (!data) return
  if (appliedFilters.value.cursor) {
    // Load-more: accumulate new items
    allItems.value = [...allItems.value, ...data.items]
  } else {
    // Fresh query: replace items
    allItems.value = [...data.items]
  }
  nextCursor.value = data.next_cursor
})

function loadMore(): void {
  if (nextCursor.value) {
    appliedFilters.value = { ...appliedFilters.value, cursor: nextCursor.value }
  }
}

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
