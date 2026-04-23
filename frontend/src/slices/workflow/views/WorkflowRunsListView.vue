<template>
  <section class="workflow-runs p-4">
    <header class="flex items-center gap-3 mb-4">
      <router-link
        :to="{ name: 'workflow.list', params: { workspaceId: route.params.workspaceId } }"
        class="text-sm text-gray-500 hover:underline"
      >
        &larr; {{ $t('workflow.runs.backToList') }}
      </router-link>
      <h1 class="text-xl font-semibold">{{ $t('workflow.runs.title') }}</h1>
      <button class="btn btn-primary btn-sm ml-auto" @click="onTrigger">
        {{ $t('workflow.runs.triggerManual') }}
      </button>
    </header>

    <div class="mb-3">
      <label class="text-xs text-gray-500 flex items-center gap-1">
        <input v-model="showArchive" type="checkbox" />
        {{ $t('workflow.runs.includeArchive') }}
      </label>
    </div>

    <p v-if="query.isLoading.value" class="text-gray-500">…</p>
    <table v-else-if="runsList.length" class="w-full text-sm">
      <thead>
        <tr class="border-b text-left">
          <th class="py-1">{{ $t('workflow.runs.state') }}</th>
          <th class="py-1">{{ $t('workflow.runs.trigger') }}</th>
          <th class="py-1">{{ $t('workflow.runs.started') }}</th>
          <th class="py-1">{{ $t('workflow.runs.ended') }}</th>
          <th class="py-1" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in runsList" :key="r.id" class="border-b">
          <td class="py-1">
            <span
              class="px-1.5 py-0.5 text-xs rounded"
              :class="stateClass(r.state)"
            >
              {{ r.state }}
            </span>
            <span v-if="r.archived" class="ml-1 text-[10px] text-gray-400">
              ({{ $t('workflow.runs.archived') }})
            </span>
          </td>
          <td class="py-1">{{ r.trigger_type }}</td>
          <td class="py-1 text-gray-500">{{ new Date(r.started_at).toLocaleString() }}</td>
          <td class="py-1 text-gray-500">{{ r.ended_at ? new Date(r.ended_at).toLocaleString() : '—' }}</td>
          <td class="py-1">
            <router-link
              :to="{ name: 'workflow.run', params: { runId: r.id } }"
              class="text-blue-600 hover:underline text-xs"
            >
              {{ $t('workflow.runs.inspect') }}
            </router-link>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="text-gray-400">{{ $t('workflow.runs.empty') }}</p>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { listRuns, triggerRun } from '../api'
import { wfKeys } from '../queries'

const route = useRoute()
const qc = useQueryClient()
const workflowId = route.params.workflowId as string
const showArchive = ref(false)

const query = useQuery({
  queryKey: computed(() => [...wfKeys.runs(workflowId), showArchive.value] as const),
  queryFn: () => listRuns(workflowId, { includeArchive: showArchive.value }),
})

const runsList = computed(() => query.data.value ?? [])

function stateClass(state: string): string {
  switch (state) {
    case 'running': return 'bg-blue-100 text-blue-700'
    case 'waiting': return 'bg-yellow-100 text-yellow-700'
    case 'succeeded': return 'bg-green-100 text-green-700'
    case 'failed': return 'bg-red-100 text-red-700'
    case 'cancelled': return 'bg-gray-100 text-gray-600'
    default: return ''
  }
}

async function onTrigger(): Promise<void> {
  await triggerRun(workflowId)
  qc.invalidateQueries({ queryKey: wfKeys.runs(workflowId) })
}
</script>
