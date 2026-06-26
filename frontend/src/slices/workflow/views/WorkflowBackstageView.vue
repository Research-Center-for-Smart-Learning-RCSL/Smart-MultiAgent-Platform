<template>
  <section class="workflow-backstage p-4">
    <SPageHeader
      :title="$t('workflow.backstage.title')"
      :subtitle="$t('workflow.backstage.subtitle')"
    />

    <!-- Run selector -->
    <div class="mb-4">
      <label class="text-sm font-medium block mb-1">
        {{ $t('workflow.backstage.selectRun') }}
        <select
          v-model="selectedRunId"
          class="border rounded px-2 py-1 text-sm w-full max-w-xs"
        >
          <option value="">
            —
          </option>
          <option
            v-for="r in runs"
            :key="r.id"
            :value="r.id"
          >
            {{ r.trigger_type }} · {{ r.state }} · {{ new Date(r.started_at).toLocaleString() }}
          </option>
        </select>
      </label>
    </div>

    <template v-if="selectedRunId">
      <!-- Trace: step timeline -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">
          {{ $t('workflow.backstage.trace') }}
        </h2>
        <p
          v-if="stepsQuery.isLoading.value"
          class="text-muted"
        >
          …
        </p>
        <div
          v-else-if="stepsList.length"
          class="space-y-1"
        >
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
            <span class="text-muted">
              {{ new Date(step.started_at).toLocaleTimeString() }}
              {{ step.ended_at ? '→ ' + new Date(step.ended_at).toLocaleTimeString() : '' }}
            </span>
            <span
              v-if="step.error"
              class="text-danger truncate max-w-[180px] sm:max-w-[300px]"
            >
              {{ step.error }}
            </span>
          </div>
        </div>
        <p
          v-else
          class="text-muted text-sm"
        >
          {{ $t('workflow.backstage.noSteps') }}
        </p>
      </div>

      <!-- Sub-agent tree -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">
          {{ $t('workflow.backstage.subagentTree') }}
        </h2>
        <SubagentTree
          v-if="selectedRunId"
          :run-id="selectedRunId"
          :agent-names="agentNames"
        />
      </div>

      <!-- Instruction chains -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">
          {{ $t('workflow.backstage.instructChains') }}
        </h2>
        <div
          v-if="chainIds.length"
          class="space-y-3"
        >
          <InstructChainView
            v-for="cid in chainIds"
            :key="cid"
            :chain-id="cid"
            :agent-names="agentNames"
          />
        </div>
        <p
          v-else
          class="text-muted text-sm"
        >
          {{ $t('workflow.backstage.noChains') }}
        </p>
      </div>

      <!-- Approval histories -->
      <div class="mb-6">
        <h2 class="font-semibold mb-2">
          {{ $t('workflow.backstage.approvalHistory') }}
        </h2>
        <div
          v-if="approvalsQuery.data.value?.length"
          class="space-y-2"
        >
          <ApprovalCard
            v-for="a in approvalsQuery.data.value"
            :key="a.id"
            :approval="a"
            :agent-names="agentNames"
          />
        </div>
        <p
          v-else
          class="text-muted text-sm"
        >
          {{ $t('workflow.backstage.noApprovals') }}
        </p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { SPageHeader, STATUS_BG_MAP } from '@shared/ui'
import { useSessionStore } from '@slices/identity'
import { getWorkspace } from '@slices/conversation'
import { projectsApi, tenancyKeys } from '@slices/tenancy'
import { getApproval, getInstruction, listApprovalsForRun, listRuns, listSteps } from '../api'
import { wfKeys } from '../queries'
import type { ApprovalWithVotes } from '../types'
import { fetchProjectAgents } from '../utils/projectAgents'
import ApprovalCard from '../components/ApprovalCard.vue'
import InstructChainView from '../components/InstructChainView.vue'
import SubagentTree from '../components/SubagentTree.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const workflowId = route.params.workflowId as string
const workspaceId = route.params.workspaceId as string
const selectedRunId = ref('')
const agentNames = ref<Record<string, string>>({})

// ---- authorization: platform admin OR project owner ----------------------
// The route guard only knows the global admin flag, so resolve the per-project
// role here (workspace -> project -> my membership). Members fetch is skipped
// for admins, who are always allowed.
const isAdmin = computed(() => session.me?.is_admin ?? false)

const workspaceQuery = useQuery({
  queryKey: computed(() => ['workflow', 'backstage', 'workspace', workspaceId]),
  queryFn: () => getWorkspace(workspaceId),
  enabled: computed(() => !isAdmin.value),
})

const projectId = computed(() => workspaceQuery.data.value?.project_id ?? '')

const membersQuery = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
  queryFn: () => projectsApi.listMembers(projectId.value).then((r) => r.data),
  enabled: computed(() => !isAdmin.value && !!projectId.value),
})

const isOwner = computed(() => {
  const me = session.me
  const members = membersQuery.data.value
  if (!me || !members) return false
  return members.find((m) => m.user_id === me.id)?.role === 'owner'
})

const isAuthorized = computed(() => isAdmin.value || isOwner.value)

// Only conclude "denied" once the per-project role has actually resolved, so a
// legitimate owner isn't bounced mid-load.
const authDecided = computed(() => {
  if (isAdmin.value) return true
  if (workspaceQuery.isError.value) return true
  return membersQuery.isSuccess.value || membersQuery.isError.value
})

watch(
  [authDecided, isAuthorized],
  ([decided, ok]) => {
    if (decided && !ok) router.replace({ name: 'root' })
  },
  { immediate: true },
)

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

// The list endpoint returns approvals without their votes; fetch each one in
// full so the backstage cards can render the voter breakdown (spec 5.6). Use
// allSettled so one archived/erroring approval does not collapse the panel —
// the readable ones still render.
const approvalsQuery = useQuery({
  queryKey: computed(() => wfKeys.approvals(selectedRunId.value)),
  queryFn: async () => {
    const list = await listApprovalsForRun(selectedRunId.value)
    const settled = await Promise.allSettled(list.map((a) => getApproval(a.id)))
    return settled
      .filter((r): r is PromiseFulfilledResult<ApprovalWithVotes> => r.status === 'fulfilled')
      .map((r) => r.value)
  },
  enabled: computed(() => !!selectedRunId.value),
})

const stepsList = computed(() => stepsQuery.data.value ?? [])

// Derive the distinct instruction-chain ids for the run from its instruct
// steps: each instruct step stores `{ instruction_id }` in its output, and an
// instruction record carries the chain id it belongs to. The getInstruction
// lookup is project-scoped on the backend, so admins and project owners alike
// can resolve it here.
const chainIds = ref<string[]>([])

// Monotonic guard: each run change bumps this so a slower in-flight resolution
// for a previously-selected run cannot land last and overwrite fresher chain
// ids (same stale-overwrite race the run socket guards with syncGeneration).
let chainGeneration = 0

watch(
  stepsList,
  async (steps) => {
    const generation = ++chainGeneration
    const instructionIds = steps
      .map((s) => (s.output as { instruction_id?: unknown } | null)?.instruction_id)
      .filter((v): v is string => typeof v === 'string')
    if (!instructionIds.length) {
      chainIds.value = []
      return
    }
    const seen = new Set<string>()
    await Promise.all(
      instructionIds.map(async (iid) => {
        try {
          const instr = await getInstruction(iid)
          seen.add(instr.chain_id)
        } catch {
          // Non-fatal — instruction may be archived or unreadable.
        }
      }),
    )
    if (generation !== chainGeneration) return
    chainIds.value = [...seen]
  },
  { immediate: true },
)

// Map agent ids to display names so the sub-agent tree / instruction chain
// render readable labels instead of truncated ids. Failure is non-fatal —
// labels fall back to ids.
async function loadAgentNames(): Promise<void> {
  try {
    const agents = await fetchProjectAgents(workspaceId)
    const map: Record<string, string> = {}
    for (const a of agents) map[a.id] = a.name
    agentNames.value = map
  } catch {
    // Non-fatal.
  }
}
onMounted(loadAgentNames)

function stepBorderClass(state: string): string {
  switch (state) {
    case 'running': return 'border-accent'
    case 'succeeded': return 'border-success'
    case 'failed': return 'border-danger'
    case 'cancelled': return 'border-muted'
    default: return 'border-border'
  }
}

function stepBgClass(state: string): string {
  return STATUS_BG_MAP[state] ?? ''
}
</script>
