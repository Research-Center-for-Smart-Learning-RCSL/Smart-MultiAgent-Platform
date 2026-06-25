<template>
  <section class="workflow-run p-4">
    <header class="flex items-center gap-3 mb-4">
      <button
        v-if="run"
        type="button"
        class="text-sm text-muted hover:underline"
        @click="onBackToRuns"
      >
        &larr; {{ $t('workflow.run.backToRuns') }}
      </button>
      <h1 class="text-xl font-semibold font-mono">
        {{ $t('workflow.run.idLabel', { id: shortRunId }) }}
      </h1>
      <SStatusBadge
        v-if="run"
        :status="run.state"
      />
      <span
        v-if="connected"
        class="ml-2 inline-flex items-center gap-1 text-xs text-success"
      >
        <span
          class="w-2 h-2 rounded-full bg-success inline-block"
          aria-hidden="true"
        />
        {{ $t('workflow.run.connected') }}
      </span>
    </header>

    <!-- Cancel -->
    <div
      v-if="run && (run.state === 'running' || run.state === 'waiting')"
      class="mb-4"
    >
      <button
        class="btn btn-danger btn-sm"
        @click="onCancel"
      >
        {{ $t('workflow.run.cancel') }}
      </button>
    </div>

    <!-- Run details -->
    <div
      v-if="run"
      class="mb-6 text-sm grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1 max-w-2xl"
    >
      <p><strong>{{ $t('workflow.run.triggerType') }}:</strong> {{ run.trigger_type }}</p>
      <p><strong>{{ $t('workflow.run.started') }}:</strong> {{ new Date(run.started_at).toLocaleString() }}</p>
      <p v-if="run.ended_at">
        <strong>{{ $t('workflow.run.ended') }}:</strong> {{ new Date(run.ended_at).toLocaleString() }}
      </p>
    </div>

    <!-- Step timeline -->
    <h2 class="font-semibold mb-2">
      {{ $t('workflow.run.steps') }}
    </h2>
    <p
      v-if="stepsQuery.isLoading.value"
      class="text-muted"
    >
      …
    </p>
    <div
      v-else-if="steps.length"
      class="overflow-x-auto"
    >
      <table class="table">
        <thead>
          <tr>
            <th scope="col">
              {{ $t('workflow.run.nodeId') }}
            </th>
            <th scope="col">
              {{ $t('workflow.run.state') }}
            </th>
            <th scope="col">
              {{ $t('workflow.run.started') }}
            </th>
            <th scope="col">
              {{ $t('workflow.run.ended') }}
            </th>
            <th scope="col">
              {{ $t('workflow.run.error') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="step in steps"
            :key="step.id"
          >
            <td class="font-mono text-xs">
              {{ step.node_id }}
            </td>
            <td>
              <SStatusBadge :status="step.state" />
            </td>
            <td class="text-muted">
              {{ new Date(step.started_at).toLocaleTimeString() }}
            </td>
            <td class="text-muted">
              {{ step.ended_at ? new Date(step.ended_at).toLocaleTimeString() : '—' }}
            </td>
            <td class="text-danger text-xs truncate max-w-[200px]">
              {{ step.error ?? '' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p
      v-else
      class="text-muted"
    >
      {{ $t('workflow.run.noSteps') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useToast } from '@shared/composables'
import { SStatusBadge } from '@shared/ui'
import { useI18n } from 'vue-i18n'
import { cancelRun, getRun, listSteps } from '../api'
import { useWorkflowRunSocket } from '../composables/useWorkflowRunSocket'
import { wfKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const runId = route.params.runId as string
const shortRunId = (runId ?? '').slice(0, 8)
const toast = useToast()

const { connected } = useWorkflowRunSocket(runId)

const TERMINAL_STATES = new Set<string>(['succeeded', 'failed', 'cancelled'])

const runQuery = useQuery({
  queryKey: wfKeys.run(runId),
  queryFn: () => getRun(runId),
  refetchInterval: (q) =>
    TERMINAL_STATES.has((q.state.data as { state?: string } | undefined)?.state ?? '')
      ? false
      : 10_000,
})

const stepsQuery = useQuery({
  queryKey: wfKeys.steps(runId),
  queryFn: () => listSteps(runId),
  refetchInterval: () =>
    TERMINAL_STATES.has(runQuery.data.value?.state ?? '') ? false : 5_000,
})

const run = computed(() => runQuery.data.value ?? null)
const steps = computed(() => stepsQuery.data.value ?? [])

function onBackToRuns(): void {
  // The runs-list route is workspace+workflow scoped, but `WorkflowRun` does
  // not carry workspace_id. Step back through history instead so the user
  // returns to whichever list (runs / backstage / dashboard) brought them in.
  router.back()
}

async function onCancel(): Promise<void> {
  try {
    await cancelRun(runId)
    qc.invalidateQueries({ queryKey: wfKeys.run(runId) })
    qc.invalidateQueries({ queryKey: wfKeys.steps(runId) })
  } catch {
    toast.error(t('workflow.run.cancelFailed'))
  }
}
</script>
