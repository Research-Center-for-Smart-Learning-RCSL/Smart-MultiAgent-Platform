<template>
  <section class="admin-audit">
    <h1>{{ $t('admin.audit.title') }}</h1>

    <form
      class="admin-audit__filters"
      @submit.prevent="applyFilters"
    >
      <input
        v-model="filters.action"
        :placeholder="$t('admin.audit.action')"
      >
      <input
        v-model="filters.actor_user_id"
        :placeholder="$t('admin.audit.actorUserId')"
      >
      <input
        v-model="filters.resource_type"
        :placeholder="$t('admin.audit.resourceType')"
      >
      <input
        v-model="filters.resource_id"
        :placeholder="$t('admin.audit.resourceId')"
      >
      <input
        v-model="filters.ip_prefix"
        :placeholder="$t('admin.audit.ipPrefix')"
      >
      <input
        v-model="filters.session_id"
        :placeholder="$t('admin.audit.sessionId')"
      >
      <input
        v-model="filters.from"
        type="datetime-local"
        :aria-label="$t('admin.audit.from')"
      >
      <input
        v-model="filters.to"
        type="datetime-local"
        :aria-label="$t('admin.audit.to')"
      >
      <button
        type="submit"
        class="btn"
      >
        {{ $t('admin.users.search') }}
      </button>
      <button
        type="button"
        class="btn"
        @click="onExport"
      >
        {{ $t('admin.audit.export') }}
      </button>
    </form>

    <table v-if="allItems.length">
      <thead>
        <tr>
          <th>ID</th>
          <th>{{ $t('admin.audit.action') }}</th>
          <th>{{ $t('admin.audit.actorUserId') }}</th>
          <th>{{ $t('admin.audit.resourceType') }}</th>
          <th>{{ $t('admin.audit.resourceId') }}</th>
          <th>{{ $t('admin.audit.ipPrefix') }}</th>
          <th>{{ $t('admin.users.created') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="entry in allItems"
          :key="entry.id"
        >
          <td>{{ entry.id }}</td>
          <td>{{ entry.action }}</td>
          <td>{{ entry.actor_user_id ?? '-' }}</td>
          <td>{{ entry.resource_type ?? '-' }}</td>
          <td>{{ entry.resource_id ?? '-' }}</td>
          <td>{{ entry.actor_ip ?? '-' }}</td>
          <td>{{ new Date(entry.created_at).toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>

    <div
      v-if="nextCursor"
      class="admin-audit__pagination"
    >
      <button
        class="btn"
        @click="loadMore"
      >
        {{ $t('admin.audit.loadMore') }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AuditFilter, AuditEntry } from '../types'

const filters = reactive<AuditFilter>({})
const appliedFilters = ref<AuditFilter>({})
const allItems = ref<AuditEntry[]>([])
const nextCursor = ref<number | null>(null)

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
  queryFn: () => adminApi.queryAudit(appliedFilters.value).then(r => r.data),
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
  const { data } = await adminApi.exportAudit(appliedFilters.value)
  window.open(data.url, '_blank')
}
</script>

<style scoped>
.admin-audit__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0;
}
.admin-audit__filters input { max-width: 14rem; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--color-border, #eee); text-align: left; font-size: 0.875rem; }
.admin-audit__pagination { margin: 1rem 0; }
</style>
