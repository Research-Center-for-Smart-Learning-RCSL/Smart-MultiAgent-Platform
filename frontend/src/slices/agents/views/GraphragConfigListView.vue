<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { ElMessage, ElMessageBox } from 'element-plus'

import { FormField } from '@shared/ui'
import { useServerErrors } from '@shared/composables'
import { keyGroupsApi } from '@slices/keys'
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
  queryKey: ['keys', 'keyGroups', projectId],
  queryFn: async () => (await keyGroupsApi.listForProject(projectId)).data,
})

const agentById = computed(() =>
  new Map((agentsQuery.data.value ?? []).map((a) => [a.id, a])),
)
const keyGroupName = (id: string): string =>
  (keyGroupsQuery.data.value ?? []).find((g) => g.id === id)?.name ?? id

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
    ElMessage.success(t('agents.graphragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) ElMessage.error(t('agents.graphragList.createFailed'))
  },
})

const onSubmit = handleSubmit((values) => createMutation.mutate(values))

// Build is a 2PC machine (BuildState): idle → running → neo4j_committed →
// qdrant_committed (success) | failed_compensating → failed. GraphRAG has no WS
// channel, so after a manual build we poll the status endpoint until it settles,
// overriding the row's displayed state live.
const IN_PROGRESS = new Set(['running', 'neo4j_committed', 'failed_compensating'])
const liveState = ref<Record<string, string>>({})

async function pollStatus(id: string, attempts = 0): Promise<void> {
  if (attempts > 40) return // ~2 min ceiling at 3 s/poll
  try {
    const { data } = await agentsApi.getGraphragStatus(id)
    liveState.value = { ...liveState.value, [id]: data.state }
    if (IN_PROGRESS.has(data.state)) {
      setTimeout(() => void pollStatus(id, attempts + 1), 3000)
    } else {
      qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    }
  } catch {
    // stop polling; the manual Refresh button remains available
  }
}

const buildMutation = useMutation({
  mutationFn: (id: string) => agentsApi.buildGraphrag(id),
  onSuccess: (_data, id) => {
    ElMessage.success(t('agents.graphragList.buildStarted'))
    void pollStatus(id)
  },
  onError: () => ElMessage.error(t('agents.graphragList.buildFailed')),
})

const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteGraphragConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    ElMessage.success(t('agents.graphragList.deleted'))
  },
  onError: () => ElMessage.error(t('agents.graphragList.deleteFailed')),
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
            {{ liveState[c.id] ?? c.last_build_state }}
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
              :disabled="buildMutation.isPending.value"
              @click="buildMutation.mutate(c.id)"
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
          <td colspan="4">
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
