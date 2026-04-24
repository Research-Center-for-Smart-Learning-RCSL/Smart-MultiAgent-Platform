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
      class="text-sm font-medium text-gray-600 hover:text-gray-900 flex items-center gap-1"
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
        class="text-xs bg-red-100 text-red-700 px-1 rounded ml-1"
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
        class="text-xs text-gray-400"
      >
        {{ t('workflow.dlq.loading') }}
      </div>
      <div
        v-else-if="error"
        class="text-xs text-red-500"
      >
        {{ error }}
      </div>
      <div
        v-else-if="entries.length === 0"
        class="text-xs text-gray-400"
      >
        {{ t('workflow.dlq.empty') }}
      </div>
      <table
        v-else
        class="w-full text-xs border-collapse"
      >
        <thead>
          <tr class="border-b text-left text-gray-500">
            <th class="py-1 pr-2">
              {{ t('workflow.dlq.streamId') }}
            </th>
            <th class="py-1 pr-2">
              {{ t('workflow.dlq.attempts') }}
            </th>
            <th class="py-1 pr-2">
              {{ t('workflow.dlq.error') }}
            </th>
            <th class="py-1 pr-2">
              {{ t('workflow.dlq.movedAt') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="entry in entries"
            :key="entry.stream_entry_id"
            class="border-b"
          >
            <td class="py-1 pr-2 font-mono">
              {{ entry.stream_id }}
            </td>
            <td class="py-1 pr-2">
              {{ entry.attempt_count }}
            </td>
            <td class="py-1 pr-2 text-red-600 truncate max-w-64">
              {{ entry.last_error }}
            </td>
            <td class="py-1 pr-2 text-gray-500">
              {{ entry.moved_at }}
            </td>
          </tr>
        </tbody>
      </table>
      <button
        class="mt-1 text-xs text-blue-600 hover:underline"
        @click="refresh"
      >
        {{ t('workflow.dlq.refresh') }}
      </button>
    </div>
  </div>
</template>
