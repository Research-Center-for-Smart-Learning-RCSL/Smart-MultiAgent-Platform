<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  Cog6ToothIcon,
  CommandLineIcon,
  BookOpenIcon,
  ServerIcon,
  ArrowsPointingOutIcon,
  TrashIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  SCard,
  SFormField,
  SInput,
  SSelect,
  SRadio,
  SToggle,
  SCodeEditor,
  SButton,
  SBadge,
  STable,
  SAlert,
  SEmptyState,
  SSkeleton,
  SModal,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { ApiError } from '@shared/errors'
import { keyGroupsApi, keysKeys, type KeyGroup } from '@slices/keys'
import { agentsApi, type McpBinding } from '../api'
import { agentKeys } from '../queries'
import { agentCreateSchema, type AgentCreateInput } from '../types/schemas'
import { useMcpTest } from '../composables/useMcpTest'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const { isMobile } = useBreakpoint()

const routeAgentId = route.params.agentId as string
const isCreateMode = routeAgentId === 'new'
const agentId = isCreateMode ? '' : routeAgentId
const createProjectId = (route.query.projectId as string) ?? ''

const activeTab = ref((route.query.tab as string) || 'general')
const conflictDetected = ref(false)

// --- Queries ---
const query = useQuery({
  queryKey: agentKeys.agent(agentId),
  enabled: !isCreateMode,
  queryFn: async () => (await agentsApi.get(agentId)).data,
})

const pickerProjectId = computed(() => {
  if (isCreateMode) return createProjectId
  return query.data.value?.project_id ?? ''
})

const keyGroupsQuery = useQuery({
  queryKey: computed(() => keysKeys.keyGroups(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => (await keyGroupsApi.listForProject(pickerProjectId.value)).data,
})

const ragConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.ragConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => (await agentsApi.listRagConfigs(pickerProjectId.value)).data,
})

const graphragConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.graphragConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: async () => (await agentsApi.listGraphragConfigs(pickerProjectId.value)).data,
})

const mcpBindingsQuery = useQuery({
  queryKey: computed(() => agentKeys.mcpBindings(agentId)),
  enabled: computed(() => !isCreateMode && !!agentId),
  queryFn: async () => (await agentsApi.listMcpBindings(agentId)).data,
})

const thisAgentGraphrag = computed(() =>
  (graphragConfigsQuery.data.value ?? []).find((c) => c.agent_id === agentId),
)

const mcpBindings = computed<McpBinding[]>(() => mcpBindingsQuery.data.value ?? [])

const keyGroups = computed<KeyGroup[]>(() => keyGroupsQuery.data.value ?? [])
const hasKeyGroups = computed(() => keyGroups.value.length > 0)

// --- Form setup ---
const schema = toTypedSchema(agentCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, meta } =
  useForm<AgentCreateInput>({
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
const [contextTokenCap] = defineField('context_token_cap')
const [ragConfigId] = defineField('rag_config_id')
const [graphragConfigId] = defineField('graphrag_config_id')
const [a2aEnabled] = defineField('a2a_enabled')

// Wakeup config decomposed fields
const wakeupEveryN = ref<number | null>(null)
const wakeupSilence = ref<number | null>(null)
const wakeupCallOnly = ref(false)
const wakeupAutostop = ref<number | null>(null)

// Workflow capabilities decomposed fields
const canInstruct = ref(false)
const canApprove = ref(false)
const canCreateSubagent = ref(false)
const maxAliveSubagents = ref(5)

// Populate form from loaded agent
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
        context_token_cap: agent.context_token_cap,
        rag_config_id: agent.rag_config_id,
        graphrag_config_id: agent.graphrag_config_id,
        a2a_enabled: agent.a2a_enabled,
      },
    })
    const wc = agent.wakeup_config as Record<string, number | boolean | null>
    wakeupEveryN.value = (wc.every_n_messages as number) ?? null
    wakeupSilence.value = (wc.silence_minutes as number) ?? null
    wakeupCallOnly.value = (wc.call_only as boolean) ?? false
    wakeupAutostop.value = (wc.autostop_rounds as number) ?? null

    const wf = agent.workflow_capabilities as Record<string, boolean | number>
    canInstruct.value = (wf.can_instruct as boolean) ?? false
    canApprove.value = (wf.can_approve as boolean) ?? false
    canCreateSubagent.value = (wf.can_create_subagent as boolean) ?? false
    maxAliveSubagents.value = (wf.max_alive_subagents as number) ?? 5
  },
  { immediate: true },
)

// Default key group for create mode
watch(
  () => keyGroupsQuery.data.value,
  (groups) => {
    if (isCreateMode && groups && groups.length && !keyGroupId.value) {
      keyGroupId.value = groups[0]!.id
    }
  },
  { immediate: true },
)

const { applyServerErrors } = useServerErrors(setErrors)

function assemblePayload(values: AgentCreateInput): AgentCreateInput {
  // Send every key explicitly (null/false for unset) so a partial-merge backend
  // can't retain a stale value the user just cleared. SInput type=number emits 0
  // when cleared, so `|| null` maps both 0 and null to "unset" for these min-1
  // fields (0 messages / 0 minutes is never a valid trigger).
  const wakeup_config: Record<string, unknown> = {
    every_n_messages: wakeupEveryN.value || null,
    silence_minutes: wakeupSilence.value || null,
    autostop_rounds: wakeupAutostop.value || null,
    call_only: wakeupCallOnly.value,
  }

  const workflow_capabilities: Record<string, unknown> = {
    can_instruct: canInstruct.value,
    can_approve: canApprove.value,
    can_create_subagent: canCreateSubagent.value,
    max_alive_subagents: canCreateSubagent.value ? maxAliveSubagents.value : null,
  }

  return { ...values, wakeup_config, workflow_capabilities }
}

// A token cap only applies in compact mode; clear it when leaving so a stale
// value (or a 0 left by clearing the input) can't ride along on save.
watch(contextMode, (mode) => {
  if (mode === 'general') contextTokenCap.value = null
})

const saveDisabled = computed(() => !isCreateMode && !meta.value.dirty)

// --- Create mutation ---
const createMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
    const { data } = await agentsApi.create(createProjectId, assemblePayload(values))
    return data
  },
  onSuccess: (agent) => {
    toast.success(t('agents.list.created'))
    router.replace({ name: 'agents.detail', params: { agentId: agent.id } })
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.list.createFailed'))
  },
})

// --- Patch mutation ---
const patchMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
    const agent = query.data.value
    if (!agent) throw new Error('Agent not loaded')
    const { data } = await agentsApi.patch(agentId, agent.version, assemblePayload(values))
    return data
  },
  onSuccess: () => {
    conflictDetected.value = false
    qc.invalidateQueries({ queryKey: agentKeys.agent(agentId) })
    toast.success(t('agents.detail.saved'))
  },
  onError: (err) => {
    if (err instanceof ApiError && err.status === 409) {
      conflictDetected.value = true
      return
    }
    if (!applyServerErrors(err)) toast.error(t('agents.detail.saveFailed'))
  },
})

const saving = computed(() => createMutation.isPending.value || patchMutation.isPending.value)

const fieldToTab: Record<string, string> = {
  name: 'general',
  model_hint: 'general',
  model_id: 'general',
  key_group_id: 'general',
  context_mode: 'general',
  context_token_cap: 'general',
  system_prompt: 'prompt',
  prompt_strategy: 'prompt',
  rag_config_id: 'knowledge',
  graphrag_config_id: 'knowledge',
  a2a_enabled: 'orchestration',
}

const tabOrder = ['general', 'prompt', 'knowledge', 'mcp', 'orchestration']

const onSubmit = handleSubmit(
  (values) => {
    if (isCreateMode) {
      createMutation.mutate(values)
    } else {
      patchMutation.mutate(values)
    }
  },
  ({ errors: fieldErrors }) => {
    const errorTabs = new Set(
      Object.keys(fieldErrors).map((f) => fieldToTab[f]).filter(Boolean),
    )
    const firstTab = tabOrder.find((tab) => errorTabs.has(tab))
    if (firstTab && firstTab !== activeTab.value) {
      onTabChange(firstTab)
    }
  },
)

function reloadAfterConflict(): void {
  conflictDetected.value = false
  qc.invalidateQueries({ queryKey: agentKeys.agent(agentId) })
}

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: () => agentsApi.remove(agentId, query.data.value!.version),
  onSuccess: () => {
    router.push({ name: 'agents.list', params: { projectId: pickerProjectId.value } })
  },
  onError: () => toast.error(t('agents.detail.deleteFailed')),
})

async function onDelete(): Promise<void> {
  const ok = await confirm({
    title: t('agents.detail.deleteConfirmTitle'),
    message: t('agents.detail.deleteConfirm'),
    variant: 'error',
    confirmLabel: t('agents.detail.delete'),
  })
  if (!ok) return
  deleteMutation.mutate()
}

// --- MCP test ---
const { testingIds, runTest, failedResult } = useMcpTest(agentId)

// --- Tab config ---
const tabs = computed(() => [
  { key: 'general', label: t('agents.detail.tabs.general'), icon: Cog6ToothIcon },
  { key: 'prompt', label: t('agents.detail.tabs.prompt'), icon: CommandLineIcon },
  { key: 'knowledge', label: t('agents.detail.tabs.knowledge'), icon: BookOpenIcon },
  { key: 'mcp', label: t('agents.detail.tabs.mcp'), icon: ServerIcon, badge: mcpBindings.value.length > 0 ? String(mcpBindings.value.length) : undefined },
  { key: 'orchestration', label: t('agents.detail.tabs.orchestration'), icon: ArrowsPointingOutIcon },
])

function onTabChange(tab: string): void {
  activeTab.value = tab
  router.replace({ query: { ...route.query, tab } })
}

// Prompt character counter
const promptLength = computed(() => (systemPrompt.value ?? '').length)
const promptCounterClass = computed(() => {
  if (promptLength.value >= 99000) return 'text-[var(--color-danger)]'
  if (promptLength.value >= 90000) return 'text-[var(--color-warning)]'
  return 'text-[var(--color-muted)]'
})

// Options
const modelHintOptions = computed(() => [
  { value: 'claude', label: t('agents.form.modelHints.claude') },
  { value: 'openai', label: t('agents.form.modelHints.openai') },
  { value: 'gemini', label: t('agents.form.modelHints.gemini') },
])

const keyGroupOptions = computed(() =>
  keyGroups.value.map((g) => ({ value: g.id, label: g.name })),
)

const promptStrategyOptions = computed(() => [
  { value: 'full', label: t('agents.form.strategies.full') },
  { value: 'lazy', label: t('agents.form.strategies.lazy') },
])

const ragConfigOptions = computed(() => [
  { value: '', label: t('agents.form.noRagConfig') },
  ...(ragConfigsQuery.data.value ?? []).map((c) => ({ value: c.id, label: c.name })),
])

const graphragConfigOptions = computed(() => {
  const options = [{ value: '', label: t('agents.form.noGraphragConfig') }]
  if (thisAgentGraphrag.value) {
    options.push({ value: thisAgentGraphrag.value.id, label: t('agents.form.graphragConfigThis') })
  }
  return options
})

const mcpColumns = computed<Column[]>(() => [
  { key: 'source', label: t('agents.mcp.colSource'), width: '80px' },
  { key: 'reference', label: t('agents.mcp.colReference') },
  { key: 'tools', label: t('agents.mcp.colTools'), width: '100px' },
  { key: 'actions', label: '', width: '80px', align: 'right' },
])

const pageTitle = computed(() => {
  if (isCreateMode) return t('agents.detail.new')
  return query.data.value?.name ?? t('agents.detail.title')
})

const breadcrumbs = computed(() => [
  { label: t('agents.breadcrumb.agents'), to: pickerProjectId.value ? { name: 'agents.list', params: { projectId: pickerProjectId.value } } : undefined },
  { label: pageTitle.value },
])

// --- GraphRAG status in Knowledge tab ---
const graphragStatusQuery = useQuery({
  queryKey: computed(() => agentKeys.graphragConfig(graphragConfigId.value ?? '')),
  enabled: computed(() => !!graphragConfigId.value && !isCreateMode),
  queryFn: async () => (await agentsApi.getGraphragStatus(graphragConfigId.value!)).data,
})

const graphragStatusText = computed(() => {
  const status = graphragStatusQuery.data.value
  if (!status) return ''
  const stateMap: Record<string, string> = {
    idle: t('agents.graphragList.states.idle'),
    running: t('agents.graphragList.states.running'),
    neo4j_committed: t('agents.graphragList.states.neo4jCommitted'),
    qdrant_committed: t('agents.graphragList.states.qdrantCommitted'),
    failed: t('agents.graphragList.states.failed'),
    failed_compensating: t('agents.graphragList.states.compensating'),
  }
  const state = stateMap[status.state] ?? status.state
  const lastBuilt = status.last_build_at ? new Date(status.last_build_at).toLocaleString() : '--'
  return t('agents.graphragList.graphragStatusInfo', { state, lastBuilt })
})
</script>

<template>
  <main class="p-6">
    <!-- Loading skeleton -->
    <template v-if="!isCreateMode && query.isLoading.value">
      <SSkeleton width="200px" />
      <div class="flex gap-2 mt-4">
        <SSkeleton
          v-for="i in 5"
          :key="i"
          variant="rect"
          width="80px"
          height="32px"
        />
      </div>
      <SSkeleton class="mt-6" />
      <SSkeleton class="mt-2" />
    </template>

    <template v-else>
      <SPageHeader
        :title="pageTitle"
        :breadcrumbs="breadcrumbs"
      >
        <template #actions>
          <SButton
            v-if="!isCreateMode"
            variant="danger"
            @click="onDelete"
          >
            <template #icon-left>
              <TrashIcon class="w-4 h-4" />
            </template>
            {{ t('agents.detail.delete') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="saving"
            :disabled="saveDisabled"
            @click="onSubmit"
          >
            {{ t('agents.detail.save') }}
          </SButton>
        </template>
      </SPageHeader>

      <SAlert
        v-if="!hasKeyGroups && !keyGroupsQuery.isLoading.value"
        variant="warning"
        class="mt-4"
      >
        {{ t('agents.form.noKeyGroups') }}
      </SAlert>

      <SAlert
        v-if="conflictDetected"
        variant="warning"
        class="mt-4"
      >
        {{ t('agents.detail.conflictAlert') }}
        <template #actions>
          <SButton
            variant="ghost"
            size="sm"
            @click="reloadAfterConflict"
          >
            {{ t('agents.detail.reload') }}
          </SButton>
        </template>
      </SAlert>

      <!-- Tabs - collapse to SSelect on mobile -->
      <div
        v-if="isMobile"
        class="mt-6"
      >
        <SSelect
          :model-value="activeTab"
          :options="tabs.map(tab => ({ value: tab.key, label: tab.label }))"
          @update:model-value="onTabChange"
        />
      </div>

      <STabs
        v-else
        :model-value="activeTab"
        :tabs="tabs"
        class="mt-6"
        @update:model-value="onTabChange"
      />

      <form @submit.prevent="onSubmit">
        <!-- Tab: General -->
        <div
          v-show="activeTab === 'general'"
          class="mt-6 space-y-6"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.detail.tabs.general') }}
            </h3>
            <SFormField
              :label="t('agents.form.name')"
              name="name"
              :error="errors.name"
              required
            >
              <SInput
                v-model="name"
                :error="!!errors.name"
              />
            </SFormField>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
              <SFormField
                :label="t('agents.form.modelHint')"
                name="model_hint"
                :error="errors.model_hint"
                required
              >
                <SSelect
                  v-model="modelHint"
                  :options="modelHintOptions"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.modelId')"
                name="model_id"
                :error="errors.model_id"
                :help="t('agents.form.modelIdHelp')"
              >
                <SInput
                  v-model="modelId"
                  :placeholder="t('agents.form.modelIdPlaceholder')"
                />
              </SFormField>
            </div>

            <SFormField
              :label="t('agents.form.keyGroup')"
              name="key_group_id"
              :error="errors.key_group_id"
              required
              class="mt-4"
            >
              <SSelect
                v-model="keyGroupId"
                :options="keyGroupOptions"
                :placeholder="t('agents.form.keyGroupPlaceholder')"
              />
            </SFormField>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.contextMode') }}
            </h3>
            <div class="flex gap-6">
              <SRadio
                v-model="contextMode"
                value="general"
                name="context_mode"
              >
                {{ t('agents.form.contextModeGeneral') }}
              </SRadio>
              <SRadio
                v-model="contextMode"
                value="compact"
                name="context_mode"
              >
                {{ t('agents.form.contextModeCompact') }}
              </SRadio>
            </div>
            <SFormField
              v-if="contextMode === 'compact'"
              :label="t('agents.form.contextTokenCap')"
              name="context_token_cap"
              :error="errors.context_token_cap"
              :help="t('agents.form.contextTokenCapHelp')"
              class="mt-4"
            >
              <SInput
                v-model="contextTokenCap"
                type="number"
              />
            </SFormField>
          </SCard>
        </div>

        <!-- Tab: Prompt -->
        <div
          v-show="activeTab === 'prompt'"
          class="mt-6 space-y-6"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.systemPrompt') }}
            </h3>
            <SFormField
              :label="t('agents.form.promptStrategy')"
              name="prompt_strategy"
              :error="errors.prompt_strategy"
            >
              <SSelect
                v-model="promptStrategy"
                :options="promptStrategyOptions"
              />
            </SFormField>

            <SFormField
              :label="t('agents.form.systemPrompt')"
              name="system_prompt"
              :error="errors.system_prompt"
              class="mt-4"
            >
              <SCodeEditor
                v-model="systemPrompt"
                language="markdown"
                :rows="16"
                :placeholder="t('agents.form.systemPromptPlaceholder')"
              />
            </SFormField>
            <p
              class="text-xs mt-1"
              :class="promptCounterClass"
            >
              {{ `${promptLength.toLocaleString()} / 100,000` }}
            </p>
          </SCard>
        </div>

        <!-- Tab: Knowledge -->
        <div
          v-show="activeTab === 'knowledge'"
          class="mt-6 space-y-6"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.ragConfig') }}
            </h3>
            <SFormField
              :label="t('agents.form.ragConfig')"
              name="rag_config_id"
              :error="errors.rag_config_id"
              :help="t('agents.form.manageRagConfigs')"
            >
              <SSelect
                v-model="ragConfigId"
                :options="ragConfigOptions"
              />
            </SFormField>
            <SButton
              v-if="ragConfigId && pickerProjectId"
              variant="link"
              class="mt-2"
              :to="{
                name: 'agents.ragConfig',
                params: { projectId: pickerProjectId, configId: ragConfigId },
              }"
              as="router-link"
            >
              {{ t('agents.rag.manageLink') }}
            </SButton>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.graphragConfig') }}
            </h3>
            <SFormField
              :label="t('agents.form.graphragConfig')"
              name="graphrag_config_id"
              :error="errors.graphrag_config_id"
            >
              <SSelect
                v-model="graphragConfigId"
                :options="graphragConfigOptions"
              />
            </SFormField>
            <SAlert
              v-if="graphragConfigId && graphragStatusQuery.data.value"
              variant="info"
              class="mt-3"
            >
              {{ graphragStatusText }}
            </SAlert>
            <SButton
              v-if="pickerProjectId"
              variant="link"
              class="mt-2"
              :to="{ name: 'agents.graphragConfigs', params: { projectId: pickerProjectId } }"
              as="router-link"
            >
              {{ t('agents.form.manageGraphragConfigs') }}
            </SButton>
          </SCard>
        </div>

        <!-- Tab: MCP -->
        <div
          v-show="activeTab === 'mcp'"
          class="mt-6"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.detail.tabs.mcp') }}
            </h3>

            <STable
              v-if="mcpBindings.length > 0"
              :columns="mcpColumns"
              :data="mcpBindings"
              row-key="id"
            >
              <template #cell-source="{ row }">
                <SBadge variant="neutral">
                  {{ t(`agents.mcp.sources.${row.source}`) }}
                </SBadge>
              </template>

              <template #cell-reference="{ row }">
                <span class="font-mono text-sm break-all">{{ row.reference }}</span>
              </template>

              <template #cell-tools="{ row }">
                {{ row.allowed_tools.length ? t('agents.mcp.nAllowed', { n: row.allowed_tools.length }) : t('agents.mcp.allTools') }}
              </template>

              <template #actions="{ row }">
                <SButton
                  variant="ghost"
                  size="sm"
                  :loading="testingIds.has(row.id)"
                  @click="runTest(row.id)"
                >
                  {{ t('agents.mcp.test') }}
                </SButton>
              </template>
            </STable>

            <SEmptyState
              v-else-if="!isCreateMode"
              :icon="ServerIcon"
              :title="t('agents.mcp.emptyTitle')"
              :text="t('agents.mcp.emptyDescription')"
            >
              <template #action>
                <SButton
                  variant="link"
                  :to="{ name: 'agents.mcp', params: { agentId } }"
                  as="router-link"
                >
                  {{ t('agents.mcp.add') }}
                </SButton>
              </template>
            </SEmptyState>

            <SButton
              v-if="!isCreateMode && mcpBindings.length > 0"
              variant="link"
              class="mt-4"
              :to="{ name: 'agents.mcp', params: { agentId } }"
              as="router-link"
            >
              {{ t('agents.form.manageMcp') }}
            </SButton>
          </SCard>
        </div>

        <!-- Tab: Orchestration -->
        <div
          v-show="activeTab === 'orchestration'"
          class="mt-6 space-y-6"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.a2aEnabled') }}
            </h3>
            <SFormField
              :label="t('agents.form.a2aEnabled')"
              name="a2a_enabled"
              :help="t('agents.form.a2aHelp')"
            >
              <SToggle v-model="a2aEnabled" />
            </SFormField>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.wakeupHelp') }}
            </h3>
            <div class="space-y-4">
              <SFormField
                :label="t('agents.form.wakeupEveryN')"
                name="wakeup_every_n"
              >
                <SInput
                  v-model="wakeupEveryN"
                  type="number"
                  :disabled="wakeupCallOnly"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.wakeupSilence')"
                name="wakeup_silence"
              >
                <SInput
                  v-model="wakeupSilence"
                  type="number"
                  :disabled="wakeupCallOnly"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.wakeupCallOnly')"
                name="wakeup_call_only"
              >
                <SToggle v-model="wakeupCallOnly" />
              </SFormField>
              <SFormField
                :label="t('agents.form.wakeupAutostop')"
                name="wakeup_autostop"
              >
                <SInput
                  v-model="wakeupAutostop"
                  type="number"
                />
              </SFormField>
            </div>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.orchestration') }}
            </h3>
            <div class="space-y-4">
              <SFormField
                :label="t('agents.form.canInstruct')"
                name="can_instruct"
              >
                <SToggle v-model="canInstruct" />
              </SFormField>
              <SFormField
                :label="t('agents.form.canApprove')"
                name="can_approve"
              >
                <SToggle v-model="canApprove" />
              </SFormField>
              <SFormField
                :label="t('agents.form.canCreateSubagent')"
                name="can_create_subagent"
              >
                <SToggle v-model="canCreateSubagent" />
              </SFormField>
              <SFormField
                v-if="canCreateSubagent"
                :label="t('agents.form.maxAliveSubagents')"
                name="max_alive_subagents"
              >
                <SInput
                  v-model="maxAliveSubagents"
                  type="number"
                />
              </SFormField>
            </div>
          </SCard>
        </div>
      </form>

      <!-- Fixed bottom bar on mobile -->
      <div
        v-if="isMobile"
        class="fixed bottom-0 left-0 right-0 p-4 bg-[var(--color-bg)] border-t border-[var(--color-border)] flex gap-3 z-10"
      >
        <SButton
          v-if="!isCreateMode"
          variant="danger"
          class="flex-1"
          @click="onDelete"
        >
          {{ t('agents.detail.delete') }}
        </SButton>
        <SButton
          variant="primary"
          class="flex-1"
          :loading="saving"
          :disabled="saveDisabled"
          @click="onSubmit"
        >
          {{ t('agents.detail.save') }}
        </SButton>
      </div>
    </template>

    <!-- MCP test failure modal -->
    <SModal
      :open="!!failedResult"
      :title="t('agents.mcp.testErrorTitle')"
      size="md"
      @close="failedResult = null"
    >
      <SCodeEditor
        v-if="failedResult"
        :model-value="failedResult.error"
        language="text"
        :rows="6"
        readonly
      />
      <p
        v-if="failedResult"
        class="text-sm text-[var(--color-text-muted)] mt-2"
      >
        {{ t('agents.mcp.testErrorDuration', { ms: failedResult.duration_ms }) }}
      </p>

      <template #footer>
        <div class="flex justify-end">
          <SButton
            variant="secondary"
            @click="failedResult = null"
          >
            {{ t('app.close') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
