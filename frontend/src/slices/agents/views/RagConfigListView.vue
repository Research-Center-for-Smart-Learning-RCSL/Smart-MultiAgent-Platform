<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  PlusIcon,
  PencilSquareIcon,
  TrashIcon,
  DocumentTextIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SSearchInput,
  STable,
  SBadge,
  SButton,
  SDropdown,
  SModal,
  SFormField,
  SInput,
  SSelect,
  SToggle,
  SEmptyState,
  SPagination,
  STooltip,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useClientPagination,
} from '@shared/composables'
import { projectKeysApi, CAPABILITIES, keysKeys } from '@slices/keys'
import { projectsApi, tenancyKeys } from '@slices/tenancy'
import { agentsApi, type RagConfig } from '../api'
import { agentKeys } from '../queries'
import { ragConfigCreateSchema, type RagConfigCreateInput } from '../types/schemas'
import { useRagConfigForm } from '../composables/useRagConfigForm'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()
const { confirm } = useConfirmDialog()

const search = ref('')
const showModal = ref(false)

const projectQuery = useQuery({
  queryKey: tenancyKeys.project(projectId),
  queryFn: async () => (await projectsApi.get(projectId)).data,
})

const breadcrumbs = computed(() => [
  { label: t('agents.breadcrumb.projects'), to: { name: 'tenancy.projectList' } },
  { label: projectQuery.data.value?.name ?? projectId.slice(0, 8), to: { name: 'tenancy.projectDetail', params: { id: projectId } } },
  { label: t('agents.breadcrumb.ragConfigs') },
])

const configsQuery = useQuery({
  queryKey: agentKeys.ragConfigs(projectId),
  queryFn: async () => (await agentsApi.listRagConfigs(projectId)).data,
})

const projectKeysQuery = useQuery({
  queryKey: keysKeys.projectKeys(projectId),
  queryFn: async () => (await projectKeysApi.listCarried(projectId)).data,
})

const configs = computed<RagConfig[]>(() => configsQuery.data.value ?? [])
const loading = computed(() => configsQuery.isLoading.value)

const embedKeys = computed(() =>
  (projectKeysQuery.data.value ?? []).filter((k) =>
    CAPABILITIES[k.provider].includes('embedding'),
  ),
)
const rerankKeys = computed(() =>
  (projectKeysQuery.data.value ?? []).filter((k) =>
    CAPABILITIES[k.provider].includes('rerank'),
  ),
)

const filteredConfigs = computed(() => {
  if (!search.value) return configs.value
  const q = search.value.toLowerCase()
  return configs.value.filter((c) => c.name.toLowerCase().includes(q))
})

const { currentPage, totalPages, paginatedItems, pageSize } =
  useClientPagination(filteredConfigs)

// --- Create form ---
const schema = toTypedSchema(ragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, values } =
  useForm<RagConfigCreateInput>({
    validationSchema: schema,
    initialValues: {
      name: '',
      chunk_strategy: 'fixed',
      chunk_params: {},
      embed_key_id: '',
      embed_provider: 'openai',
      embed_model: '',
      rerank_enabled: false,
      rerank_key_id: null,
      rerank_provider: null,
      rerank_model: null,
      top_k: 8,
    },
  })

const [name] = defineField('name')
const [chunkStrategy] = defineField('chunk_strategy')
const [embedKeyId] = defineField('embed_key_id')
const [embedProvider] = defineField('embed_provider')
const [embedModel] = defineField('embed_model')
const [rerankEnabled] = defineField('rerank_enabled')
const [rerankKeyId] = defineField('rerank_key_id')
const [rerankModel] = defineField('rerank_model')
const [topK] = defineField('top_k')
const [rerankProvider] = defineField('rerank_provider')

const {
  chunkSizeTokens,
  chunkOverlapTokens,
  similarityThreshold,
  hasEmbedKeys,
  embedKeyOptions,
  rerankKeyOptions,
  resetChunkDefaults,
  defaultEmbedKey,
  assembleChunkParams,
} = useRagConfigForm({
  embedKeys,
  rerankKeys,
  embedKeyId,
  embedProvider,
  rerankEnabled,
  rerankKeyId,
  rerankProvider,
  rerankModel,
})

// Rerank needs a Cohere key; if the toggle is on without one the backend rejects
// with CapabilityMismatch, so disable the toggle when none exist and block submit
// if it is somehow on without a key.
const hasRerankKeys = computed(() => rerankKeys.value.length > 0)
const rerankIncomplete = computed(() => rerankEnabled.value && !rerankKeyId.value)

function openCreateModal(): void {
  resetForm()
  resetChunkDefaults()
  defaultEmbedKey()
  showModal.value = true
}

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (payload: RagConfigCreateInput) =>
    (await agentsApi.createRagConfig(projectId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
    showModal.value = false
    toast.success(t('agents.ragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.ragList.createFailed'))
  },
})

const onSubmit = handleSubmit((formValues) => {
  createMutation.mutate({
    ...formValues,
    chunk_params: assembleChunkParams(formValues.chunk_strategy),
  })
})

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteRagConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
    toast.success(t('agents.ragList.deleted'))
  },
  onError: () => toast.error(t('agents.ragList.deleteFailed')),
})

async function confirmDelete(cfg: RagConfig): Promise<void> {
  const ok = await confirm({
    title: t('agents.ragList.deleteTitle'),
    message: t('agents.ragList.deleteConfirm', { name: cfg.name }),
    variant: 'warning',
  })
  if (!ok) return
  deleteMutation.mutate(cfg.id)
}

function goToConfig(configId: string): void {
  router.push({
    name: 'agents.ragConfig',
    params: { projectId, configId },
  })
}

function onAction(key: string, row: RagConfig): void {
  if (key === 'edit') goToConfig(row.id)
  else if (key === 'delete') void confirmDelete(row)
}

function onRowClick(row: RagConfig): void {
  goToConfig(row.id)
}

const actionItems = computed(() => [
  { key: 'edit', label: t('common.edit', 'Edit'), icon: PencilSquareIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('common.delete', 'Delete'), icon: TrashIcon, danger: true },
])

const chunkStrategyOptions = computed(() => [
  { value: 'fixed', label: t('agents.ragForm.chunkFixed') },
  { value: 'semantic', label: t('agents.ragForm.chunkSemantic') },
])

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('agents.ragList.colName') },
  { key: 'chunk_strategy', label: t('agents.ragList.colStrategy'), width: '100px' },
  { key: 'embedding', label: t('agents.ragList.colEmbed'), width: '160px' },
  { key: 'top_k', label: t('agents.ragList.colTopK'), width: '70px' },
  { key: 'rerank_enabled', label: t('agents.ragList.colRerank'), width: '80px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.ragList.title')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <STooltip
          v-if="!hasEmbedKeys"
          :content="t('agents.ragList.noEmbedKeys')"
        >
          <SButton
            variant="primary"
            disabled
          >
            <template #icon-left>
              <PlusIcon class="w-4 h-4" />
            </template>
            {{ t('agents.ragList.create') }}
          </SButton>
        </STooltip>
        <SButton
          v-else
          variant="primary"
          @click="openCreateModal"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agents.ragList.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div class="mt-6">
      <SSearchInput
        v-model="search"
        :placeholder="t('agents.ragList.searchPlaceholder')"
        class="w-64"
      />
    </div>

    <STable
      :columns="columns"
      :data="paginatedItems"
      :loading="loading"
      row-key="id"
      class="mt-6"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <span class="font-medium cursor-pointer text-[var(--color-accent)]">
          {{ row.name }}
        </span>
      </template>

      <template #cell-chunk_strategy="{ row }">
        <SBadge variant="neutral">
          {{ row.chunk_strategy }}
        </SBadge>
      </template>

      <template #cell-embedding="{ row }">
        <span class="font-mono text-sm">{{ row.embed_provider }}/{{ row.embed_model }}</span>
      </template>

      <template #cell-top_k="{ row }">
        {{ row.top_k }}
      </template>

      <template #cell-rerank_enabled="{ row }">
        <SBadge
          v-if="row.rerank_enabled"
          variant="success"
        >
          {{ t('agents.ragList.rerankOn') }}
        </SBadge>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >--</span>
      </template>

      <template #actions="{ row }">
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
      </template>

      <template #empty>
        <SEmptyState
          :icon="DocumentTextIcon"
          :title="t('agents.ragList.emptyTitle')"
          :text="t('agents.ragList.emptyDescription')"
        >
          <template #action>
            <SButton
              v-if="hasEmbedKeys"
              variant="primary"
              @click="openCreateModal"
            >
              {{ t('agents.ragList.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <SPagination
      v-if="filteredConfigs.length > pageSize"
      :page="currentPage"
      :total-pages="totalPages"
      :total-items="filteredConfigs.length"
      :page-size="pageSize"
      class="mt-4"
      @update:page="currentPage = $event"
    />

    <!-- Create modal -->
    <SModal
      :open="showModal"
      :title="t('agents.ragList.create')"
      size="lg"
      @close="showModal = false"
    >
      <form
        @submit.prevent="onSubmit"
      >
        <SFormField
          :label="t('agents.ragForm.name')"
          name="name"
          :error="errors.name"
          required
        >
          <SInput
            v-model="name"
            :error="!!errors.name"
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.embedKey')"
          name="embed_key_id"
          :error="errors.embed_key_id"
          required
        >
          <SSelect
            v-model="embedKeyId"
            :options="embedKeyOptions"
            :placeholder="t('agents.ragForm.embedKeyPlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.embedModel')"
          name="embed_model"
          :error="errors.embed_model"
          required
        >
          <SInput
            v-model="embedModel"
            :placeholder="t('agents.ragForm.embedModelHint')"
            :error="!!errors.embed_model"
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.chunkStrategy')"
          name="chunk_strategy"
          :error="errors.chunk_strategy"
        >
          <SSelect
            v-model="chunkStrategy"
            :options="chunkStrategyOptions"
          />
        </SFormField>

        <template v-if="values.chunk_strategy === 'fixed'">
          <div class="grid grid-cols-2 gap-4">
            <SFormField
              :label="t('agents.ragForm.chunkSize')"
              name="chunk_size_tokens"
            >
              <SInput
                v-model="chunkSizeTokens"
                type="number"
              />
            </SFormField>
            <SFormField
              :label="t('agents.ragForm.chunkOverlap')"
              name="chunk_overlap_tokens"
            >
              <SInput
                v-model="chunkOverlapTokens"
                type="number"
              />
            </SFormField>
          </div>
        </template>
        <SFormField
          v-else
          :label="t('agents.ragForm.similarityThreshold')"
          name="similarity_threshold"
        >
          <SInput
            v-model="similarityThreshold"
            type="number"
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.topK')"
          name="top_k"
          :error="errors.top_k"
        >
          <SInput
            v-model="topK"
            type="number"
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.rerankEnabled')"
          name="rerank_enabled"
          :help="!hasRerankKeys ? t('agents.ragForm.noRerankKeys') : undefined"
        >
          <SToggle
            v-model="rerankEnabled"
            :disabled="!hasRerankKeys"
          />
        </SFormField>

        <template v-if="rerankEnabled">
          <SFormField
            :label="t('agents.ragForm.rerankKey')"
            name="rerank_key_id"
            :error="errors.rerank_key_id"
          >
            <SSelect
              v-model="rerankKeyId"
              :options="rerankKeyOptions"
              :placeholder="t('agents.ragForm.rerankKeyPlaceholder')"
            />
          </SFormField>
          <SFormField
            :label="t('agents.ragForm.rerankModel')"
            name="rerank_model"
            :error="errors.rerank_model"
          >
            <SInput v-model="rerankModel" />
          </SFormField>
        </template>
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showModal = false"
          >
            {{ t('agents.ragList.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="createMutation.isPending.value"
            :disabled="rerankIncomplete"
            @click="onSubmit"
          >
            {{ t('agents.ragForm.submit') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
