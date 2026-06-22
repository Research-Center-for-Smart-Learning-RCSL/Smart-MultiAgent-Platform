<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listDlq } from '../api'
import type { DlqEntry } from '../types'

const { t } = useI18n()

const props = defineProps<{
  agentId: string
}>()

const entries = ref<DlqEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const expanded = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    entries.value = await listDlq(props.agentId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(() => props.agentId, () => {
  entries.value = []
  if (expanded.value) void refresh()
}, { immediate: false })

function toggle(): void {
  expanded.value = !expanded.value
  if (expanded.value && entries.value.length === 0) void refresh()
}
</script>

<template>
  <div class="dlq-viewer my-2">
    <button
      class="text-sm font-medium text-muted hover:text-fg flex items-center gap-1"
      @click="toggle"
    >
      <span
        :class="expanded ? 'rotate-90' : ''"
        class="transition-transform"
      >
        ▶
      </span>
      {{ t('workflow.dlq.title') }}
      <span
        v-if="entries.length"
        class="text-xs bg-danger-tint text-danger-on px-1 rounded ml-1"
      >
        {{ entries.length }}
      </span>
    </button>

    <div
      v-if="expanded"
      class="mt-2"
    >
      <div
        v-if="loading"
        class="text-xs text-muted"
      >
        {{ t('workflow.dlq.loading') }}
      </div>
      <div
        v-else-if="error"
        class="text-xs text-danger"
      >
        {{ error }}
      </div>
      <div
        v-else-if="entries.length === 0"
        class="text-xs text-muted"
      >
        {{ t('workflow.dlq.empty') }}
      </div>
      <div
        v-else
        class="overflow-x-auto"
      >
        <table class="table text-xs">
          <thead>
            <tr class="text-muted">
              <th>
                {{ t('workflow.dlq.streamId') }}
              </th>
              <th>
                {{ t('workflow.dlq.attempts') }}
              </th>
              <th>
                {{ t('workflow.dlq.error') }}
              </th>
              <th>
                {{ t('workflow.dlq.movedAt') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in entries"
              :key="entry.stream_entry_id"
            >
              <td class="font-mono">
                {{ entry.stream_id }}
              </td>
              <td>
                {{ entry.attempt_count }}
              </td>
              <td class="text-danger truncate max-w-xs sm:max-w-sm">
                {{ entry.last_error }}
              </td>
              <td class="text-muted">
                {{ entry.moved_at }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <button
        class="mt-1 text-xs text-accent hover:underline"
        @click="refresh"
      >
        {{ t('workflow.dlq.refresh') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.table th,
.table td {
  padding: 0.25rem 0.5rem;
}
</style>
