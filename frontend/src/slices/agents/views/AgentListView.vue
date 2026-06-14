<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { computed, ref, watch } from 'vue'

import { ElMessage } from 'element-plus'
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
const projectId = route.params.projectId as string

const showForm = ref(false)

const query = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => {
    const { data } = await agentsApi.list(projectId)
    return data
  },
})

// Key groups are the BYO-key routing target — an agent cannot be created
// without one, so the picker is required and we block submit when empty.
const keyGroupsQuery = useQuery({
  queryKey: ['keys', 'keyGroups', projectId],
  queryFn: async () => {
    const { data } = await keyGroupsApi.listForProject(projectId)
    return data
  },
})

const ragConfigsQuery = useQuery({
  queryKey: agentKeys.ragConfigs(projectId),
  queryFn: async () => {
    const { data } = await agentsApi.listRagConfigs(projectId)
    return data
  },
})

const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)

const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<AgentCreateInput>({
  validationSchema: schema,
  initialValues: {
    name: '',
    model_hint: 'claude',
    key_group_id: '',
    system_prompt: '',
    prompt_strategy: 'full',
    rag_config_id: null,
    graphrag_config_id: null,
    context_mode: 'general',
    context_token_cap: null,
    a2a_enabled: false,
  },
})

const [name] = defineField('name')
const [modelHint] = defineField('model_hint')
const [keyGroupId] = defineField('key_group_id')
const [systemPrompt] = defineField('system_prompt')
const [promptStrategy] = defineField('prompt_strategy')
const [contextMode] = defineField('context_mode')
const [ragConfigId] = defineField('rag_config_id')
const [a2aEnabled] = defineField('a2a_enabled')

// Default the required picker to the first available group once loaded so the
// common single-group case needs no manual selection.
watch(
  () => keyGroupsQuery.data.value,
  (groups) => {
    if (groups && groups.length && !keyGroupId.value) {
      keyGroupId.value = groups[0]!.id
    }
  },
  { immediate: true },
)

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
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
  createMutation.mutate(values)
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
      <p
        v-if="!keyGroupsQuery.isLoading.value && !hasKeyGroups"
        class="agent-list__warning"
        role="alert"
      >
        {{ t('agents.form.noKeyGroups') }}
      </p>

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
          rows="4"
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
        <RouterLink
          class="agent-list__rag-manage"
          :to="{ name: 'agents.ragConfigs', params: { projectId } }"
        >
          {{ t('agents.form.manageRagConfigs') }}
        </RouterLink>
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

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value || !hasKeyGroups"
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
        <span class="agent-list__item-model">{{ agent.model_hint }}</span>
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
.agent-list__warning {
  color: var(--color-danger, #b91c1c);
  margin-bottom: var(--space-3);
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
