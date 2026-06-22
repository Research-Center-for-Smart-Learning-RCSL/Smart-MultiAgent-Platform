<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { computed, ref, watch } from 'vue'

import { useServerErrors, useToast } from '@shared/composables'
import { SCard, SPageHeader } from '@shared/ui'
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
  queryKey: keysKeys.keyGroups(projectId),
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
    model_id: null,
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
const [modelId] = defineField('model_id')
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
    toast.success(t('agents.list.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) {
      toast.error(t('agents.list.createFailed'))
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
  <section class="agent-list px-4 py-4 sm:p-6">
    <SPageHeader :title="t('agents.list.title')">
      <button
        class="btn btn-primary"
        @click="showForm = !showForm"
      >
        {{ showForm ? t('agents.list.cancel') : t('agents.list.create') }}
      </button>
    </SPageHeader>

    <SCard
      v-if="showForm"
      class="max-w-[480px] mb-6"
    >
    <form
      @submit.prevent="onSubmit"
    >
      <p
        v-if="!keyGroupsQuery.isLoading.value && !hasKeyGroups"
        class="agent-list__warning"
        role="alert"
      >
        {{ t('agents.form.noKeyGroups') }}
      </p>

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
      >
        <template #after-rag>
          <RouterLink
            class="agent-list__rag-manage"
            :to="{ name: 'agents.ragConfigs', params: { projectId } }"
          >
            {{ t('agents.form.manageRagConfigs') }}
          </RouterLink>
          <RouterLink
            class="agent-list__rag-manage"
            :to="{ name: 'agents.graphragConfigs', params: { projectId } }"
          >
            {{ t('agents.form.manageGraphragConfigs') }}
          </RouterLink>
        </template>
      </AgentFormFields>

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value || !hasKeyGroups"
      >
        {{ t('agents.form.submit') }}
      </button>
    </form>
    </SCard>

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
        <span class="agent-list__item-model">{{ agent.model_id ?? agent.model_hint }}</span>
      </li>
    </ul>

    <p
      v-else
      class="text-muted"
    >
      {{ t('agents.list.empty') }}
    </p>
  </section>
</template>

<style scoped>
.agent-list__warning {
  color: var(--color-danger);
  margin-bottom: 0.75rem;
}
.agent-list__items {
  list-style: none;
  padding: 0;
}
.agent-list__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 0.5rem;
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
