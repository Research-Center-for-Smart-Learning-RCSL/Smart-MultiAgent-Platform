<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  Cog6ToothIcon,
  DocumentIcon,
  TrashIcon,
  UserGroupIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  SCard,
  SFormField,
  SInput,
  SSelect,
  SToggle,
  SButton,
  STable,
  SBadge,
  SCheckbox,
  SModal,
  SFileUpload,
  SProgressBar,
  SAlert,
  SEmptyState,
  SSkeleton,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { tusUpload } from '@shared/transport'
import { projectKeysApi, CAPABILITIES, keysKeys } from '@slices/keys'
import {
  agentsApi,
  RAG_MULTIPART_MAX,
  type Agent,
  type RagConfig,
  type RagDocument,
  type RagConfigPatchInput,
} from '../api'
import { agentKeys } from '../queries'
import { ragConfigCreateSchema, type RagConfigCreateInput } from '../types/schemas'
import { useRagConfigSocket } from '../composables/useRagConfigSocket'
import { useRagConfigForm } from '../composables/useRagConfigForm'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const configId = route.params.configId as string
const toast = useToast()
const { confirm } = useConfirmDialog()
const { isMobile } = useBreakpoint()

const activeTab = ref((route.query.tab as string) || 'settings')

const { progress } = useRagConfigSocket(configId, projectId)

const configQuery = useQuery({
  queryKey: agentKeys.ragConfig(configId),
  queryFn: async () => (await agentsApi.getRagConfig(configId)).data,
})

const docsQuery = useQuery({
  queryKey: agentKeys.ragDocuments(configId),
  queryFn: async () => (await agentsApi.listDocuments(configId)).data,
})

const projectKeysQuery = useQuery({
  queryKey: keysKeys.projectKeys(projectId),
  queryFn: async () => (await projectKeysApi.listCarried(projectId)).data,
})

const config = computed<RagConfig | undefined>(() => configQuery.data.value)
const docs = computed<RagDocument[]>(() => docsQuery.data.value ?? [])
const configError = computed(() => configQuery.error.value)

// --- Per-agent document scoping ---
const agentsQuery = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => (await agentsApi.list(projectId)).data,
})

// Only agents bound to THIS config may appear on a document's allowlist.
const boundAgents = computed<Agent[]>(() =>
  (agentsQuery.data.value ?? []).filter((a) => a.rag_config_id === configId),
)
// Upload allowlist: default to every bound agent so a fresh upload is visible
// by default (the backend treats an empty allowlist as "no agent may see it").
// Seed ONCE when the bound agents first load — re-seeding on every refetch
// would silently discard the user's manual deselection before they upload.
const uploadAgentIds = ref<string[]>([])
const uploadAgentsSeeded = ref(false)
watch(
  boundAgents,
  (agents) => {
    if (!uploadAgentsSeeded.value && agents.length) {
      uploadAgentIds.value = agents.map((a) => a.id)
      uploadAgentsSeeded.value = true
    }
  },
  { immediate: true },
)
function toggleUploadAgent(id: string, on: boolean): void {
  uploadAgentIds.value = on
    ? [...new Set([...uploadAgentIds.value, id])]
    : uploadAgentIds.value.filter((x) => x !== id)
}

// --- Edit an existing document's allowlist ---
const editDoc = ref<RagDocument | null>(null)
const editAgentIds = ref<string[]>([])
function openAgentsEditor(doc: RagDocument): void {
  editDoc.value = doc
  editAgentIds.value = [...doc.agent_ids]
}
function toggleEditAgent(id: string, on: boolean): void {
  editAgentIds.value = on
    ? [...new Set([...editAgentIds.value, id])]
    : editAgentIds.value.filter((x) => x !== id)
}
const setAgentsMutation = useMutation({
  mutationFn: async () => {
    if (!editDoc.value) return
    await agentsApi.setDocumentAgents(editDoc.value.id, [...editAgentIds.value])
  },
  onSuccess: () => {
    editDoc.value = null
    toast.success(t('agents.rag.agentsSaved'))
    qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
  },
  onError: () => toast.error(t('agents.rag.agentsSaveFailed')),
})

const breadcrumbs = computed(() => [
  { label: t('agents.breadcrumb.ragConfigs'), to: { name: 'agents.ragConfigs', params: { projectId } } },
  { label: config.value?.name ?? '...' },
])

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

watch(
  () => progress.value.state,
  (state) => {
    if (state === 'ready' || state === 'failed') {
      qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
    }
  },
)

// --- Settings form ---
const formSchema = toTypedSchema(ragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, values } =
  useForm<RagConfigCreateInput>({ validationSchema: formSchema })

const [chunkStrategy] = defineField('chunk_strategy')
const [embedKeyId] = defineField('embed_key_id')
const [embedProvider] = defineField('embed_provider')
const [embedModel] = defineField('embed_model')
const [rerankEnabled] = defineField('rerank_enabled')
const [rerankKeyId] = defineField('rerank_key_id')
const [rerankModel] = defineField('rerank_model')
const [topK] = defineField('top_k')
const [rerankProvider] = defineField('rerank_provider')
defineField('name')

const {
  chunkSizeTokens,
  chunkOverlapTokens,
  similarityThreshold,
  embedKeyOptions,
  rerankKeyOptions,
  assembleChunkParams,
  loadChunkParams,
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

watch(
  () => configQuery.data.value,
  (cfg) => {
    if (!cfg) return
    resetForm({
      values: {
        name: cfg.name,
        chunk_strategy: cfg.chunk_strategy as 'fixed' | 'semantic',
        chunk_params: cfg.chunk_params,
        embed_key_id: cfg.embed_key_id ?? '',
        embed_provider: cfg.embed_provider as RagConfigCreateInput['embed_provider'],
        embed_model: cfg.embed_model,
        rerank_enabled: cfg.rerank_enabled,
        rerank_key_id: cfg.rerank_key_id,
        rerank_provider: (cfg.rerank_provider as 'cohere' | null) ?? null,
        rerank_model: cfg.rerank_model,
        top_k: cfg.top_k,
      },
    })
    loadChunkParams(cfg.chunk_params as Record<string, unknown>)
  },
  { immediate: true },
)

const { applyServerErrors } = useServerErrors(setErrors)

// Embedding (provider/model/key) and chunk strategy are immutable post-creation
// — an indexed corpus can't switch embedding space — so only the patchable
// fields are sent.
const saveMutation = useMutation({
  mutationFn: async (payload: RagConfigPatchInput) =>
    (await agentsApi.patchRagConfig(configId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.ragConfig(configId) })
    toast.success(t('agents.detail.saved'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.detail.saveFailed'))
  },
})

const onSaveSettings = handleSubmit((formValues) => {
  saveMutation.mutate({
    name: formValues.name,
    top_k: formValues.top_k,
    chunk_params: assembleChunkParams(formValues.chunk_strategy),
    rerank_enabled: formValues.rerank_enabled,
    rerank_key_id: formValues.rerank_key_id,
    rerank_provider: formValues.rerank_provider,
    rerank_model: formValues.rerank_model,
  })
})

const deleteConfigMutation = useMutation({
  mutationFn: () => agentsApi.deleteRagConfig(configId),
  onSuccess: () => {
    router.push({ name: 'agents.ragConfigs', params: { projectId } })
    toast.success(t('agents.ragList.deleted'))
  },
  onError: () => toast.error(t('agents.ragList.deleteFailed')),
})

async function onDeleteConfig(): Promise<void> {
  const ok = await confirm({
    title: t('agents.ragList.deleteTitle'),
    message: t('agents.ragList.deleteConfirm', { name: config.value?.name ?? '' }),
    variant: 'error',
  })
  if (!ok) return
  deleteConfigMutation.mutate()
}

// --- Document upload ---
const uploading = ref(false)

async function onFiles(files: File[]): Promise<void> {
  uploading.value = true
  const agentIds = [...uploadAgentIds.value]
  try {
    for (const file of files) {
      if (file.size <= RAG_MULTIPART_MAX) {
        await agentsApi.uploadDocumentMultipart(configId, file, agentIds)
      } else {
        // The allowlist rides in tus metadata so the finaliser applies it
        // atomically on the new document (no racy post-upload PATCH).
        await tusUpload({
          file,
          purpose: 'rag_source',
          projectId,
          ragConfigId: configId,
          ragAgentIds: agentIds,
        })
      }
    }
    toast.success(t('agents.rag.uploadStarted'))
    qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
  } catch {
    toast.error(t('agents.rag.uploadFailed'))
  } finally {
    uploading.value = false
  }
}

// --- Document delete ---
const deleteDocMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteDocument(id),
  onSuccess: () => {
    toast.success(t('agents.rag.deleted'))
    qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
  },
  onError: () => toast.error(t('agents.rag.deleteFailed')),
})

async function confirmDeleteDoc(doc: RagDocument): Promise<void> {
  const ok = await confirm({
    title: t('agents.rag.deleteTitle'),
    message: t('agents.rag.deleteConfirm', { name: doc.filename }),
    variant: 'warning',
  })
  if (!ok) return
  deleteDocMutation.mutate(doc.id)
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const statusVariant = (status: string): 'info' | 'success' | 'danger' | 'warning' => {
  const map: Record<string, 'info' | 'success' | 'danger' | 'warning'> = {
    ingesting: 'info', ready: 'success', failed: 'danger', quarantined: 'warning',
  }
  return map[status] ?? 'info'
}

const scanVariant = (status: string): 'neutral' | 'success' | 'danger' => {
  const map: Record<string, 'neutral' | 'success' | 'danger'> = {
    pending: 'neutral', clean: 'success', quarantined: 'danger', skipped: 'neutral',
  }
  return map[status] ?? 'neutral'
}

const tabs = computed(() => [
  { key: 'settings', label: t('agents.ragForm.tabs.settings'), icon: Cog6ToothIcon },
  {
    key: 'documents',
    label: t('agents.ragForm.tabs.documents'),
    icon: DocumentIcon,
    badge: docs.value.length > 0 ? String(docs.value.length) : undefined,
  },
])

function onTabChange(tab: string): void {
  activeTab.value = tab
  router.replace({ query: { ...route.query, tab } })
}

const chunkStrategyOptions = computed(() => [
  { value: 'fixed', label: t('agents.ragForm.chunkFixed') },
  { value: 'semantic', label: t('agents.ragForm.chunkSemantic') },
])

const docColumns = computed<Column[]>(() => [
  { key: 'filename', label: t('agents.rag.colName') },
  { key: 'size_bytes', label: t('agents.rag.colSize'), width: '80px' },
  { key: 'status', label: t('agents.rag.colStatus'), width: '100px' },
  { key: 'scan_status', label: t('agents.rag.colScanned'), width: '100px' },
  { key: 'agents', label: t('agents.rag.colAgents'), width: '140px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])

const progressText = computed(() => {
  const p = progress.value
  if (p.state === 'ingesting' && p.documentsTotal > 0) {
    return t('agents.rag.ingestionProgress', {
      processed: p.documentsProcessed,
      total: p.documentsTotal,
    })
  }
  if (p.state === 'indexing') return t('agents.rag.indexing')
  if (p.state === 'ingesting') return t('agents.rag.ingestionStarted')
  return ''
})

const progressValue = computed(() => {
  const p = progress.value
  if (p.state === 'ingesting' && p.documentsTotal > 0) {
    return Math.round((p.documentsProcessed / p.documentsTotal) * 100)
  }
  return 0
})

const showProgress = computed(() =>
  ['ingesting', 'indexing'].includes(progress.value.state),
)
</script>

<template>
  <main class="p-6">
    <template v-if="configQuery.isLoading.value">
      <SSkeleton width="200px" />
      <SSkeleton class="mt-4" />
      <SSkeleton class="mt-2" />
    </template>

    <SAlert
      v-else-if="configError"
      variant="danger"
      class="mt-4"
    >
      {{ t('agents.ragList.loadError') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="configQuery.refetch()"
        >
          {{ t('agents.detail.reload') }}
        </SButton>
      </template>
    </SAlert>

    <template v-else-if="config">
      <SPageHeader
        :title="config.name"
        :breadcrumbs="breadcrumbs"
      >
        <template #actions>
          <SButton
            variant="danger"
            @click="onDeleteConfig"
          >
            {{ t('agents.detail.delete') }}
          </SButton>
          <SButton
            v-if="activeTab === 'settings'"
            variant="primary"
            :loading="saveMutation.isPending.value"
            @click="onSaveSettings"
          >
            {{ t('agents.detail.save') }}
          </SButton>
        </template>
      </SPageHeader>

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

      <!-- Tab: Settings -->
      <div
        v-show="activeTab === 'settings'"
        role="tabpanel"
        id="tabpanel-settings"
        aria-labelledby="settings"
      >
          <form
            class="mt-6 space-y-6"
            @submit.prevent="onSaveSettings"
          >
            <SCard>
              <h3 class="text-lg font-semibold mb-4">
                {{ t('agents.ragForm.embedProvider') }}
              </h3>
              <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
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
                    disabled
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
                    disabled
                  />
                </SFormField>
              </div>
              <p class="text-sm text-[var(--color-muted)] mt-2">
                {{ t('agents.ragForm.immutableHint') }}
              </p>
            </SCard>

            <SCard>
              <h3 class="text-lg font-semibold mb-4">
                {{ t('agents.ragForm.chunkStrategy') }}
              </h3>
              <SFormField
                :label="t('agents.ragForm.chunkStrategy')"
                name="chunk_strategy"
              >
                <SSelect
                  v-model="chunkStrategy"
                  :options="chunkStrategyOptions"
                  disabled
                />
              </SFormField>
              <template v-if="values.chunk_strategy === 'fixed'">
                <div class="grid grid-cols-2 gap-4 mt-4">
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
                class="mt-4"
              >
                <SInput
                  v-model="similarityThreshold"
                  type="number"
                />
              </SFormField>
            </SCard>

            <SCard>
              <h3 class="text-lg font-semibold mb-4">
                {{ t('agents.ragForm.topK') }}
              </h3>
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
                class="mt-4"
              >
                <SToggle v-model="rerankEnabled" />
              </SFormField>

              <template v-if="rerankEnabled">
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
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
                </div>
              </template>
            </SCard>
          </form>
      </div>

      <!-- Tab: Documents -->
      <div
        v-show="activeTab === 'documents'"
        role="tabpanel"
        id="tabpanel-documents"
        aria-labelledby="documents"
      >
          <div class="mt-6 space-y-6">
            <SCard>
              <h3 class="text-lg font-semibold mb-4">
                {{ t('agents.rag.upload') }}
              </h3>
              <SFileUpload
                accept=".pdf,.txt,.md,.docx,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                :max-size="33554432"
                multiple
                :disabled="uploading"
                @files="onFiles"
              >
                <p class="text-sm text-[var(--color-muted)]">
                  {{ t('agents.rag.sizeHint') }}
                </p>
              </SFileUpload>

              <!-- Per-agent allowlist applied to uploads in this batch. -->
              <div class="mt-4">
                <p class="text-sm font-medium mb-1">
                  {{ t('agents.rag.visibleToAgents') }}
                </p>
                <p class="text-sm text-[var(--color-muted)] mb-2">
                  {{ t('agents.rag.visibleToAgentsHint') }}
                </p>
                <p
                  v-if="boundAgents.length === 0"
                  class="text-sm text-[var(--color-muted)]"
                >
                  {{ t('agents.rag.noBoundAgents') }}
                </p>
                <div
                  v-else
                  class="flex flex-col gap-1"
                >
                  <SCheckbox
                    v-for="agent in boundAgents"
                    :key="agent.id"
                    :model-value="uploadAgentIds.includes(agent.id)"
                    @update:model-value="toggleUploadAgent(agent.id, $event)"
                  >
                    {{ agent.name }}
                  </SCheckbox>
                </div>
              </div>
            </SCard>

            <SCard>
              <h3 class="text-lg font-semibold mb-4">
                {{ t('agents.ragForm.tabs.documents') }}
              </h3>

              <STable
                :columns="docColumns"
                :data="docs"
                :loading="docsQuery.isLoading.value"
                row-key="id"
              >
                <template #cell-size_bytes="{ row }">
                  {{ humanSize(row.size_bytes) }}
                </template>

                <template #cell-status="{ row }">
                  <SBadge :variant="statusVariant(row.status)">
                    {{ t(`agents.rag.status.${row.status}`) }}
                  </SBadge>
                </template>

                <template #cell-scan_status="{ row }">
                  <SBadge :variant="scanVariant(row.scan_status)">
                    {{ t(`agents.rag.scan.${row.scan_status}`) }}
                  </SBadge>
                </template>

                <template #cell-agents="{ row }">
                  <SButton
                    variant="ghost"
                    size="sm"
                    @click="openAgentsEditor(row)"
                  >
                    <template #icon-left>
                      <UserGroupIcon class="w-4 h-4" />
                    </template>
                    <span :class="{ 'text-[var(--color-warning)]': row.agent_ids.length === 0 }">
                      {{
                        row.agent_ids.length === 0
                          ? t('agents.rag.agentsNone')
                          : t('agents.rag.agentsCount', { count: row.agent_ids.length })
                      }}
                    </span>
                  </SButton>
                </template>

                <template #actions="{ row }">
                  <SButton
                    variant="ghost"
                    icon-only
                    size="sm"
                    @click="confirmDeleteDoc(row)"
                  >
                    <TrashIcon class="w-4 h-4 text-[var(--color-danger)]" />
                  </SButton>
                </template>

                <template #empty>
                  <SEmptyState
                    :icon="DocumentIcon"
                    :title="t('agents.rag.emptyTitle')"
                    :text="t('agents.rag.emptyDescription')"
                  />
                </template>
              </STable>

              <div
                v-if="showProgress"
                class="mt-4"
              >
                <SProgressBar
                  :value="progressValue"
                  :indeterminate="progress.state === 'indexing' || (progress.state === 'ingesting' && progress.documentsTotal === 0)"
                  variant="info"
                />
                <p class="text-sm text-[var(--color-muted)] mt-1">
                  {{ progressText }}
                </p>
              </div>
            </SCard>
          </div>
      </div>

      <!-- Edit a document's per-agent allowlist -->
      <SModal
        :open="editDoc !== null"
        :title="t('agents.rag.agentsModalTitle')"
        size="md"
        @close="editDoc = null"
      >
        <p class="text-sm text-[var(--color-muted)] mb-3">
          {{ t('agents.rag.visibleToAgentsHint') }}
        </p>
        <p
          v-if="boundAgents.length === 0"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('agents.rag.noBoundAgents') }}
        </p>
        <div
          v-else
          class="flex flex-col gap-1"
        >
          <SCheckbox
            v-for="agent in boundAgents"
            :key="agent.id"
            :model-value="editAgentIds.includes(agent.id)"
            @update:model-value="toggleEditAgent(agent.id, $event)"
          >
            {{ agent.name }}
          </SCheckbox>
        </div>

        <template #footer>
          <div class="flex justify-end gap-3">
            <SButton
              variant="secondary"
              @click="editDoc = null"
            >
              {{ t('agents.ragList.cancel') }}
            </SButton>
            <SButton
              variant="primary"
              :loading="setAgentsMutation.isPending.value"
              @click="setAgentsMutation.mutate()"
            >
              {{ t('agents.detail.save') }}
            </SButton>
          </div>
        </template>
      </SModal>
    </template>
  </main>
</template>
