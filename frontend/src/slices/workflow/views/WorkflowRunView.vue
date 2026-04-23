<template>
  <section class="workflow-run p-4">
    <header class="flex items-center gap-3 mb-4">
      <router-link
        v-if="run"
        :to="{ name: 'workflow.runs', params: { workflowId: run.workflow_id } }"
        class="text-sm text-gray-500 hover:underline"
      >
        &larr; {{ $t('workflow.run.backToRuns') }}
      </router-link>
      <h1 class="text-xl font-semibold">
        {{ $t('workflow.run.title') }}
      </h1>
      <span
        v-if="run"
        class="px-2 py-0.5 text-xs rounded-full"
        :class="stateClass(run.state)"
      >
        {{ run.state }}
      </span>
      <span
        v-if="connected"
        class="ml-2 w-2 h-2 rounded-full bg-green-500 inline-block"
        :title="$t('workflow.run.liveConnected')"
      />
    </header>

    <!-- Cancel -->
    <div v-if="run && (run.state === 'running' || run.state === 'waiting')" class="mb-4">
      <button class="btn btn-sm text-red-600" @click="onCancel">
        {{ $t('workflow.run.cancel') }}
      </button>
    </div>

    <!-- Run details -->
    <div v-if="run" class="mb-6 text-sm space-y-1">
      <p><strong>{{ $t('workflow.run.triggerType') }}:</strong> {{ run.trigger_type }}</p>
      <p><strong>{{ $t('workflow.run.started') }}:</strong> {{ new Date(run.started_at).toLocaleString() }}</p>
      <p v-if="run.ended_at">
        <strong>{{ $t('workflow.run.ended') }}:</strong> {{ new Date(run.ended_at).toLocaleString() }}
      </p>
    </div>

    <!-- Step timeline -->
    <h2 class="font-semibold mb-2">{{ $t('workflow.run.steps') }}</h2>
    <p v-if="stepsQuery.isLoading.value" class="text-gray-500">…</p>
    <table v-else-if="steps.length" class="w-full text-sm">
      <thead>
        <tr class="border-b text-left">
          <th class="py-1">{{ $t('workflow.run.nodeId') }}</th>
          <th class="py-1">{{ $t('workflow.run.state') }}</th>
          <th class="py-1">{{ $t('workflow.run.started') }}</th>
          <th class="py-1">{{ $t('workflow.run.ended') }}</th>
          <th class="py-1">{{ $t('workflow.run.error') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="step in steps" :key="step.id" class="border-b">
          <td class="py-1 font-mono text-xs">{{ step.node_id }}</td>
          <td class="py-1">
            <span class="px-1.5 py-0.5 text-xs rounded" :class="stateClass(step.state)">
              {{ step.state }}
            </span>
          </td>
          <td class="py-1 text-gray-500">{{ new Date(step.started_at).toLocaleTimeString() }}</td>
          <td class="py-1 text-gray-500">{{ step.ended_at ? new Date(step.ended_at).toLocaleTimeString() : '—' }}</td>
          <td class="py-1 text-red-600 text-xs truncate max-w-[200px]">{{ step.error ?? '' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="text-gray-400">{{ $t('workflow.run.noSteps') }}</p>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage } from 'element-plus'
import { cancelRun, getRun, listSteps } from '../api'
import { useWorkflowRunSocket } from '../composables/useWorkflowRunSocket'
import { wfKeys } from '../queries'
import type { RunState, StepState } from '../types'

const route = useRoute()
const qc = useQueryClient()
const runId = route.params.runId as string

const { connected } = useWorkflowRunSocket(runId)

const runQuery = useQuery({
  queryKey: wfKeys.run(runId),
  queryFn: () => getRun(runId),
  refetchInterval: 10_000,
})

const stepsQuery = useQuery({
  queryKey: wfKeys.steps(runId),
  queryFn: () => listSteps(runId),
  refetchInterval: 5_000,
})

const run = computed(() => runQuery.data.value ?? null)
const steps = computed(() => stepsQuery.data.value ?? [])

function stateClass(state: RunState | StepState | string): string {
  switch (state) {
    case 'running':
      return 'bg-blue-100 text-blue-700'
    case 'waiting':
      return 'bg-yellow-100 text-yellow-700'
    case 'succeeded':
      return 'bg-green-100 text-green-700'
    case 'failed':
      return 'bg-red-100 text-red-700'
    case 'cancelled':
    case 'skipped':
      return 'bg-gray-100 text-gray-600'
    case 'pending':
      return 'bg-gray-50 text-gray-400'
    default:
      return ''
  }
}

async function onCancel(): Promise<void> {
  try {
    await cancelRun(runId)
    qc.invalidateQueries({ queryKey: wfKeys.run(runId) })
    qc.invalidateQueries({ queryKey: wfKeys.steps(runId) })
  } catch {
    ElMessage.error('Failed to cancel run.')
  }
}
</script>
