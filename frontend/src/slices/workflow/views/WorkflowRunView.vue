<template>
  <section class="workflow-run">
    <SEmptyState
      v-if="forbidden"
      :icon="LockClosedIcon"
      :title="$t('workflow.run.forbiddenTitle')"
      :text="$t('workflow.run.forbiddenText')"
    />
    <template v-else>
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
        <SButton
          variant="danger"
          size="sm"
          @click="onCancel"
        >
          {{ $t('workflow.run.cancel') }}
        </SButton>
      </div>

      <!-- Run details -->
      <div
        v-if="run"
        class="mb-6 text-sm grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1 max-w-2xl"
      >
        <p><strong>{{ $t('workflow.run.triggerType') }}:</strong> {{ run.trigger_type }}</p>
        <p><strong>{{ $t('workflow.run.started') }}:</strong> {{ formatDateTime(run.started_at) }}</p>
        <p v-if="run.ended_at">
          <strong>{{ $t('workflow.run.ended') }}:</strong> {{ formatDateTime(run.ended_at) }}
        </p>
      </div>

      <!-- Step timeline -->
      <h2 class="font-semibold mb-2">
        {{ $t('workflow.run.steps') }}
      </h2>
      <StepsTable
        v-if="stepsQuery.isLoading.value || steps.length"
        :columns="columns"
        :data="steps"
        :loading="stepsQuery.isLoading.value"
        :loading-label="$t('workflow.run.steps')"
        row-key="id"
        responsive-mode="hide-columns"
      >
        <template #cell-node_id="{ row }">
          <span class="font-mono text-xs">{{ row.node_id }}</span>
        </template>
        <template #cell-state="{ row }">
          <SStatusBadge :status="row.state" />
        </template>
        <template #cell-started_at="{ row }">
          <span class="text-muted">{{ new Date(row.started_at).toLocaleTimeString() }}</span>
        </template>
        <template #cell-ended_at="{ row }">
          <span class="text-muted">{{ row.ended_at ? new Date(row.ended_at).toLocaleTimeString() : '—' }}</span>
        </template>
        <template #cell-error="{ row }">
          <span class="text-danger text-xs truncate max-w-[200px] inline-block">{{ row.error ?? '' }}</span>
        </template>
      </StepsTable>
      <p
        v-else
        class="text-muted"
      >
        {{ $t('workflow.run.noSteps') }}
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { LockClosedIcon } from '@heroicons/vue/24/outline'
import { useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SStatusBadge, STable } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDateTime } from '@shared/utils/datetime'
import { useI18n } from 'vue-i18n'
import { cancelRun, getRun, listSteps } from '../api'
import type { WorkflowStep } from '../types'
import { useWorkflowRunSocket } from '../composables/useWorkflowRunSocket'
import { wfKeys } from '../queries'

// Same STable row-generic pin as the other workflow tables.
const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
const StepsTable = STable as unknown as new () => {
  $props: Omit<STablePropsBase, 'data'> & { data?: WorkflowStep[] }
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const runId = route.params.runId as string
const shortRunId = (runId ?? '').slice(0, 8)
const toast = useToast()

const { connected } = useWorkflowRunSocket(runId)

const TERMINAL_STATES = new Set<string>(['succeeded', 'failed', 'cancelled'])

const columns = computed<Column[]>(() => [
  { key: 'node_id', label: t('workflow.run.nodeId'), cellType: 'text' },
  { key: 'state', label: t('workflow.run.state'), cellType: 'badge' },
  { key: 'started_at', label: t('workflow.run.started'), cellType: 'date', hideBelow: 'sm' },
  { key: 'ended_at', label: t('workflow.run.ended'), cellType: 'date', hideBelow: 'sm' },
  { key: 'error', label: t('workflow.run.error'), cellType: 'text', hideBelow: 'md' },
])

// A run read is Admin-or-project-owner ([R14.10]). This route is keyed by run id
// alone, so — unlike the workspace-scoped workflow views — there is no project to
// resolve the caller's role against up front; the server's answer is the only
// signal, and it is turned into an empty state rather than a silent blank page
// or a toast (dossier §7).
const isForbidden = (e: unknown): boolean => e instanceof ApiError && e.status === 403

const runQuery = useQuery({
  queryKey: wfKeys.run(runId),
  queryFn: () => getRun(runId),
  retry: (count, e) => !isForbidden(e) && count < 3,
  refetchInterval: (q) =>
    isForbidden(q.state.error) ||
    TERMINAL_STATES.has((q.state.data as { state?: string } | undefined)?.state ?? '')
      ? false
      : 10_000,
})

const stepsQuery = useQuery({
  queryKey: wfKeys.steps(runId),
  queryFn: () => listSteps(runId),
  retry: (count, e) => !isForbidden(e) && count < 3,
  refetchInterval: (q) =>
    isForbidden(q.state.error) || TERMINAL_STATES.has(runQuery.data.value?.state ?? '')
      ? false
      : 5_000,
})

const forbidden = computed(
  () => isForbidden(runQuery.error.value) || isForbidden(stepsQuery.error.value),
)

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
