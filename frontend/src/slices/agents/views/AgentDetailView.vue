<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { computed, watch } from 'vue'

import { ElMessage, ElMessageBox } from 'element-plus'
import { FormField } from '@shared/ui'
import { useServerErrors } from '@shared/composables'
import { keyGroupsApi } from '@slices/keys'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'
import { agentCreateSchema, type AgentCreateInput } from '../types/schemas'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
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

const keyGroupsQuery = useQuery({
  queryKey: ['keys', 'keyGroups', 'agent', agentId],
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => {
    const { data } = await keyGroupsApi.listForProject(pickerProjectId.value)
    return data
  },
})

const ragConfigsQuery = useQuery({
  queryKey: ['agents', 'ragConfigs', 'agent', agentId],
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => {
    const { data } = await agentsApi.listRagConfigs(pickerProjectId.value)
    return data
  },
})

const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<AgentCreateInput>({
  validationSchema: schema,
})

const [name] = defineField('name')
const [modelHint] = defineField('model_hint')
const [keyGroupId] = defineField('key_group_id')
const [systemPrompt] = defineField('system_prompt')
const [promptStrategy] = defineField('prompt_strategy')
const [contextMode] = defineField('context_mode')
const [ragConfigId] = defineField('rag_config_id')
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
    ElMessage.success(t('agents.detail.saved'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) {
      ElMessage.error(t('agents.detail.saveFailed'))
    }
  },
})

const deleteMutation = useMutation({
  mutationFn: () => agentsApi.remove(agentId),
  onSuccess: () => {
    router.back()
  },
  onError: () => ElMessage.error(t('agents.detail.deleteFailed')),
})

async function onDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('agents.detail.deleteConfirm'),
      t('agents.detail.deleteConfirmTitle'),
      { confirmButtonText: t('agents.detail.delete'), cancelButtonText: t('app.cancel'), type: 'warning' },
    )
  } catch {
    return
  }
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
      <FormField
        :label="t('agents.form.name')"
        name="name"
        :error="errors.name"
        required
      >
        <input
          id="name"
          v-model="name"
          :aria-describedby="errors.name ? 'name-error' : undefined"
          :aria-invalid="!!errors.name"
        >
      </FormField>

      <FormField
        :label="t('agents.form.modelHint')"
        name="model_hint"
        :error="errors.model_hint"
        required
      >
        <select
          id="model_hint"
          v-model="modelHint"
        >
          <option value="claude">
            Claude
          </option>
          <option value="openai">
            OpenAI
          </option>
          <option value="gemini">
            Gemini
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.keyGroup')"
        name="key_group_id"
        :error="errors.key_group_id"
        required
      >
        <select
          id="key_group_id"
          v-model="keyGroupId"
        >
          <option
            value=""
            disabled
          >
            {{ t('agents.form.keyGroupPlaceholder') }}
          </option>
          <option
            v-for="g in keyGroupsQuery.data.value ?? []"
            :key="g.id"
            :value="g.id"
          >
            {{ g.name }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.systemPrompt')"
        name="system_prompt"
        :error="errors.system_prompt"
      >
        <textarea
          id="system_prompt"
          v-model="systemPrompt"
          rows="6"
        />
      </FormField>

      <FormField
        :label="t('agents.form.promptStrategy')"
        name="prompt_strategy"
        :error="errors.prompt_strategy"
      >
        <select
          id="prompt_strategy"
          v-model="promptStrategy"
        >
          <option value="full">
            {{ t('agents.form.promptStrategyFull') }}
          </option>
          <option value="lazy">
            {{ t('agents.form.promptStrategyLazy') }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.contextMode')"
        name="context_mode"
        :error="errors.context_mode"
      >
        <select
          id="context_mode"
          v-model="contextMode"
        >
          <option value="general">
            {{ t('agents.form.contextModeGeneral') }}
          </option>
          <option value="compact">
            {{ t('agents.form.contextModeCompact') }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.ragConfig')"
        name="rag_config_id"
        :error="errors.rag_config_id"
      >
        <select
          id="rag_config_id"
          v-model="ragConfigId"
        >
          <option :value="null">
            {{ t('agents.form.ragConfigNone') }}
          </option>
          <option
            v-for="rc in ragConfigsQuery.data.value ?? []"
            :key="rc.id"
            :value="rc.id"
          >
            {{ rc.name }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.a2aEnabled')"
        name="a2a_enabled"
        :error="errors.a2a_enabled"
      >
        <input
          id="a2a_enabled"
          v-model="a2aEnabled"
          type="checkbox"
        >
      </FormField>

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
  gap: var(--space-3);
  margin-top: var(--space-4);
}
</style>
