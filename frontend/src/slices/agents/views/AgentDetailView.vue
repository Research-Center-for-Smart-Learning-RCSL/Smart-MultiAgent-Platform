<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { watch } from 'vue'

import { ElMessage } from 'element-plus'
import { FormField } from '@shared/ui'
import { useServerErrors } from '@shared/composables'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'
import { agentCreateSchema } from '../types/schemas'

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

const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm({
  validationSchema: schema,
})

const [name] = defineField('name')
const [modelProvider] = defineField('model_provider')
const [modelName] = defineField('model_name')
const [systemPrompt] = defineField('system_prompt')
const [temperature] = defineField('temperature')
const [maxTokens] = defineField('max_tokens')

const { applyServerErrors } = useServerErrors(setErrors)

watch(
  () => query.data.value,
  (agent) => {
    if (!agent) return
    resetForm({
      values: {
        name: agent.name,
        model_provider: agent.model_provider,
        model_name: agent.model_name,
        system_prompt: agent.system_prompt,
        temperature: agent.temperature,
        max_tokens: agent.max_tokens,
        rag_config_id: agent.rag_config_id,
        mcp_server_ids: agent.mcp_server_ids,
      },
    })
  },
  { immediate: true },
)

const patchMutation = useMutation({
  mutationFn: async (values: Record<string, unknown>) => {
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
        :label="t('agents.form.provider')"
        name="model_provider"
        :error="errors.model_provider"
        required
      >
        <select
          id="model_provider"
          v-model="modelProvider"
        >
          <option value="openai">
            OpenAI
          </option>
          <option value="claude">
            Claude
          </option>
          <option value="gemini">
            Gemini
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.form.model')"
        name="model_name"
        :error="errors.model_name"
        required
      >
        <input
          id="model_name"
          v-model="modelName"
        >
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
        :label="t('agents.form.temperature')"
        name="temperature"
        :error="errors.temperature"
      >
        <input
          id="temperature"
          v-model.number="temperature"
          type="number"
          step="0.1"
          min="0"
          max="2"
        >
      </FormField>

      <FormField
        :label="t('agents.form.maxTokens')"
        name="max_tokens"
        :error="errors.max_tokens"
      >
        <input
          id="max_tokens"
          v-model.number="maxTokens"
          type="number"
          min="1"
          max="128000"
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
          @click="deleteMutation.mutate()"
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
