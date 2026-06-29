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
  SAlert,
  SSkeleton,
  SCharCount,
} from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { ApiError } from '@shared/errors'
import { keyGroupsApi, keysKeys, type KeyGroup } from '@slices/keys'
import { agentsApi, type AgentTool, type AgentToolType } from '../api'
import { agentKeys } from '../queries'
import { agentCreateSchema, type AgentCreateInput } from '../types/schemas'

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

const toolsQuery = useQuery({
  queryKey: computed(() => agentKeys.tools(agentId)),
  enabled: computed(() => !isCreateMode && !!agentId),
  queryFn: async () => (await agentsApi.listTools(agentId)).data,
})

const modelCatalogQuery = useQuery({
  queryKey: agentKeys.modelCatalog(),
  queryFn: async () => (await agentsApi.getModelCatalog()).data,
})

const thisAgentGraphrag = computed(() =>
  (graphragConfigsQuery.data.value ?? []).find((c) => c.agent_id === agentId),
)

const allTools = computed<AgentTool[]>(() => toolsQuery.data.value ?? [])

const singletonEnabled = (type: AgentToolType): boolean =>
  allTools.value.find((tool) => tool.tool_type === type)?.enabled ?? false

const toolTypeCount = (type: AgentToolType): number =>
  allTools.value.filter((tool) => tool.tool_type === type).length

const hostedToolRows = computed(() => [
  {
    key: 'webSearch',
    label: t('agents.tools.webSearch.label'),
    kind: 'toggle' as const,
    enabled: singletonEnabled('hosted_web_search'),
  },
  {
    key: 'codeInterpreter',
    label: t('agents.tools.codeInterpreter.label'),
    kind: 'toggle' as const,
    enabled: singletonEnabled('hosted_code_interpreter'),
  },
  {
    key: 'fileWorkspace',
    label: t('agents.tools.fileWorkspace.label'),
    kind: 'toggle' as const,
    enabled: singletonEnabled('hosted_file_workspace'),
  },
  {
    key: 'fileSearch',
    label: t('agents.tools.fileSearch.label'),
    kind: 'toggle' as const,
    enabled: singletonEnabled('hosted_file_search'),
  },
  {
    key: 'mcp',
    label: t('agents.tools.mcp.label'),
    kind: 'count' as const,
    count: toolTypeCount('hosted_mcp'),
  },
])

const localToolRows = computed(() => [
  {
    key: 'functions',
    label: t('agents.tools.functions.label'),
    kind: 'count' as const,
    count: toolTypeCount('local_function'),
  },
  {
    key: 'localShell',
    label: t('agents.tools.localShell.label'),
    kind: 'soon' as const,
  },
])

const enabledToolCount = computed(() => {
  const singletons = (
    [
      'hosted_web_search',
      'hosted_code_interpreter',
      'hosted_file_workspace',
      'hosted_file_search',
    ] as AgentToolType[]
  ).filter((type) => singletonEnabled(type)).length
  return (
    singletons +
    allTools.value.filter(
      (tool) =>
        (tool.tool_type === 'hosted_mcp' || tool.tool_type === 'local_function') &&
        tool.enabled,
    ).length
  )
})

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
      effort: null,
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
const [effort] = defineField('effort')
const [keyGroupId] = defineField('key_group_id')
const [systemPrompt] = defineField('system_prompt')
const [promptStrategy] = defineField('prompt_strategy')
const [contextMode] = defineField('context_mode')
const [contextTokenCap] = defineField('context_token_cap')
const [ragConfigId] = defineField('rag_config_id')
const [graphragConfigId] = defineField('graphrag_config_id')
const [a2aEnabled] = defineField('a2a_enabled')

// Model-id combobox: per-provider preset list + "provider default" (stores null)
// + "custom" (free-text). `customModel` captures the intent to type a custom id
// while the field is empty — an empty model_id otherwise reads as "default".
const CUSTOM_MODEL = '__custom__'
const customModel = ref(false)
const chatModelsForHint = computed(
  () =>
    modelCatalogQuery.data.value?.chat.find((c) => c.provider === modelHint.value)?.models ?? [],
)
// The model the runtime uses when model_id is left unset, surfaced in the
// "provider default" option label so the user sees which model that resolves to.
const defaultModelForHint = computed(
  () =>
    modelCatalogQuery.data.value?.chat.find((c) => c.provider === modelHint.value)?.default ?? '',
)
const isCustomModel = computed(
  () =>
    customModel.value ||
    // Only treat a saved model_id as "custom" once the catalog has loaded;
    // while the catalog is in-flight chatModelsForHint = [] and every preset
    // model_id would incorrectly appear custom, causing a flicker.
    (!!modelCatalogQuery.data.value && !!modelId.value && !chatModelsForHint.value.includes(modelId.value)),
)
const modelSelectValue = computed<string>({
  get: () => (isCustomModel.value ? CUSTOM_MODEL : (modelId.value ?? '')),
  set: (v) => {
    const s = String(v)
    if (s === CUSTOM_MODEL) {
      customModel.value = true
    } else {
      customModel.value = false
      modelId.value = s === '' ? null : s
    }
  },
})
const customModelId = computed<string>({
  get: () => modelId.value ?? '',
  set: (v) => {
    modelId.value = v.trim() === '' ? null : v
  },
})
const modelIdOptions = computed(() => [
  {
    value: '',
    label: defaultModelForHint.value
      ? t('agents.form.modelDefaultNamed', { model: defaultModelForHint.value })
      : t('agents.form.modelDefault'),
  },
  ...chatModelsForHint.value.map((m) => ({ value: m, label: m })),
  { value: CUSTOM_MODEL, label: t('agents.form.modelCustom') },
])

// Reasoning effort: empty = provider default (stored as null via schema preprocess).
const effortOptions = computed(() => [
  { value: '', label: t('agents.form.effortDefault') },
  { value: 'low', label: t('agents.form.effortLevels.low') },
  { value: 'medium', label: t('agents.form.effortLevels.medium') },
  { value: 'high', label: t('agents.form.effortLevels.high') },
])

// Wakeup config decomposed fields. New agents default to replying to every
// message (every_n=1): without an enabled trigger an agent is inert and never
// responds, which is a surprising default for a chat agent. Edit mode loads the
// agent's real config in the query watcher below.
const wakeupEveryN = ref<number | null>(isCreateMode ? 1 : null)
const wakeupSilence = ref<number | null>(null)
const wakeupCallOnly = ref(false)
const wakeupAutostop = ref<number | null>(null)

// Mirrors the backend WakeupConfig.is_inert() check: with no trigger enabled the
// agent never auto-responds (it can still be summoned by @mention in a room).
const wakeupInert = computed(
  () => !wakeupEveryN.value && !wakeupSilence.value && !wakeupCallOnly.value,
)

// Workflow capabilities decomposed fields
const canInstruct = ref(false)
const canApprove = ref(false)
const canCreateSubagent = ref(false)
const maxAliveSubagents = ref(5)

// Wakeup + workflow-capability fields live outside the vee-validate form, so
// `meta.dirty` never flips when only these change. Snapshot them at load time
// and compare to drive the Save button (otherwise editing only triggers /
// orchestration leaves Save permanently disabled).
function extrasSnapshot(): string {
  return JSON.stringify([
    wakeupEveryN.value,
    wakeupSilence.value,
    wakeupCallOnly.value,
    wakeupAutostop.value,
    canInstruct.value,
    canApprove.value,
    canCreateSubagent.value,
    maxAliveSubagents.value,
  ])
}
const extrasBaseline = ref(extrasSnapshot())
const extrasDirty = computed(() => extrasSnapshot() !== extrasBaseline.value)

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
        effort: (agent.effort ?? null) as AgentCreateInput['effort'],
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
    const wc = agent.wakeup_config as Record<string, unknown>
    if (wc?.triggers) {
      const triggers = wc.triggers as Record<string, Record<string, unknown>>
      const enm = triggers.every_n_messages ?? {}
      const sm = triggers.silence_minutes ?? {}
      const co = triggers.call_only ?? {}
      wakeupEveryN.value = enm.enabled ? (enm.n as number) ?? null : null
      wakeupSilence.value = sm.enabled ? (sm.t_minutes as number) ?? null : null
      wakeupCallOnly.value = (co.enabled as boolean) ?? false
      wakeupAutostop.value = (sm.autostop_rounds as number) ?? null
    } else {
      // Legacy flat format saved before the nested triggers schema
      wakeupEveryN.value = (wc?.every_n_messages as number) || null
      wakeupSilence.value = (wc?.silence_minutes as number) || null
      wakeupCallOnly.value = (wc?.call_only as boolean) ?? false
      wakeupAutostop.value = (wc?.autostop_rounds as number) || null
    }

    const wf = agent.workflow_capabilities as Record<string, boolean | number>
    canInstruct.value = (wf.can_instruct as boolean) ?? false
    canApprove.value = (wf.can_approve as boolean) ?? false
    canCreateSubagent.value = (wf.can_create_subagent as boolean) ?? false
    maxAliveSubagents.value = (wf.max_alive_subagents as number) ?? 5

    // Re-baseline so the freshly loaded values don't read as dirty. A
    // successful patch invalidates the query, which re-fires this watcher and
    // clears extrasDirty the same way meta.dirty resets via resetForm.
    extrasBaseline.value = extrasSnapshot()
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
  // Call-only is mutually exclusive with the autonomous triggers (the inputs
  // are disabled in the UI), so never ship their `enabled` flags alongside it —
  // otherwise the saved config is self-contradictory (the backend would ignore
  // every_n/silence, but the stored data reads as if both were active).
  const callOnly = wakeupCallOnly.value
  const wakeup_config: Record<string, unknown> = {
    triggers: {
      every_n_messages: {
        enabled: !callOnly && !!wakeupEveryN.value,
        n: wakeupEveryN.value || 3,
      },
      silence_minutes: {
        enabled: !callOnly && !!wakeupSilence.value,
        t_minutes: wakeupSilence.value || 2,
        autostop_rounds: wakeupAutostop.value || 5,
      },
      call_only: {
        enabled: callOnly,
      },
    },
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

// When the user changes provider, the previous model_id is invalid for the new
// provider — clear it so a stale cross-provider ID never reaches the save payload.
watch(modelHint, () => {
  modelId.value = null
  customModel.value = false
})

const contextTokenCapPlaceholder = computed(() => {
  const contextLimit =
    modelCatalogQuery.data.value?.chat.find((c) => c.provider === modelHint.value)?.context_limit ??
    128_000
  const defaultCap = Math.floor(contextLimit * 0.75)
  return t('agents.form.contextTokenCapDefault', { cap: defaultCap.toLocaleString() })
})

function insertLazyTemplate(): void {
  const existing = (systemPrompt.value ?? '').trimEnd()
  if (!existing) {
    systemPrompt.value = t('agents.form.promptStrategyLazyTemplate')
  } else {
    systemPrompt.value = existing + t('agents.form.promptStrategyLazySectionAppend')
  }
}

const saveDisabled = computed(
  () => !isCreateMode && !meta.value.dirty && !extrasDirty.value,
)

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

const tabOrder = ['general', 'prompt', 'knowledge', 'tools', 'orchestration']

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

// --- Tab config ---
const tabs = computed(() => [
  { key: 'general', label: t('agents.detail.tabs.general'), icon: Cog6ToothIcon },
  { key: 'prompt', label: t('agents.detail.tabs.prompt'), icon: CommandLineIcon },
  { key: 'knowledge', label: t('agents.detail.tabs.knowledge'), icon: BookOpenIcon },
  { key: 'tools', label: t('agents.tools.tabLabel'), icon: ServerIcon, badge: enabledToolCount.value > 0 ? String(enabledToolCount.value) : undefined },
  { key: 'orchestration', label: t('agents.detail.tabs.orchestration'), icon: ArrowsPointingOutIcon },
])

function onTabChange(tab: string): void {
  activeTab.value = tab
  router.replace({ query: { ...route.query, tab } })
}

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
                <SSelect
                  v-model="modelSelectValue"
                  :options="modelIdOptions"
                />
                <SInput
                  v-if="isCustomModel"
                  v-model="customModelId"
                  :maxlength="INPUT_LIMITS.MODEL_ID"
                  :placeholder="t('agents.form.modelIdPlaceholder')"
                  :error="!!errors.model_id"
                  class="mt-2"
                />
              </SFormField>
            </div>

            <SFormField
              :label="t('agents.form.effort')"
              name="effort"
              :error="errors.effort"
              :help="t('agents.form.effortHelp')"
              class="mt-4"
            >
              <SSelect
                v-model="effort"
                :options="effortOptions"
              />
            </SFormField>

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
                :placeholder="contextTokenCapPlaceholder"
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
              :help="promptStrategy === 'full' ? t('agents.form.promptStrategyFullHelp') : t('agents.form.promptStrategyLazyHelp')"
            >
              <SSelect
                v-model="promptStrategy"
                :options="promptStrategyOptions"
              />
            </SFormField>

            <SAlert
              v-if="promptStrategy === 'lazy'"
              variant="info"
              :title="t('agents.form.promptStrategyLazyCalloutTitle')"
              class="mt-3"
            >
              {{ t('agents.form.promptStrategyLazyCallout') }}
              <pre class="mt-2 text-xs font-mono rounded px-3 py-2 overflow-x-auto whitespace-pre opacity-80">{{ t('agents.form.promptStrategyLazyFormatExample') }}</pre>
              <template #actions>
                <SButton
                  type="button"
                  variant="ghost"
                  size="sm"
                  @click="insertLazyTemplate"
                >
                  {{ t('agents.form.promptStrategyLazyInsert') }}
                </SButton>
              </template>
            </SAlert>

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
            <SCharCount
              :current="(systemPrompt ?? '').length"
              :max="INPUT_LIMITS.SYSTEM_PROMPT"
            />
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
          v-show="activeTab === 'tools'"
          class="mt-6"
        >
          <SCard>
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-lg font-semibold">
                {{ t('agents.tools.tabLabel') }}
              </h3>
              <SButton
                v-if="!isCreateMode"
                variant="link"
                :to="{ name: 'agents.tools', params: { agentId } }"
                as="router-link"
              >
                {{ t('agents.detail.toolsOverview.manage') }}
              </SButton>
            </div>

            <p
              v-if="isCreateMode"
              class="text-sm text-muted"
            >
              {{ t('agents.detail.toolsOverview.createHint') }}
            </p>

            <template v-else>
              <h4 class="text-sm font-semibold text-muted mb-1">
                {{ t('agents.tools.hosted.title') }}
              </h4>
              <ul class="divide-y divide-border">
                <li
                  v-for="row in hostedToolRows"
                  :key="row.key"
                  class="flex items-center justify-between py-2"
                >
                  <span class="text-sm">{{ row.label }}</span>
                  <SBadge
                    v-if="row.kind === 'toggle'"
                    :variant="row.enabled ? 'success' : 'neutral'"
                  >
                    {{ row.enabled ? t('agents.detail.toolsOverview.on') : t('agents.detail.toolsOverview.off') }}
                  </SBadge>
                  <SBadge
                    v-else
                    :variant="row.count > 0 ? 'info' : 'neutral'"
                  >
                    {{ row.count > 0 ? row.count : t('agents.detail.toolsOverview.none') }}
                  </SBadge>
                </li>
              </ul>

              <h4 class="text-sm font-semibold text-muted mb-1 mt-6">
                {{ t('agents.tools.local.title') }}
              </h4>
              <ul class="divide-y divide-border">
                <li
                  v-for="row in localToolRows"
                  :key="row.key"
                  class="flex items-center justify-between py-2"
                >
                  <span class="text-sm">{{ row.label }}</span>
                  <SBadge
                    v-if="row.kind === 'soon'"
                    variant="neutral"
                  >
                    {{ t('agents.detail.toolsOverview.comingSoon') }}
                  </SBadge>
                  <SBadge
                    v-else
                    :variant="row.count > 0 ? 'info' : 'neutral'"
                  >
                    {{ row.count > 0 ? row.count : t('agents.detail.toolsOverview.none') }}
                  </SBadge>
                </li>
              </ul>
            </template>
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
              <SToggle
                v-model="a2aEnabled"
                variant="robot"
              />
            </SFormField>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.form.wakeupHelp') }}
            </h3>
            <SAlert
              v-if="wakeupInert"
              variant="warning"
              class="mb-4"
            >
              {{ t('agents.form.wakeupInertWarning') }}
            </SAlert>
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

          <SCard v-if="!isCreateMode">
            <h3 class="text-lg font-semibold mb-1">
              {{ t('agents.detail.openOrchestration') }}
            </h3>
            <p class="text-sm text-muted mb-4">
              {{ t('agents.detail.openOrchestrationHelp') }}
            </p>
            <SButton
              variant="secondary"
              :to="{ name: 'workflow.agentOrchestration', params: { agentId } }"
              as="router-link"
            >
              <template #icon-left>
                <ArrowsPointingOutIcon class="w-4 h-4" />
              </template>
              {{ t('agents.detail.openOrchestration') }}
            </SButton>
          </SCard>
        </div>
      </form>

      <!-- Fixed bottom bar on mobile -->
      <div
        v-if="isMobile"
        class="fixed bottom-0 left-0 right-0 p-4 bg-bg border-t border-border flex gap-3 z-10"
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
  </main>
</template>
