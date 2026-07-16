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
  SWakeupEditor,
  SLoadingSpinner,
} from '@shared/ui'
import {
  defaultWakeupConfig,
  normalizeWakeupConfig,
  type WakeupConfig,
} from '@shared/types/workflow'
import { deepCloneJSON } from '@shared/utils/deepClone'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { ApiError } from '@shared/errors'
import { keyGroupsApi, keysKeys, type KeyGroup } from '@slices/keys'
import { PromptAssistantPanel, PromptTemplatePicker } from '@slices/prompt-studio'
import { agentsApi, type AgentTool, type AgentToolType, type ConceptMapOwnerKind } from '../api'
import { agentKeys } from '../queries'
import { useModelCatalog } from '@shared/composables'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'
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
  queryFn: () => agentsApi.get(agentId),
})

const pickerProjectId = computed(() => {
  if (isCreateMode) return createProjectId
  return query.data.value?.project_id ?? ''
})

const keyGroupsQuery = useQuery({
  queryKey: computed(() => keysKeys.keyGroups(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: () => keyGroupsApi.listForProject(pickerProjectId.value),
})

const ragConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.ragConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: () => agentsApi.listRagConfigs(pickerProjectId.value),
})

const knowmapConfigsQuery = useQuery({
  queryKey: computed(() => agentKeys.knowmapConfigs(pickerProjectId.value)),
  enabled: computed(() => !!pickerProjectId.value),
  queryFn: () => agentsApi.listKnowmapConfigs(pickerProjectId.value),
})

const toolsQuery = useQuery({
  queryKey: computed(() => agentKeys.tools(agentId)),
  enabled: computed(() => !isCreateMode && !!agentId),
  queryFn: () => agentsApi.listTools(agentId),
})

// WS5 (R11.09) — read-only Concept Map coverage for the Knowledge tab: the maps
// of this agent's rooms, its member groups, and those rooms' workspaces. Purely
// informational (transparency, not an attach).
const coverageQuery = useQuery({
  queryKey: computed(() => agentKeys.conceptMapCoverage(agentId)),
  enabled: computed(() => !isCreateMode && !!agentId),
  queryFn: () => agentsApi.getAgentConceptMapCoverage(agentId),
})
const coverageEntries = computed(() => coverageQuery.data.value?.entries ?? [])
const ownerKindVariant = (kind: ConceptMapOwnerKind): 'info' | 'success' | 'neutral' =>
  ({ chatroom: 'info', agent_group: 'success', workspace: 'neutral' } as const)[kind]

const modelCatalogQuery = useModelCatalog()

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
      rag_config_id: null,
      knowmap_config_id: null,
      context_mode: 'general',
      context_token_cap: null,
      temperature: null,
      top_p: null,
      seed: null,
      a2a_enabled: false,
    },
  })

const [name] = defineField('name')
const [modelHint] = defineField('model_hint')
const [modelId] = defineField('model_id')
const [effort] = defineField('effort')
const [keyGroupId] = defineField('key_group_id')
const [systemPrompt] = defineField('system_prompt')
const [contextMode] = defineField('context_mode')
const [contextTokenCap] = defineField('context_token_cap')
const [temperature] = defineField('temperature')
const [topP] = defineField('top_p')
const [seed] = defineField('seed')
const [ragConfigId] = defineField('rag_config_id')
const [knowmapConfigId] = defineField('knowmap_config_id')
const [a2aEnabled] = defineField('a2a_enabled')

// Model-id combobox: per-provider preset list + "provider default" (stores null)
// + "custom" (free-text). `customModel` captures the intent to type a custom id
// while the field is empty — an empty model_id otherwise reads as "default".
const CUSTOM_MODEL = '__custom__'
const customModel = ref(false)
// One lookup of the active provider's catalog entry; models / default /
// context_limit are all derived from it (no repeated find() per computed).
const currentChatEntry = computed(() =>
  modelCatalogQuery.data.value?.chat.find((c) => c.provider === modelHint.value),
)
const chatModelsForHint = computed(() => currentChatEntry.value?.models ?? [])
// The model the runtime uses when model_id is left unset, surfaced in the
// "provider default" option label so the user sees which model that resolves to.
const defaultModelForHint = computed(() => currentChatEntry.value?.default ?? '')
const isCustomModel = computed(
  () =>
    customModel.value ||
    (!!modelId.value &&
      // Catalog loaded and the saved id isn't a preset -> custom. Also show the
      // free-text input when the catalog FAILED to load, so a saved custom model
      // stays viewable/editable instead of vanishing behind an empty dropdown.
      // While the catalog is still in-flight, neither branch fires (no flicker).
      ((!!modelCatalogQuery.data.value && !chatModelsForHint.value.includes(modelId.value)) ||
        modelCatalogQuery.isError.value)),
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

// Wakeup config. New agents default to replying to every message (every_n=1):
// without an enabled trigger an agent is inert and never responds, which is a
// surprising default for a chat agent. Edit mode loads the agent's real config
// in the query watcher below.
const wakeupConfig = ref<WakeupConfig>(defaultWakeupConfig())
if (isCreateMode) {
  wakeupConfig.value.triggers.every_n_messages = { enabled: true, n: 1 }
}

// SInput's model-value accepts `string | number` (no `null`); these fields are
// nullable numbers (unset = provider default). Bridge to '' for display only —
// SInput type="number" always emits a number, so the field keeps storing
// `number | null` exactly as before.
function nullableNumberModel(field: { value: number | null }) {
  return computed<string | number>({
    get: () => field.value ?? '',
    set: (v) => {
      field.value = v === '' ? null : Number(v)
    },
  })
}

// Sampling controls (R9.18) use a *text* input, not number: SInput type="number"
// coerces a cleared field to 0 (Number('') === 0), which would make it impossible
// to distinguish "provider default" (null) from the valid value temperature=0.
// A text input emits '' when cleared (-> null) and the numeric string otherwise.
// Non-numeric keystrokes are ignored so NaN never enters the model.
function nullableNumberFromText(field: { value: number | null }) {
  return computed<string | number>({
    get: () => field.value ?? '',
    set: (v) => {
      const s = String(v).trim()
      if (s === '') {
        field.value = null
        return
      }
      const n = Number(s)
      if (Number.isFinite(n)) field.value = n
    },
  })
}

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
    wakeupConfig.value,
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
        context_mode: agent.context_mode as AgentCreateInput['context_mode'],
        context_token_cap: agent.context_token_cap,
        temperature: agent.temperature,
        top_p: agent.top_p,
        seed: agent.seed,
        rag_config_id: agent.rag_config_id,
        knowmap_config_id: agent.knowmap_config_id,
        a2a_enabled: agent.a2a_enabled,
      },
    })
    wakeupConfig.value = normalizeWakeupConfig(agent.wakeup_config)

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
  // normalizeWakeupConfig (run when wakeupConfig was loaded) and SWakeupEditor's
  // mode toggle both enforce call_only being mutually exclusive with the
  // autonomous triggers, so the config here is already self-consistent. Clone it
  // so the reactive object isn't shared into (and mutated after) the payload.
  const wakeup_config = deepCloneJSON(wakeupConfig.value) as unknown as Record<string, unknown>

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

// Clearing model_id on a provider switch must happen ONLY for a user-initiated
// change (handled by the model-hint <SSelect> below). A watch(modelHint) would
// also fire during the edit-load resetForm — which flips the hint from the
// 'claude' create-default to the loaded agent's provider — and wipe the saved
// model_id for every non-Claude agent.
function onModelHintChange(value: string | number): void {
  modelHint.value = String(value) as AgentCreateInput['model_hint']
  modelId.value = null
  customModel.value = false
}

const contextTokenCapPlaceholder = computed(() => {
  const contextLimit = currentChatEntry.value?.context_limit ?? 128_000
  const defaultCap = Math.floor(contextLimit * 0.75)
  return t('agents.form.contextTokenCapDefault', { cap: defaultCap.toLocaleString() })
})
const contextTokenCapModel = nullableNumberModel(contextTokenCap)
const temperatureModel = nullableNumberFromText(temperature)
const topPModel = nullableNumberFromText(topP)
const seedModel = nullableNumberFromText(seed)

// Prompt Assistant applies a whole draft (it confirms overwrite itself when the
// editor is non-empty); the template picker inserts a template body, appending
// with a blank-line separator so it never silently clobbers existing content.
function onAssistantDraft(text: string): void {
  systemPrompt.value = text
}

function onTemplateInsert(body: string): void {
  const existing = (systemPrompt.value ?? '').trimEnd()
  systemPrompt.value = existing ? `${existing}\n\n${body}` : body
}

const saveDisabled = computed(
  () => !isCreateMode && !meta.value.dirty && !extrasDirty.value,
)

function applyAgentSaveError(err: unknown, fallbackMessage: string): void {
  if (!applyServerErrors(err)) toast.error(fallbackMessage)
}

// --- Create mutation ---
const createMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
    const data = await agentsApi.create(createProjectId, assemblePayload(values))
    return data
  },
  onSuccess: (agent) => {
    toast.success(t('agents.list.created'))
    router.replace({ name: 'agents.detail', params: { agentId: agent.id } })
  },
  onError: (err) => applyAgentSaveError(err, t('agents.list.createFailed')),
})

// --- Patch mutation ---
const patchMutation = useMutation({
  mutationFn: async (values: AgentCreateInput) => {
    const agent = query.data.value
    if (!agent) throw new Error('Agent not loaded')
    const data = await agentsApi.patch(agentId, agent.version, assemblePayload(values))
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
    applyAgentSaveError(err, t('agents.detail.saveFailed'))
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
  temperature: 'general',
  top_p: 'general',
  seed: 'general',
  system_prompt: 'prompt',
  rag_config_id: 'knowledge',
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
  {
    key: 'tools',
    label: t('agents.tools.tabLabel'),
    icon: ServerIcon,
    ...(enabledToolCount.value > 0 && { badge: String(enabledToolCount.value) }),
  },
  { key: 'orchestration', label: t('agents.detail.tabs.orchestration'), icon: ArrowsPointingOutIcon },
])

// STabs/SSelect emit `string | number` for model-value; tab keys here are always
// strings, so normalize defensively without changing behavior for existing callers.
function onTabChange(tab: string | number): void {
  const key = String(tab)
  activeTab.value = key
  router.replace({ query: { ...route.query, tab: key } })
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

const ragConfigOptions = computed(() => [
  { value: '', label: t('agents.form.noRagConfig') },
  ...(ragConfigsQuery.data.value ?? []).map((c) => ({ value: c.id, label: c.name })),
])

const knowmapConfigOptions = computed(() => [
  { value: '', label: t('agents.form.noKnowmapConfig') },
  ...(knowmapConfigsQuery.data.value ?? []).map((c) => ({ value: c.id, label: c.name })),
])

const pageTitle = computed(() => {
  if (isCreateMode) return t('agents.detail.new')
  return query.data.value?.name ?? t('agents.detail.title')
})

const breadcrumbs = computed(() => [
  {
    label: t('agents.breadcrumb.agents'),
    ...(pickerProjectId.value && {
      to: { name: 'agents.list', params: { projectId: pickerProjectId.value } },
    }),
  },
  { label: pageTitle.value },
])

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
        focus-on-mount
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
              :error="errors.name ?? ''"
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
                :error="errors.model_hint ?? ''"
                required
              >
                <SSelect
                  :model-value="modelHint"
                  :options="modelHintOptions"
                  @update:model-value="onModelHintChange"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.modelId')"
                name="model_id"
                :error="errors.model_id ?? ''"
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
              :error="errors.effort ?? ''"
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
              :error="errors.key_group_id ?? ''"
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
              :error="errors.context_token_cap ?? ''"
              :help="t('agents.form.contextTokenCapHelp')"
              class="mt-4"
            >
              <SInput
                v-model="contextTokenCapModel"
                type="number"
                :placeholder="contextTokenCapPlaceholder"
              />
            </SFormField>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-1">
              {{ t('agents.form.samplingTitle') }}
            </h3>
            <p class="mb-4 text-sm text-[var(--color-muted)]">
              {{ t('agents.form.samplingHelp') }}
            </p>
            <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <SFormField
                :label="t('agents.form.temperature')"
                name="temperature"
                :error="errors.temperature ?? ''"
                :help="t('agents.form.temperatureHelp')"
              >
                <SInput
                  v-model="temperatureModel"
                  type="text"
                  :placeholder="t('agents.form.samplingDefaultPlaceholder')"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.topP')"
                name="top_p"
                :error="errors.top_p ?? ''"
                :help="t('agents.form.topPHelp')"
              >
                <SInput
                  v-model="topPModel"
                  type="text"
                  :placeholder="t('agents.form.samplingDefaultPlaceholder')"
                />
              </SFormField>
              <SFormField
                :label="t('agents.form.seed')"
                name="seed"
                :error="errors.seed ?? ''"
                :help="t('agents.form.seedHelp')"
              >
                <SInput
                  v-model="seedModel"
                  type="text"
                  :placeholder="t('agents.form.samplingDefaultPlaceholder')"
                />
              </SFormField>
            </div>
          </SCard>
        </div>

        <!-- Tab: Prompt -->
        <div
          v-show="activeTab === 'prompt'"
          class="mt-6"
        >
          <div class="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_22rem]">
            <SCard>
              <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-semibold">
                  {{ t('agents.form.systemPrompt') }}
                </h3>
                <PromptTemplatePicker
                  v-if="pickerProjectId"
                  :project-id="pickerProjectId"
                  @insert="onTemplateInsert"
                />
              </div>
              <SFormField
                :label="t('agents.form.systemPrompt')"
                name="system_prompt"
                :error="errors.system_prompt ?? ''"
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

            <PromptAssistantPanel
              v-if="pickerProjectId"
              :project-id="pickerProjectId"
              :current-draft="systemPrompt ?? ''"
              class="min-h-[32rem] lg:sticky lg:top-6 lg:self-start lg:h-[calc(100vh-8rem)]"
              @apply-draft="onAssistantDraft"
            />
          </div>
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
              :error="errors.rag_config_id ?? ''"
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
              {{ t('agents.form.knowmapConfig') }}
            </h3>
            <SFormField
              :label="t('agents.form.knowmapConfig')"
              name="knowmap_config_id"
              :error="errors.knowmap_config_id ?? ''"
              :help="t('agents.form.manageKnowmapConfigs')"
            >
              <SSelect
                v-model="knowmapConfigId"
                :options="knowmapConfigOptions"
              />
            </SFormField>
            <SButton
              v-if="knowmapConfigId && pickerProjectId"
              variant="link"
              class="mt-2"
              :to="{
                name: 'agents.knowmapConfig',
                params: { projectId: pickerProjectId, configId: knowmapConfigId },
              }"
              as="router-link"
            >
              {{ t('agents.rag.manageLink') }}
            </SButton>
          </SCard>

          <!-- Read-only Concept Map coverage (WS5, R11.09) -->
          <SCard v-if="!isCreateMode">
            <h3 class="text-lg font-semibold mb-1">
              {{ t('agents.graphragCoverage.title') }}
            </h3>
            <p class="text-sm text-[var(--color-muted)] mb-4">
              {{ t('agents.graphragCoverage.help') }}
            </p>
            <div
              v-if="coverageQuery.isLoading.value"
              class="flex justify-center py-4"
            >
              <SLoadingSpinner />
            </div>
            <p
              v-else-if="coverageEntries.length === 0"
              class="text-sm text-[var(--color-muted)]"
            >
              {{ t('agents.graphragCoverage.empty') }}
            </p>
            <ul
              v-else
              class="space-y-2"
            >
              <li
                v-for="e in coverageEntries"
                :key="e.config_id"
                class="flex items-center justify-between gap-3 border-b border-[var(--color-border)] pb-2 last:border-0"
              >
                <div class="min-w-0 flex items-center gap-2">
                  <span class="font-medium truncate">{{ e.owner_name }}</span>
                  <SBadge
                    :variant="ownerKindVariant(e.owner_kind)"
                    size="sm"
                  >
                    {{ t(`agents.conceptMaps.ownerKinds.${e.owner_kind}`) }}
                  </SBadge>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <SBadge
                    :variant="graphragBuildStateVariant(e.last_build_state)"
                    size="sm"
                  >
                    {{ t(graphragBuildStateLabelKey(e.last_build_state)) }}
                  </SBadge>
                  <SBadge
                    :variant="e.active ? 'success' : 'neutral'"
                    size="sm"
                  >
                    {{ e.active ? t('agents.graphragCoverage.active') : t('agents.graphragCoverage.inactive') }}
                  </SBadge>
                </div>
              </li>
            </ul>
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
            <SWakeupEditor v-model="wakeupConfig" />
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
