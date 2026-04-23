<template>
  <section class="workflow-backstage p-4">
    <header class="mb-4">
      <h1 class="text-xl font-semibold">{{ $t('workflow.backstage.title') }}</h1>
      <p class="text-sm text-gray-500">
        {{ $t('workflow.backstage.subtitle') }}
      </p>
    </header>

    <!-- Run selector -->
    <div class="mb-4">
      <label class="text-sm font-medium block mb-1">{{ $t('workflow.backstage.selectRun') }}</label>
      <select
        v-model="selectedRunId"
        class="border rounded px-2 py-1 text-sm w-full max-w-xs"
        @change="onRunSelected"
      >
        <option value="">—</option>
        <option v-for="r in runs" :key="r.id" :value="r.id">
          {{ r.trigger_type }} · {{ r.state }} · {{ new Date(r.started_at).toLocaleString() }}
        </option>
      </select>
    </div>

    <template v-if="selectedRunId">
      <!-- Trace: step timeline -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">{{ $t('workflow.backstage.trace') }}</h2>
        <p v-if="stepsQuery.isLoading.value" class="text-gray-500">…</p>
        <div v-else-if="stepsList.length" class="space-y-1">
          <div
            v-for="step in stepsList"
            :key="step.id"
            class="flex items-start gap-2 text-xs border-l-2 pl-3 py-1"
            :class="stepBorderClass(step.state)"
          >
            <span class="font-mono w-32 shrink-0">{{ step.node_id }}</span>
            <span
              class="px-1.5 py-0.5 rounded"
              :class="stepBgClass(step.state)"
            >
              {{ step.state }}
            </span>
            <span class="text-gray-400">
              {{ new Date(step.started_at).toLocaleTimeString() }}
              {{ step.ended_at ? '→ ' + new Date(step.ended_at).toLocaleTimeString() : '' }}
            </span>
            <span v-if="step.error" class="text-red-600 truncate max-w-[300px]">
              {{ step.error }}
            </span>
          </div>
        </div>
        <p v-else class="text-gray-400 text-sm">{{ $t('workflow.backstage.noSteps') }}</p>
      </div>

      <!-- Sub-agent tree -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">{{ $t('workflow.backstage.subagentTree') }}</h2>
        <SubagentTree v-if="selectedRunId" :parent-instance-id="selectedRunId" :agent-names="agentNames" />
      </div>

      <!-- Instruction chains -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">{{ $t('workflow.backstage.instructChains') }}</h2>
        <InstructChainView v-if="selectedRunId" :chain-id="selectedRunId" :agent-names="agentNames" />
      </div>

      <!-- Approval histories -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">{{ $t('workflow.backstage.approvalHistory') }}</h2>
        <div v-if="approvalsQuery.data.value?.length" class="space-y-2">
          <ApprovalCard
            v-for="a in approvalsQuery.data.value"
            :key="a.id"
            :approval="a"
            :agent-names="agentNames"
          />
        </div>
        <p v-else class="text-gray-400 text-sm">{{ $t('workflow.backstage.noApprovals') }}</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { listApprovalsForRun, listRuns, listSteps } from '../api'
import { wfKeys } from '../queries'
import ApprovalCard from '../components/ApprovalCard.vue'
import InstructChainView from '../components/InstructChainView.vue'
import SubagentTree from '../components/SubagentTree.vue'

const route = useRoute()
const workflowId = route.params.workflowId as string
const selectedRunId = ref('')
const agentNames = ref<Record<string, string>>({})

const runsQuery = useQuery({
  queryKey: wfKeys.runs(workflowId),
  queryFn: () => listRuns(workflowId, { limit: 100, includeArchive: true }),
})

const runs = computed(() => runsQuery.data.value ?? [])

const stepsQuery = useQuery({
  queryKey: computed(() => wfKeys.steps(selectedRunId.value)),
  queryFn: () => listSteps(selectedRunId.value),
  enabled: computed(() => !!selectedRunId.value),
})

const approvalsQuery = useQuery({
  queryKey: computed(() => wfKeys.approvals(selectedRunId.value)),
  queryFn: () => listApprovalsForRun(selectedRunId.value),
  enabled: computed(() => !!selectedRunId.value),
})

const stepsList = computed(() => stepsQuery.data.value ?? [])

function onRunSelected(): void {
  // queries auto-refetch on key change
}

function stepBorderClass(state: string): string {
  switch (state) {
    case 'running': return 'border-blue-400'
    case 'succeeded': return 'border-green-400'
    case 'failed': return 'border-red-400'
    case 'cancelled': return 'border-gray-300'
    default: return 'border-gray-200'
  }
}

function stepBgClass(state: string): string {
  switch (state) {
    case 'running': return 'bg-blue-100 text-blue-700'
    case 'succeeded': return 'bg-green-100 text-green-700'
    case 'failed': return 'bg-red-100 text-red-700'
    case 'cancelled': return 'bg-gray-100 text-gray-600'
    case 'pending': return 'bg-gray-50 text-gray-400'
    default: return ''
  }
}
</script>
