<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { ref } from 'vue'

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
const projectId = route.params.projectId as string

const showForm = ref(false)

const query = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => {
    const { data } = await agentsApi.list(projectId)
    return data
  },
})

const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm({
  validationSchema: schema,
  initialValues: {
    name: '',
    model_provider: 'openai',
    model_name: 'gpt-4o',
    system_prompt: '',
    temperature: 0.7,
    max_tokens: 4096,
    rag_config_id: null,
    mcp_server_ids: [],
  },
})

const [name] = defineField('name')
const [modelProvider] = defineField('model_provider')
const [modelName] = defineField('model_name')
const [systemPrompt] = defineField('system_prompt')
const [temperature] = defineField('temperature')
const [maxTokens] = defineField('max_tokens')

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (values: Parameters<typeof agentsApi.create>[1]) => {
    const { data } = await agentsApi.create(projectId, values)
    return data
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.agents(projectId) })
    resetForm()
    showForm.value = false
    ElMessage.success(t('agents.list.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) {
      ElMessage.error(t('agents.list.createFailed'))
    }
  },
})

const onSubmit = handleSubmit((values) => {
  createMutation.mutate(values as Parameters<typeof agentsApi.create>[1])
})

function goToAgent(agentId: string) {
  router.push({ name: 'agents.detail', params: { agentId } })
}
</script>

<template>
  <section class="agent-list p-6">
    <div class="agent-list__header">
      <h1 class="text-xl font-semibold mb-4">
        {{ t('agents.list.title') }}
      </h1>
      <button
        class="btn btn-primary"
        @click="showForm = !showForm"
      >
        {{ showForm ? t('agents.list.cancel') : t('agents.list.create') }}
      </button>
    </div>

    <form
      v-if="showForm"
      class="agent-list__form"
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
          :aria-describedby="errors.model_name ? 'model_name-error' : undefined"
          :aria-invalid="!!errors.model_name"
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
          rows="4"
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

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value"
      >
        {{ t('agents.form.submit') }}
      </button>
    </form>

    <div
      v-if="query.isLoading.value"
      class="agent-list__loading"
    >
      {{ t('agents.list.loading') }}
    </div>

    <ul
      v-else-if="query.data.value?.length"
      class="agent-list__items"
    >
      <li
        v-for="agent in query.data.value"
        :key="agent.id"
        class="agent-list__item"
        role="button"
        tabindex="0"
        @click="goToAgent(agent.id)"
        @keydown.enter="goToAgent(agent.id)"
      >
        <span class="agent-list__item-name">{{ agent.name }}</span>
        <span class="agent-list__item-model">{{ agent.model_provider }} / {{ agent.model_name }}</span>
      </li>
    </ul>

    <p
      v-else
      class="text-gray-500"
    >
      {{ t('agents.list.empty') }}
    </p>
  </section>
</template>

<style scoped>
.agent-list__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.agent-list__form {
  max-width: 480px;
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.agent-list__items {
  list-style: none;
  padding: 0;
}
.agent-list__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
  cursor: pointer;
  min-height: var(--touch-min);
}
.agent-list__item:hover {
  background: var(--color-surface);
}
.agent-list__item-name {
  font-weight: 500;
}
.agent-list__item-model {
  color: var(--color-muted);
  font-size: 0.875rem;
}
</style>
