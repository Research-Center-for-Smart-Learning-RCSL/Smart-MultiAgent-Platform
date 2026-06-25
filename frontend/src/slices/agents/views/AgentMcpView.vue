<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  PlusIcon,
  TrashIcon,
  PencilSquareIcon,
  PlayIcon,
  ServerIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SBadge,
  SButton,
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
import { agentsApi, type McpBinding, type McpBindingPatchInput } from '../api'
import { agentKeys } from '../queries'
import { mcpBindingCreateSchema, type McpBindingCreateInput } from '../types/schemas'
import { useMcpTest } from '../composables/useMcpTest'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const agentId = route.params.agentId as string

const BUILTIN_TOOLS = ['file', 'web_search', 'code_exec']

const agentQuery = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: async () => (await agentsApi.get(agentId)).data,
})
const projectId = computed(() => agentQuery.data.value?.project_id ?? '')

const bindingsQuery = useQuery({
  queryKey: agentKeys.mcpBindings(agentId),
  queryFn: async () => (await agentsApi.listMcpBindings(agentId)).data,
})

const bindings = computed<McpBinding[]>(() => bindingsQuery.data.value ?? [])
const loading = computed(() => bindingsQuery.isLoading.value)

const { isTesting, runTest } = useMcpTest(agentId)

// --- Add / Edit modal ---
const showModal = ref(false)
const editingBinding = ref<McpBinding | null>(null)
const isEditing = computed(() => !!editingBinding.value)
const configJsonError = ref<string | null>(null)

const schema = toTypedSchema(mcpBindingCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } =
  useForm<McpBindingCreateInput>({
    validationSchema: schema,
    initialValues: { source: 'url', reference: '', allowed_tools: [], config: {} },
  })

const [source] = defineField('source')
const [reference] = defineField('reference')

watch(source, () => {
  if (!isEditing.value) reference.value = ''
})

const allowedToolsRaw = ref('')
const configJson = ref('{}')

function openAddModal(): void {
  editingBinding.value = null
  resetForm()
  allowedToolsRaw.value = ''
  configJson.value = '{}'
  configJsonError.value = null
  showModal.value = true
}

function openEditModal(binding: McpBinding): void {
  editingBinding.value = binding
  resetForm({
    values: {
      source: binding.source,
      reference: binding.reference,
      allowed_tools: binding.allowed_tools,
      config: binding.config,
    },
  })
  allowedToolsRaw.value = binding.allowed_tools.join(', ')
  configJson.value = JSON.stringify(binding.config, null, 2)
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
  mutationFn: async (payload: McpBindingCreateInput) =>
    (await agentsApi.addMcpBinding(agentId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.mcpBindings(agentId) })
    showModal.value = false
    toast.success(t('agents.mcp.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.mcp.createFailed'))
  },
})

// Only allowed_tools and config are mutable; source/reference are immutable
// (and disabled in the form), so editing PATCHes just those two fields.
const patchMutation = useMutation({
  mutationFn: async (vars: { bindingId: string; payload: McpBindingPatchInput }) =>
    (await agentsApi.patchMcpBinding(agentId, vars.bindingId, vars.payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.mcpBindings(agentId) })
    showModal.value = false
    toast.success(t('agents.mcp.updated'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.mcp.createFailed'))
  },
})

const submitting = computed(
  () => createMutation.isPending.value || patchMutation.isPending.value,
)

const onSubmit = handleSubmit((values) => {
  const parsedConfig = parseConfig(configJson.value)
  if (parsedConfig === null) {
    configJsonError.value = t('agents.mcp.invalidJson')
    return
  }
  configJsonError.value = null
  const allowed_tools = parseTools(allowedToolsRaw.value)
  const editing = editingBinding.value
  if (editing) {
    patchMutation.mutate({
      bindingId: editing.id,
      payload: { allowed_tools, config: parsedConfig },
    })
  } else {
    createMutation.mutate({ ...values, allowed_tools, config: parsedConfig })
  }
})

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (bindingId: string) => agentsApi.deleteMcpBinding(agentId, bindingId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.mcpBindings(agentId) })
    toast.success(t('agents.mcp.deleted'))
  },
  onError: () => toast.error(t('agents.mcp.deleteFailed')),
})

async function confirmDelete(b: McpBinding): Promise<void> {
  const ok = await confirm({
    title: t('agents.mcp.deleteTitle'),
    message: t('agents.mcp.deleteConfirm', { ref: b.reference }),
    variant: 'warning',
  })
  if (!ok) return
  deleteMutation.mutate(b.id)
}

function onAction(key: string, row: McpBinding): void {
  if (key === 'test') runTest(row.id)
  else if (key === 'edit') openEditModal(row)
  else if (key === 'delete') void confirmDelete(row)
}

const actionItems = computed(() => [
  { key: 'test', label: t('agents.mcp.test'), icon: PlayIcon },
  { key: 'edit', label: t('common.edit', 'Edit'), icon: PencilSquareIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('agents.mcp.delete'), icon: TrashIcon, danger: true },
])

const sourceOptions = computed(() => [
  { value: 'builtin', label: t('agents.mcp.sources.builtin') },
  { value: 'url', label: t('agents.mcp.sources.url') },
  { value: 'package', label: t('agents.mcp.sources.package') },
])

const builtinOptions = computed(() =>
  BUILTIN_TOOLS.map((tool) => ({ value: tool, label: tool })),
)

const referenceHelp = computed(() => {
  const src = source.value as 'builtin' | 'url' | 'package'
  return t(`agents.mcp.referenceHelp.${src}`)
})

const columns = computed<Column[]>(() => [
  { key: 'source', label: t('agents.mcp.colSource'), width: '90px' },
  { key: 'reference', label: t('agents.mcp.colReference') },
  { key: 'tools', label: t('agents.mcp.colTools'), width: '120px' },
  { key: 'actions', label: '', width: '120px', align: 'right' },
])

const accordionItems = computed(() => [
  { key: 'config', title: t('agents.mcp.advancedConfig') },
])
</script>

<template>
  <main class="p-6">
    <SPageHeader :title="t('agents.mcp.title')">
      <template #actions>
        <SButton
          variant="primary"
          @click="openAddModal"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agents.mcp.add') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      variant="info"
      class="mt-4"
    >
      {{ t('agents.mcp.infoAlert') }}
      <template #actions>
        <SButton
          v-if="projectId"
          variant="link"
          :to="{ name: 'agents.egressAllowlist', params: { projectId } }"
          as="router-link"
        >
          {{ t('agents.mcp.manageEgress') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      :columns="columns"
      :data="bindings"
      :loading="loading"
      row-key="id"
      class="mt-6"
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
        <template v-if="!row.allowed_tools.length">
          {{ t('agents.mcp.allTools') }}
        </template>
        <template v-else>
          {{ t('agents.mcp.nAllowed', { n: row.allowed_tools.length }) }}
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
            {{ t('agents.mcp.test') }}
          </SButton>
          <SDropdown
            :items="actionItems"
            placement="bottom-end"
            @select="onAction($event, row)"
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
          :title="t('agents.mcp.emptyTitle')"
          :text="t('agents.mcp.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="openAddModal"
            >
              {{ t('agents.mcp.add') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <!-- Add / Edit modal -->
    <SModal
      :open="showModal"
      :title="isEditing ? t('common.edit', 'Edit') : t('agents.mcp.add')"
      size="lg"
      @close="showModal = false"
    >
      <form @submit.prevent="onSubmit">
        <SFormField
          :label="t('agents.mcp.source')"
          name="source"
          :error="errors.source"
          required
        >
          <SSelect
            v-model="source"
            :options="sourceOptions"
            :disabled="isEditing"
          />
        </SFormField>

        <SFormField
          :label="t('agents.mcp.reference')"
          name="reference"
          :error="errors.reference"
          :help="referenceHelp"
          required
        >
          <SSelect
            v-if="source === 'builtin' && !isEditing"
            v-model="reference"
            :options="builtinOptions"
            :placeholder="t('agents.mcp.referencePlaceholderBuiltin')"
          />
          <SInput
            v-else
            v-model="reference"
            :placeholder="source === 'url'
              ? t('agents.mcp.referencePlaceholderUrl')
              : t('agents.mcp.referencePlaceholderPackage')"
            :error="!!errors.reference"
            :disabled="isEditing"
          />
        </SFormField>

        <SFormField
          :label="t('agents.mcp.allowedTools')"
          name="allowed_tools"
          :help="t('agents.mcp.allowedToolsHelp')"
        >
          <STextarea
            v-model="allowedToolsRaw"
            :rows="3"
            :placeholder="t('agents.mcp.allowedToolsPlaceholder')"
          />
        </SFormField>

        <SAccordion
          :items="accordionItems"
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
            {{ t('agents.mcp.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="submitting"
            @click="onSubmit"
          >
            {{ isEditing ? t('agents.mcp.save') : t('agents.mcp.submit') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
