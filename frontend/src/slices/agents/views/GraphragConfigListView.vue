<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { ElMessageBox } from 'element-plus'

import { FormField } from '@shared/ui'
import { useServerErrors, usePolling, useToast } from '@shared/composables'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { agentsApi, type GraphragConfig } from '../api'
import { agentKeys } from '../queries'
import {
  graphragConfigCreateSchema,
  type GraphragConfigCreateInput,
} from '../types/schemas'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()

const showForm = ref(false)

const configsQuery = useQuery({
  queryKey: agentKeys.graphragConfigs(projectId),
  queryFn: async () => (await agentsApi.listGraphragConfigs(projectId)).data,
})

const agentsQuery = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => (await agentsApi.list(projectId)).data,
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: async () => (await keyGroupsApi.listForProject(projectId)).data,
})

const agentById = computed(() =>
  new Map((agentsQuery.data.value ?? []).map((a) => [a.id, a])),
)
const keyGroupById = computed(() =>
  new Map((keyGroupsQuery.data.value ?? []).map((g) => [g.id, g.name])),
)
const keyGroupName = (id: string): string => keyGroupById.value.get(id) ?? id

// A config is "bound" only when its agent actually points back at it
// (turn_engine reads agent.graphrag_config_id). Creating + building a config is
// inert until the agent is bound on its detail page — surface that explicitly.
const isBound = (cfg: GraphragConfig): boolean =>
  agentById.value.get(cfg.agent_id)?.graphrag_config_id === cfg.id

// A GraphRAG config is 1:1 with an agent — only agents without one yet can take
// a new config.
const configuredAgentIds = computed(
  () => new Set((configsQuery.data.value ?? []).map((c) => c.agent_id)),
)
const availableAgents = computed(() =>
  (agentsQuery.data.value ?? []).filter((a) => !configuredAgentIds.value.has(a.id)),
)
const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)
const canCreate = computed(() => availableAgents.value.length > 0 && hasKeyGroups.value)

const schema = toTypedSchema(graphragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } =
  useForm<GraphragConfigCreateInput>({
    validationSchema: schema,
    initialValues: { agent_id: '', builder_key_group_id: '', trigger_config: {} },
  })

const [agentId] = defineField('agent_id')
const [builderKeyGroupId] = defineField('builder_key_group_id')

// The builder key group must differ from the chosen agent's own consumer key
// group (billing separation, GraphRagBuilderKeyGroupConflict) — exclude it.
const builderKeyGroups = computed(() => {
  const consumer = agentId.value ? agentById.value.get(agentId.value)?.key_group_id : undefined
  return (keyGroupsQuery.data.value ?? []).filter((g) => g.id !== consumer)
})

// If switching agent invalidates the current builder pick, clear it.
watch(agentId, () => {
  if (
    builderKeyGroupId.value &&
    !builderKeyGroups.value.some((g) => g.id === builderKeyGroupId.value)
  ) {
    builderKeyGroupId.value = ''
  }
})

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (values: GraphragConfigCreateInput) =>
    (await agentsApi.createGraphragConfig(projectId, values)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    resetForm()
    showForm.value = false
    toast.success(t('agents.graphragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.graphragList.createFailed'))
  },
})

const onSubmit = handleSubmit((values) => createMutation.mutate(values))

// Build is a 2PC machine (BuildState): idle → running → neo4j_committed →
// qdrant_committed (success) | failed_compensating → failed. GraphRAG has no WS
// channel, so after a manual build we poll the status endpoint until it settles,
// overriding the row's displayed state live.
const IN_PROGRESS = new Set(['running', 'neo4j_committed', 'failed_compensating'])
const liveState = ref<Record<string, string>>({})

// The effective state of a row prefers the live-polled value over the cached
// last_build_state, so the per-row Build button can disable itself the moment a
// build is in flight (preventing duplicate enqueues against an in-progress 2PC).
const effectiveState = (cfg: GraphragConfig): string =>
  liveState.value[cfg.id] ?? cfg.last_build_state
const isBuilding = (cfg: GraphragConfig): boolean => IN_PROGRESS.has(effectiveState(cfg))

// Poll the status endpoint until the 2PC build settles (shared usePolling:
// bounded, transient errors reschedule, timers cleared on unmount). Keyed by
// config id so several builds can poll at once.
const statusPoll = usePolling((id) => agentsApi.getGraphragStatus(id).then((r) => r.data), {
  isTerminal: (s) => !IN_PROGRESS.has(s.state),
  onResult: (id, s) => {
    liveState.value = { ...liveState.value, [id]: s.state }
    if (!IN_PROGRESS.has(s.state)) {
      qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    }
  },
})

const buildMutation = useMutation({
  mutationFn: (id: string) => agentsApi.buildGraphrag(id),
  onSuccess: (_data, id) => {
    toast.success(t('agents.graphragList.buildStarted'))
    statusPoll.start(id)
  },
  onError: () => toast.error(t('agents.graphragList.buildFailed')),
})

// Optimistically mark the row in-progress before the POST resolves so a fast
// 202 can't re-enable the button (the mutation's own isPending clears on 202,
// long before the multi-minute build settles).
function startBuild(id: string): void {
  liveState.value = { ...liveState.value, [id]: 'running' }
  buildMutation.mutate(id)
}

const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteGraphragConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    toast.success(t('agents.graphragList.deleted'))
  },
  onError: () => toast.error(t('agents.graphragList.deleteFailed')),
})

async function confirmDelete(cfg: GraphragConfig): Promise<void> {
  const label = agentById.value.get(cfg.agent_id)?.name ?? cfg.agent_id
  try {
    await ElMessageBox.confirm(
      t('agents.graphragList.deleteConfirm', { name: label }),
      t('agents.graphragList.deleteTitle'),
      { type: 'warning' },
    )
  } catch {
    return // dismissed
  }
  deleteMutation.mutate(cfg.id)
}

function refresh(): void {
  qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
}
</script>

<template>
  <section class="graphrag-list p-6">
    <div class="graphrag-list__header">
      <h1 class="text-xl font-semibold mb-4">
        {{ t('agents.graphragList.title') }}
      </h1>
      <div>
        <button
          class="btn"
          type="button"
          @click="refresh"
        >
          {{ t('agents.graphragList.refresh') }}
        </button>
        <button
          class="btn btn-primary"
          :disabled="!canCreate"
          @click="showForm = !showForm"
        >
          {{ showForm ? t('agents.graphragList.cancel') : t('agents.graphragList.create') }}
        </button>
      </div>
    </div>

    <p
      v-if="!agentsQuery.isLoading.value && (agentsQuery.data.value?.length ?? 0) === 0"
      class="graphrag-list__warning"
      role="alert"
    >
      {{ t('agents.graphragList.noAgents') }}
    </p>
    <p
      v-else-if="!keyGroupsQuery.isLoading.value && !hasKeyGroups"
      class="graphrag-list__warning"
      role="alert"
    >
      {{ t('agents.graphragList.noKeyGroups') }}
    </p>
    <p
      v-else-if="!configsQuery.isLoading.value && availableAgents.length === 0"
      class="graphrag-list__warning"
      role="alert"
    >
      {{ t('agents.graphragList.allConfigured') }}
    </p>

    <form
      v-if="showForm"
      class="graphrag-list__form"
      @submit.prevent="onSubmit"
    >
      <p class="graphrag-list__hint">
        {{ t('agents.graphragList.builderHint') }}
      </p>

      <FormField
        :label="t('agents.graphragForm.agent')"
        name="agent_id"
        :error="errors.agent_id"
        required
      >
        <select
          id="agent_id"
          v-model="agentId"
        >
          <option
            value=""
            disabled
          >
            {{ t('agents.graphragForm.agentPlaceholder') }}
          </option>
          <option
            v-for="a in availableAgents"
            :key="a.id"
            :value="a.id"
          >
            {{ a.name }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.graphragForm.builderKeyGroup')"
        name="builder_key_group_id"
        :error="errors.builder_key_group_id"
        required
      >
        <select
          id="builder_key_group_id"
          v-model="builderKeyGroupId"
          :disabled="!agentId"
        >
          <option
            value=""
            disabled
          >
            {{ t('agents.graphragForm.builderKeyGroupPlaceholder') }}
          </option>
          <option
            v-for="g in builderKeyGroups"
            :key="g.id"
            :value="g.id"
          >
            {{ g.name }}
          </option>
        </select>
      </FormField>

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value"
      >
        {{ t('agents.graphragForm.submit') }}
      </button>
    </form>

    <p v-if="configsQuery.isLoading.value">
      {{ t('agents.graphragList.loading') }}
    </p>
    <table
      v-else
      class="graphrag-list__table"
    >
      <thead>
        <tr>
          <th>{{ t('agents.graphragList.colAgent') }}</th>
          <th>{{ t('agents.graphragList.colBuilder') }}</th>
          <th>{{ t('agents.graphragList.colBinding') }}</th>
          <th>{{ t('agents.graphragList.colState') }}</th>
          <th>{{ t('agents.graphragList.colActions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="c in configsQuery.data.value ?? []"
          :key="c.id"
        >
          <td>{{ agentById.get(c.agent_id)?.name ?? c.agent_id }}</td>
          <td>{{ keyGroupName(c.builder_key_group_id) }}</td>
          <td>
            <span v-if="isBound(c)">{{ t('agents.graphragList.bound') }}</span>
            <span
              v-else
              class="graphrag-list__error"
              :title="t('agents.graphragList.unboundHint')"
            >{{ t('agents.graphragList.unbound') }}</span>
          </td>
          <td>
            {{ effectiveState(c) }}
            <span
              v-if="c.last_build_error"
              class="graphrag-list__error"
              :title="c.last_build_error"
            >{{ t('agents.graphragList.errorFlag') }}</span>
          </td>
          <td>
            <button
              class="btn"
              type="button"
              :disabled="isBuilding(c)"
              @click="startBuild(c.id)"
            >
              {{ t('agents.graphragList.build') }}
            </button>
            <button
              class="btn btn-danger"
              type="button"
              @click="confirmDelete(c)"
            >
              {{ t('agents.graphragList.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="(configsQuery.data.value ?? []).length === 0">
          <td colspan="5">
            {{ t('agents.graphragList.empty') }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.graphrag-list__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.graphrag-list__form {
  max-width: 480px;
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.graphrag-list__hint {
  color: var(--color-muted);
  font-size: 0.875rem;
  margin-bottom: var(--space-3);
}
.graphrag-list__warning {
  color: var(--color-danger, #b91c1c);
  margin-bottom: var(--space-3);
}
.graphrag-list__table {
  width: 100%;
  border-collapse: collapse;
}
.graphrag-list__table th,
.graphrag-list__table td {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.graphrag-list__error {
  color: var(--color-danger, #b91c1c);
  cursor: help;
}
</style>
