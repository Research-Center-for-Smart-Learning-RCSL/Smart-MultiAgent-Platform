<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  ArrowUpTrayIcon,
  PlusIcon,
  TrashIcon,
  PencilSquareIcon,
  PlayIcon,
  ServerIcon,
  EllipsisVerticalIcon,
  DocumentIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SBadge,
  SButton,
  SCard,
  SToggle,
  SDropdown,
  SModal,
  SFormField,
  SInput,
  SSelect,
  STextarea,
  SCodeEditor,
  SAccordion,
  SAlert,
  SEmptyState,
} from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import {
  agentsApi,
  type AgentTool,
  type AgentToolPatchInput,
  type AgentToolType,
  type WorkspaceFile,
} from '../api'
import { agentKeys } from '../queries'
import { mcpToolCreateSchema } from '../types/schemas'
import { useToolTest } from '../composables/useToolTest'
import type { Column } from '@shared/ui/STable.vue'

type McpToolCreateInput = {
  tool_type: 'hosted_mcp'
  display_name?: string
  config: { source: 'url' | 'package'; reference: string; allowed_tools: string[] }
  auth?: Record<string, unknown>
}

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const agentId = route.params.agentId as string

const agentQuery = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: async () => (await agentsApi.get(agentId)).data,
})
const projectId = computed(() => agentQuery.data.value?.project_id ?? '')

const breadcrumbs = computed(() => [
  { label: agentQuery.data.value?.name ?? '...', to: { name: 'agents.detail', params: { agentId } } },
  { label: t('agents.tools.tabLabel') },
])

// --- Unified tools query ---
const toolsQuery = useQuery({
  queryKey: agentKeys.tools(agentId),
  queryFn: async () => (await agentsApi.listTools(agentId)).data,
})

const allTools = computed<AgentTool[]>(() => toolsQuery.data.value ?? [])
const loading = computed(() => toolsQuery.isLoading.value)

// Derived views
const singletonTool = (type: AgentToolType) =>
  computed(() => allTools.value.find((t) => t.tool_type === type))

const webSearch = singletonTool('hosted_web_search')
const codeInterpreter = singletonTool('hosted_code_interpreter')
const fileWorkspace = singletonTool('hosted_file_workspace')
const fileSearch = singletonTool('hosted_file_search')
const mcpTools = computed(() => allTools.value.filter((t) => t.tool_type === 'hosted_mcp'))
const functionTools = computed(() => allTools.value.filter((t) => t.tool_type === 'local_function'))

const { isTesting, runTest, failedResult } = useToolTest(agentId)

// --- Singleton toggle ---
const toggleMutation = useMutation({
  mutationFn: async (vars: { toolId: string; enabled: boolean }) =>
    (await agentsApi.patchTool(agentId, vars.toolId, { enabled: vars.enabled })).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.tools(agentId) })
    toast.success(t('agents.tools.toggleSuccess'))
  },
  onError: () => toast.error(t('agents.tools.toggleFailed')),
})

const toggleBusy = computed(() => toggleMutation.isPending.value || toolsQuery.isLoading.value)

function toggleSingleton(tool: AgentTool | undefined, value: boolean): void {
  if (!tool) return
  toggleMutation.mutate({ toolId: tool.id, enabled: value })
}

// Singleton card rows for template iteration
const singletonCards = computed(() => [
  {
    key: 'webSearch' as const,
    tool: webSearch.value,
    label: t('agents.tools.webSearch.label'),
    description: t('agents.tools.webSearch.description'),
  },
  {
    key: 'codeInterpreter' as const,
    tool: codeInterpreter.value,
    label: t('agents.tools.codeInterpreter.label'),
    description: t('agents.tools.codeInterpreter.description'),
  },
  {
    key: 'fileWorkspace' as const,
    tool: fileWorkspace.value,
    label: t('agents.tools.fileWorkspace.label'),
    description: t('agents.tools.fileWorkspace.description'),
  },
  {
    key: 'fileSearch' as const,
    tool: fileSearch.value,
    label: t('agents.tools.fileSearch.label'),
    description: t('agents.tools.fileSearch.description'),
  },
])

// --- Workspace files (Code Interpreter) ---
const wsFilesQuery = useQuery({
  queryKey: agentKeys.workspaceFiles(agentId),
  queryFn: async () => (await agentsApi.listWorkspaceFiles(agentId)).data,
  enabled: computed(() => !!codeInterpreter.value?.enabled),
})
const wsFiles = computed<WorkspaceFile[]>(() => wsFilesQuery.data.value ?? [])
const wsFileInput = ref<HTMLInputElement | null>(null)

const uploadWsFile = useMutation({
  mutationFn: async (file: File) =>
    (await agentsApi.uploadWorkspaceFile(agentId, file)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.workspaceFiles(agentId) })
    toast.success(t('agents.tools.codeInterpreter.files.uploaded'))
  },
  onError: () => toast.error(t('agents.tools.codeInterpreter.files.uploadFailed')),
})

const WS_MAX_FILE_SIZE = 32 * 1024 * 1024

function onWsFileChange(ev: Event): void {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (file.size > WS_MAX_FILE_SIZE) {
    toast.error(t('agents.tools.codeInterpreter.files.uploadFailed'))
    input.value = ''
    return
  }
  uploadWsFile.mutate(file)
  input.value = ''
}

const deleteWsFile = useMutation({
  mutationFn: (fileId: string) => agentsApi.deleteWorkspaceFile(agentId, fileId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.workspaceFiles(agentId) })
    toast.success(t('agents.tools.codeInterpreter.files.deleted'))
  },
  onError: () => toast.error(t('agents.tools.codeInterpreter.files.deleteFailed')),
})

async function confirmDeleteWsFile(file: WorkspaceFile): Promise<void> {
  const ok = await confirm({
    title: t('agents.tools.codeInterpreter.files.deleteTitle'),
    message: t('agents.tools.codeInterpreter.files.deleteConfirm', { path: file.path }),
    variant: 'warning',
  })
  if (!ok) return
  deleteWsFile.mutate(file.id)
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// --- MCP Add / Edit modal ---
const showModal = ref(false)
const editingTool = ref<AgentTool | null>(null)
const isEditing = computed(() => !!editingTool.value)
const configJsonError = ref<string | null>(null)

const schema = toTypedSchema(mcpToolCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } =
  useForm<McpToolCreateInput>({
    validationSchema: schema,
    initialValues: {
      tool_type: 'hosted_mcp',
      config: { source: 'url', reference: '', allowed_tools: [] },
    },
  })

const [mcpSource, mcpSourceAttrs] = defineField('config.source')
const [mcpReference, mcpReferenceAttrs] = defineField('config.reference')

watch(mcpSource, () => {
  if (!isEditing.value) mcpReference.value = ''
})

const allowedToolsRaw = ref('')
const configJson = ref('{}')

function openAddModal(): void {
  editingTool.value = null
  resetForm()
  allowedToolsRaw.value = ''
  configJson.value = '{}'
  configJsonError.value = null
  showModal.value = true
}

function openEditModal(tool: AgentTool): void {
  editingTool.value = tool
  const cfg = tool.config as Record<string, unknown>
  const allowedTools = (cfg.allowed_tools ?? []) as string[]
  resetForm({
    values: {
      tool_type: 'hosted_mcp',
      config: {
        source: (cfg.source as 'url' | 'package') ?? 'url',
        reference: (cfg.reference as string) ?? '',
        allowed_tools: allowedTools,
      },
    },
  })
  allowedToolsRaw.value = allowedTools.join(', ')
  const configWithout = { ...cfg }
  delete configWithout.source
  delete configWithout.reference
  delete configWithout.allowed_tools
  delete configWithout.auth
  configJson.value = JSON.stringify(configWithout, null, 2)
  configJsonError.value = null
  showModal.value = true
}

function parseTools(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function parseConfig(raw: string): Record<string, unknown> | null {
  try {
    return JSON.parse(raw) as Record<string, unknown>
  } catch {
    return null
  }
}

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (payload: McpToolCreateInput) =>
    (await agentsApi.addTool(agentId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.tools(agentId) })
    showModal.value = false
    toast.success(t('agents.tools.mcp.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.tools.mcp.createFailed'))
  },
})

const patchMutation = useMutation({
  mutationFn: async (vars: { toolId: string; payload: AgentToolPatchInput }) =>
    (await agentsApi.patchTool(agentId, vars.toolId, vars.payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.tools(agentId) })
    showModal.value = false
    toast.success(t('agents.tools.mcp.updated'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.tools.mcp.createFailed'))
  },
})

const submitting = computed(
  () => createMutation.isPending.value || patchMutation.isPending.value,
)

const onSubmit = handleSubmit((values) => {
  const parsedExtraConfig = parseConfig(configJson.value)
  if (parsedExtraConfig === null) {
    configJsonError.value = t('agents.tools.mcp.invalidJson')
    return
  }
  configJsonError.value = null
  const allowed_tools = parseTools(allowedToolsRaw.value)

  const editing = editingTool.value
  if (editing) {
    const mergedConfig: Record<string, unknown> = {
      ...editing.config,
      ...parsedExtraConfig,
      source: values.config.source,
      reference: values.config.reference,
      allowed_tools,
    }
    patchMutation.mutate({
      toolId: editing.id,
      payload: { config: mergedConfig },
    })
  } else {
    createMutation.mutate({
      ...values,
      config: {
        ...values.config,
        ...parsedExtraConfig,
        allowed_tools,
      },
    })
  }
})

// --- MCP Delete ---
const deleteMutation = useMutation({
  mutationFn: (toolId: string) => agentsApi.deleteTool(agentId, toolId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.tools(agentId) })
    toast.success(t('agents.tools.mcp.deleted'))
  },
  onError: () => toast.error(t('agents.tools.mcp.deleteFailed')),
})

async function confirmDelete(tool: AgentTool): Promise<void> {
  const ref = (tool.config as Record<string, unknown>).reference as string ?? tool.display_name ?? ''
  const ok = await confirm({
    title: t('agents.tools.mcp.deleteTitle'),
    message: t('agents.tools.mcp.deleteConfirm', { ref }),
    variant: 'warning',
  })
  if (!ok) return
  deleteMutation.mutate(tool.id)
}

function onMcpAction(key: string, row: AgentTool): void {
  if (key === 'test') runTest(row.id)
  else if (key === 'edit') openEditModal(row)
  else if (key === 'delete') void confirmDelete(row)
}

const mcpActionItems = computed(() => [
  { key: 'test', label: t('agents.tools.mcp.test'), icon: PlayIcon },
  { key: 'edit', label: t('common.edit', 'Edit'), icon: PencilSquareIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('agents.tools.mcp.deleteTitle'), icon: TrashIcon, danger: true },
])

const sourceOptions = computed(() => [
  { value: 'url', label: t('agents.tools.mcp.sourceUrl') },
  { value: 'package', label: t('agents.tools.mcp.sourcePackage') },
])

const referenceHelp = computed(() => {
  const src = mcpSource.value as 'url' | 'package'
  return t(`agents.tools.mcp.referenceHelp.${src}`)
})

const mcpColumns = computed<Column[]>(() => [
  { key: 'source', label: t('agents.tools.mcp.colSource'), width: '90px' },
  { key: 'reference', label: t('agents.tools.mcp.colReference') },
  { key: 'tools', label: t('agents.tools.mcp.colTools'), width: '120px' },
  { key: 'actions', label: '', width: '120px', align: 'right' },
])

const mcpAccordionItems = computed(() => [
  { key: 'config', title: t('agents.tools.mcp.advancedConfig') },
])
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.tools.tabLabel')"
      :breadcrumbs="breadcrumbs"
    />

    <!-- ========== Hosted Tools ========== -->
    <section class="mt-6">
      <h2 class="text-base font-semibold text-[var(--color-fg)]">
        {{ t('agents.tools.hosted.title') }}
      </h2>
      <p class="mt-1 text-sm text-[var(--color-muted)]">
        {{ t('agents.tools.hosted.subtitle') }}
      </p>

      <!-- Singleton hosted tool toggles -->
      <SCard class="mt-4">
        <ul class="flex flex-col divide-y divide-[var(--color-border)]">
          <li
            v-for="card in singletonCards"
            :key="card.key"
            class="flex items-center justify-between gap-4 py-3"
          >
            <div class="min-w-0">
              <label
                :for="`tool-${card.key}`"
                class="text-sm font-medium text-[var(--color-fg)]"
              >{{ card.label }}</label>
              <p class="text-xs text-[var(--color-muted)]">
                {{ card.description }}
              </p>
            </div>
            <SToggle
              :id="`tool-${card.key}`"
              :model-value="card.tool?.enabled ?? false"
              :disabled="toggleBusy || !card.tool"
              @update:model-value="toggleSingleton(card.tool, $event)"
            />
          </li>
        </ul>
      </SCard>

      <!-- Code Interpreter workspace files -->
      <SCard
        v-if="codeInterpreter?.enabled"
        class="mt-4"
      >
        <div class="flex items-center justify-between mb-3">
          <div>
            <h3 class="text-sm font-semibold text-[var(--color-fg)]">
              {{ t('agents.tools.codeInterpreter.files.title') }}
            </h3>
            <p class="text-xs text-[var(--color-muted)]">
              {{ t('agents.tools.codeInterpreter.files.hint') }}
            </p>
          </div>
          <div>
            <label
              class="sr-only"
              for="ws-file-upload"
            >
              {{ t('agents.tools.codeInterpreter.files.upload') }}
            </label>
            <input
              id="ws-file-upload"
              ref="wsFileInput"
              type="file"
              class="hidden"
              accept=".csv,.tsv,.json,.jsonl,.txt,.md,.pdf,.docx,.xlsx,.py,.r,.sql,.xml,.yaml,.yml,.parquet,.zip,.tar,.gz,.png,.jpg,.jpeg,.gif,.webp"
              @change="onWsFileChange"
            >
            <SButton
              variant="secondary"
              size="sm"
              :loading="uploadWsFile.isPending.value"
              @click="wsFileInput?.click()"
            >
              <template #icon-left>
                <ArrowUpTrayIcon class="w-4 h-4" />
              </template>
              {{ t('agents.tools.codeInterpreter.files.upload') }}
            </SButton>
          </div>
        </div>
        <p class="text-xs text-[var(--color-muted)] mb-3">
          {{ t('agents.tools.codeInterpreter.files.sizeHint') }}
        </p>

        <div v-if="wsFiles.length">
          <ul class="flex flex-col divide-y divide-[var(--color-border)]">
            <li
              v-for="file in wsFiles"
              :key="file.id"
              class="flex items-center justify-between gap-3 py-2"
            >
              <div class="flex items-center gap-2 min-w-0">
                <DocumentIcon class="w-4 h-4 shrink-0 text-[var(--color-muted)]" />
                <span class="text-sm font-mono truncate">{{ file.path }}</span>
                <span class="text-xs text-[var(--color-muted)] shrink-0">{{ formatBytes(file.size_bytes) }}</span>
              </div>
              <SButton
                variant="ghost"
                size="sm"
                icon-only
                :title="t('agents.tools.codeInterpreter.files.deleteTitle')"
                @click="confirmDeleteWsFile(file)"
              >
                <TrashIcon class="w-4 h-4 text-[var(--color-danger)]" />
              </SButton>
            </li>
          </ul>
        </div>
        <SEmptyState
          v-else
          :icon="DocumentIcon"
          :title="t('agents.tools.codeInterpreter.files.empty')"
        />
      </SCard>

      <!-- MCP Servers -->
      <SCard class="mt-4">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h3 class="text-sm font-semibold text-[var(--color-fg)]">
              {{ t('agents.tools.mcp.label') }}
            </h3>
            <p class="text-xs text-[var(--color-muted)]">
              {{ t('agents.tools.mcp.description') }}
            </p>
          </div>
          <SButton
            variant="secondary"
            size="sm"
            @click="openAddModal"
          >
            <template #icon-left>
              <PlusIcon class="w-4 h-4" />
            </template>
            {{ t('agents.tools.mcp.add') }}
          </SButton>
        </div>

        <STable
          :columns="mcpColumns"
          :data="mcpTools"
          :loading="loading"
          row-key="id"
        >
          <template #cell-source="{ row }">
            <SBadge variant="neutral">
              {{ (row.config as Record<string, unknown>).source ?? 'url' }}
            </SBadge>
          </template>

          <template #cell-reference="{ row }">
            <span class="font-mono text-sm break-all">{{ (row.config as Record<string, unknown>).reference ?? row.display_name }}</span>
          </template>

          <template #cell-tools="{ row }">
            <template v-if="!((row.config as Record<string, unknown>).allowed_tools as string[] ?? []).length">
              {{ t('agents.tools.mcp.allTools') }}
            </template>
            <template v-else>
              {{ t('agents.tools.mcp.nAllowed', { n: ((row.config as Record<string, unknown>).allowed_tools as string[]).length }) }}
            </template>
          </template>

          <template #actions="{ row }">
            <div class="flex items-center gap-2">
              <SButton
                variant="ghost"
                size="sm"
                :loading="isTesting(row.id)"
                @click="runTest(row.id)"
              >
                {{ t('agents.tools.mcp.test') }}
              </SButton>
              <SDropdown
                :items="mcpActionItems"
                placement="bottom-end"
                @select="onMcpAction($event, row)"
              >
                <template #trigger>
                  <SButton
                    variant="ghost"
                    icon-only
                    size="sm"
                  >
                    <EllipsisVerticalIcon class="w-4 h-4" />
                  </SButton>
                </template>
              </SDropdown>
            </div>
          </template>

          <template #empty>
            <SEmptyState
              :icon="ServerIcon"
              :title="t('agents.tools.mcp.emptyTitle')"
              :text="t('agents.tools.mcp.emptyDescription')"
            >
              <template #action>
                <SButton
                  variant="primary"
                  @click="openAddModal"
                >
                  {{ t('agents.tools.mcp.add') }}
                </SButton>
              </template>
            </SEmptyState>
          </template>
        </STable>
      </SCard>
    </section>

    <!-- ========== Local Tools ========== -->
    <section class="mt-8">
      <h2 class="text-base font-semibold text-[var(--color-fg)]">
        {{ t('agents.tools.local.title') }}
      </h2>
      <p class="mt-1 text-sm text-[var(--color-muted)]">
        {{ t('agents.tools.local.subtitle') }}
      </p>

      <!-- Functions (Phase E fills this out) -->
      <SCard class="mt-4">
        <h3 class="text-sm font-semibold text-[var(--color-fg)]">
          {{ t('agents.tools.functions.label') }}
        </h3>
        <p class="text-xs text-[var(--color-muted)]">
          {{ t('agents.tools.functions.description') }}
        </p>
        <SEmptyState
          v-if="!functionTools.length"
          class="mt-4"
          :title="t('agents.tools.functions.emptyTitle')"
          :text="t('agents.tools.functions.emptyDescription')"
        />
      </SCard>

      <!-- Local Shell — coming soon -->
      <SCard class="mt-4">
        <div class="flex items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-sm font-semibold text-[var(--color-fg)]">
                {{ t('agents.tools.localShell.label') }}
              </span>
              <SBadge variant="neutral" size="sm">
                {{ t('agents.tools.localShell.badge') }}
              </SBadge>
            </div>
            <p class="text-xs text-[var(--color-muted)]">
              {{ t('agents.tools.localShell.description') }}
            </p>
          </div>
          <SToggle
            :model-value="false"
            disabled
            :title="t('agents.tools.localShell.comingSoon')"
            :aria-label="t('agents.tools.localShell.comingSoon')"
          />
        </div>
      </SCard>
    </section>

    <!-- ========== MCP Add / Edit Modal ========== -->
    <SModal
      :open="showModal"
      :title="isEditing ? t('common.edit', 'Edit') : t('agents.tools.mcp.add')"
      size="lg"
      @close="showModal = false"
    >
      <form @submit.prevent="onSubmit">
        <SFormField
          :label="t('agents.tools.mcp.source')"
          name="config.source"
          :error="errors['config.source']"
          required
        >
          <SSelect
            v-model="mcpSource"
            :options="sourceOptions"
            :disabled="isEditing"
          />
        </SFormField>

        <SFormField
          :label="t('agents.tools.mcp.reference')"
          name="config.reference"
          :error="errors['config.reference']"
          :help="referenceHelp"
          required
        >
          <SInput
            v-model="mcpReference"
            :placeholder="mcpSource === 'url'
              ? t('agents.tools.mcp.referencePlaceholderUrl')
              : t('agents.tools.mcp.referencePlaceholderPackage')"
            :error="!!errors['config.reference']"
            :disabled="isEditing"
          />
        </SFormField>

        <SFormField
          :label="t('agents.tools.mcp.allowedTools')"
          name="allowed_tools"
          :help="t('agents.tools.mcp.allowedToolsHelp')"
        >
          <STextarea
            v-model="allowedToolsRaw"
            :rows="3"
            :placeholder="t('agents.tools.mcp.allowedToolsPlaceholder')"
          />
        </SFormField>

        <SAccordion
          :items="mcpAccordionItems"
          class="mt-4"
        >
          <template #item-config>
            <SCodeEditor
              v-model="configJson"
              language="json"
              :rows="6"
            />
            <p
              v-if="configJsonError"
              class="text-xs text-[var(--color-danger)] mt-1"
            >
              {{ configJsonError }}
            </p>
          </template>
        </SAccordion>
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showModal = false"
          >
            {{ t('common.cancel', 'Cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="submitting"
            @click="onSubmit"
          >
            {{ isEditing ? t('common.save', 'Save') : t('agents.tools.mcp.add') }}
          </SButton>
        </div>
      </template>
    </SModal>

    <!-- MCP test failure modal -->
    <SModal
      :open="!!failedResult"
      :title="t('agents.tools.mcp.testErrorTitle')"
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
        {{ t('agents.tools.mcp.testErrorDuration', { ms: failedResult.duration_ms }) }}
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
