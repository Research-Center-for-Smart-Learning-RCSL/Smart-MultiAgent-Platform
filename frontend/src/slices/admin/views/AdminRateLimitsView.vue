<template>
  <section class="admin-rate-limits">
    <h1>{{ $t('admin.rateLimits.title') }}</h1>
    <table
      v-if="query.data.value"
      class="table"
    >
      <thead>
        <tr>
          <th>{{ $t('admin.rateLimits.key') }}</th>
          <th>{{ $t('admin.rateLimits.window') }}</th>
          <th>{{ $t('admin.rateLimits.maxCount') }}</th>
          <th>{{ $t('admin.rateLimits.scope') }}</th>
          <th>{{ $t('admin.rateLimits.updated') }}</th>
          <th>{{ $t('admin.users.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="policy in query.data.value"
          :key="policy.key"
        >
          <td><code>{{ policy.key }}</code></td>
          <td>
            <input
              v-model.number="edits[policy.key].window_sec"
              type="number"
              min="1"
              class="admin-rate-limits__input"
              :aria-label="$t('admin.rateLimits.window')"
            >
          </td>
          <td>
            <input
              v-model.number="edits[policy.key].max_count"
              type="number"
              min="1"
              class="admin-rate-limits__input"
              :aria-label="$t('admin.rateLimits.maxCount')"
            >
          </td>
          <td>{{ policy.scope }}</td>
          <td>{{ new Date(policy.updated_at).toLocaleString() }}</td>
          <td>
            <button
              class="btn btn-primary btn-sm"
              @click="onPatch(policy.key)"
            >
              {{ $t('admin.rateLimits.save') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import { useAdminActions } from '../composables/useAdminActions'

interface EditRow {
  window_sec: number
  max_count: number
}

const edits = reactive<Record<string, EditRow>>({})

const query = useQuery({
  queryKey: adminKeys.rateLimits(),
  queryFn: () => adminApi.listRateLimits().then(r => r.data),
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
.admin-rate-limits__input { width: 5rem; }
</style>
