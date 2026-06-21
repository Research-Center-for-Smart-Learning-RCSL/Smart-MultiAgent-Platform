<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { computed, watch } from 'vue'

import { SFormField } from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import AgentFormFields from '../components/AgentFormFields.vue'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'
import { agentCreateSchema, type AgentCreateInput } from '../types/schemas'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const agentId = route.params.agentId as string

const query = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: async () => {
    const { data } = await agentsApi.get(agentId)
    return data
  },
})

// `project_id` (needed to scope the pickers) travels on the agent payload, not
// the route, so both queries stay disabled until the agent has loaded.
const pickerProjectId = computed(() => query.data.value?.project_id ?? '')

// These three pickers fetch the same project-scoped lists the RAG/GraphRAG list
// views own, so they MUST use the shared factory keys — otherwise the list
// views' create/delete invalidations (keyed by projectId) never reach this
// cache and the pickers serve stale options after a config is created.
const keyGroupsQuery = useQuery({
  queryKey: computed(() => keysKeys.keyGroups(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => {
    const { data } = await keyGroupsApi.listForProject(pickerProjectId.value)
    return data
  },
})

const ragConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.ragConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => {
    const { data } = await agentsApi.listRagConfigs(pickerProjectId.value)
    return data
  },
})

// A GraphRAG config is 1:1 with an agent, so the only config this agent may bind
// is the one built for it. Setting `graphrag_config_id` here is what the runtime
// reads to enable graph retrieval (turn_engine reads agent.graphrag_config_id).
const graphragConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.graphragConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => {
    const { data } = await agentsApi.listGraphragConfigs(pickerProjectId.value)
    return data
  },
})

const thisAgentGraphrag = computed(() =>
  (graphragConfigsQuery.data.value ?? []).find((c) => c.agent_id === agentId),
)

const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<AgentCreateInput>({
  validationSchema: schema,
})

const [name] = defineField('name')
const [modelHint] = defineField('model_hint')
const [modelId] = defineField('model_id')
const [keyGroupId] = defineField('key_group_id')
const [systemPrompt] = defineField('system_prompt')
const [promptStrategy] = defineField('prompt_strategy')
const [contextMode] = defineField('context_mode')
const [ragConfigId] = defineField('rag_config_id')
const [graphragConfigId] = defineField('graphrag_config_id')
const [a2aEnabled] = defineField('a2a_enabled')

const { applyServerErrors } = useServerErrors(setErrors)

watch(
  () => query.data.value,
  (agent) => {
    if (!agent) return
    resetForm({
      values: {
        name: agent.name,
        model_hint: agent.model_hint as AgentCreateInput['model_hint'],
        model_id: agent.model_id ?? null,
        key_group_id: agent.key_group_id,
        system_prompt: agent.system_prompt,
        prompt_strategy: agent.prompt_strategy as AgentCreateInput['prompt_strategy'],
        context_mode: agent.context_mode as AgentCreateInput['context_mode'],
        rag_config_id: agent.rag_config_id,
        graphrag_config_id: agent.graphrag_config_id,
        context_token_cap: agent.context_token_cap,
        a2a_enabled: agent.a2a_enabled,
      },
    })
  },
  { immediate: true },
)

const patchMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
    const agent = query.data.value
    if (!agent) throw new Error('Agent not loaded')
    const { data } = await agentsApi.patch(agentId, agent.version, values)
    return data
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.agent(agentId) })
    toast.success(t('agents.detail.saved'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) {
      toast.error(t('agents.detail.saveFailed'))
    }
  },
})

const deleteMutation = useMutation({
  mutationFn: () => agentsApi.remove(agentId, query.data.value!.version),
  onSuccess: () => {
    router.back()
  },
  onError: () => toast.error(t('agents.detail.deleteFailed')),
})

async function onDelete(): Promise<void> {
  const ok = await confirm({ title: t('agents.detail.deleteConfirmTitle'), message: t('agents.detail.deleteConfirm'), variant: 'warning', confirmLabel: t('agents.detail.delete'), cancelLabel: t('app.cancel') })
  if (!ok) return
  deleteMutation.mutate()
}

const onSubmit = handleSubmit((values) => {
  patchMutation.mutate(values)
})
</script>

<template>
  <section class="agent-detail p-6">
    <h1 class="text-xl font-semibold mb-4">
      {{ query.data.value?.name ?? t('agents.detail.title') }}
    </h1>

    <div
      v-if="query.isLoading.value"
      class="agent-detail__loading"
    >
      {{ t('agents.detail.loading') }}
    </div>

    <form
      v-else-if="query.data.value"
      class="agent-detail__form"
      @submit.prevent="onSubmit"
    >
      <AgentFormFields
        v-model:name="name"
        v-model:model-hint="modelHint"
        v-model:model-id="modelId"
        v-model:key-group-id="keyGroupId"
        v-model:system-prompt="systemPrompt"
        v-model:prompt-strategy="promptStrategy"
        v-model:context-mode="contextMode"
        v-model:rag-config-id="ragConfigId"
        v-model:a2a-enabled="a2aEnabled"
        :errors="errors"
        :key-groups="keyGroupsQuery.data.value ?? []"
        :rag-configs="ragConfigsQuery.data.value ?? []"
        :textarea-rows="6"
      >
        <template #after-rag>
          <RouterLink
            v-if="ragConfigId"
            class="agent-detail__rag-manage"
            :to="{
              name: 'agents.ragConfig',
              params: { projectId: pickerProjectId, configId: ragConfigId },
            }"
          >
            {{ t('agents.rag.manageLink') }}
          </RouterLink>
          <RouterLink
            class="agent-detail__rag-manage"
            :to="{ name: 'agents.ragConfigs', params: { projectId: pickerProjectId } }"
          >
            {{ t('agents.form.manageRagConfigs') }}
          </RouterLink>
        </template>

        <template #extra-fields>
          <SFormField
            :label="t('agents.form.graphragConfig')"
            name="graphrag_config_id"
            :error="errors.graphrag_config_id"
          >
            <select
              id="graphrag_config_id"
              v-model="graphragConfigId"
            >
              <option :value="null">
                {{ t('agents.form.graphragConfigNone') }}
              </option>
              <option
                v-if="thisAgentGraphrag"
                :value="thisAgentGraphrag.id"
              >
                {{ t('agents.form.graphragConfigThis') }}
              </option>
            </select>
            <RouterLink
              class="agent-detail__rag-manage"
              :to="{ name: 'agents.graphragConfigs', params: { projectId: pickerProjectId } }"
            >
              {{ t('agents.form.manageGraphragConfigs') }}
            </RouterLink>
          </SFormField>

          <SFormField
            :label="t('agents.form.mcp')"
            name="mcp"
          >
            <RouterLink
              class="agent-detail__rag-manage"
              :to="{ name: 'agents.mcp', params: { agentId } }"
            >
              {{ t('agents.form.manageMcp') }}
            </RouterLink>
          </SFormField>

          <SFormField
            :label="t('agents.form.orchestration')"
            name="orchestration"
          >
            <RouterLink
              class="agent-detail__rag-manage"
              :to="{ name: 'workflow.agentOrchestration', params: { agentId } }"
            >
              {{ t('agents.form.manageOrchestration') }}
            </RouterLink>
          </SFormField>
        </template>
      </AgentFormFields>

      <div class="agent-detail__actions">
        <button
          type="submit"
          class="btn btn-primary"
          :disabled="patchMutation.isPending.value"
        >
          {{ t('agents.detail.save') }}
        </button>
        <button
          type="button"
          class="btn btn-danger"
          :disabled="deleteMutation.isPending.value"
          @click="onDelete()"
        >
          {{ t('agents.detail.delete') }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.agent-detail__form {
  max-width: 560px;
}
.agent-detail__actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}
</style>
