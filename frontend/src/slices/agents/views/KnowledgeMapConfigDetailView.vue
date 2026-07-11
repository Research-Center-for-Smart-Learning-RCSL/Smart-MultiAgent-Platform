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
  ShareIcon,
  ArrowPathIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  SCard,
  SFormField,
  SInput,
  SSelect,
  SButton,
  STable,
  SBadge,
  SCheckbox,
  SModal,
  SFileUpload,
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
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { useProjectRole } from '@slices/tenancy'
import {
  agentsApi,
  RAG_MULTIPART_MAX,
  GRAPHRAG_IN_PROGRESS,
  type Agent,
  type KnowmapConfig,
  type KnowmapDocument,
} from '../api'
import { agentKeys } from '../queries'
import {
  knowmapConfigCreateSchema,
  type KnowmapConfigCreateInput,
  type KnowmapConfigPatchInput,
} from '../types/schemas'
import { useKnowmapSocket } from '../composables/useKnowmapSocket'
import { useChunkParamsForm } from '../composables/useChunkParamsForm'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'
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

// --- Owner gating (R11.23 upload/allowlist surface; not the config CRUD
// surface, which only needs RESOURCE_CREATE_EDIT on the backend — mirrors
// knowmap.py's own _require_owner scope, not file-RAG's ungated debt (FU-4
// of the Phase 3β/4β spec: RagConfigDetailView is not owner-gated; do not
// imitate that here). `decided` guards against a flash-shown-then-hidden
// control before the role resolves.
const { isAuthorized, decided } = useProjectRole(projectId)

// --- Live build status ---
const { liveState, watch: watchBuild, unwatch: unwatchBuild } = useKnowmapSocket(projectId)

const configQuery = useQuery({
  queryKey: agentKeys.knowmapConfig(configId),
  queryFn: () => agentsApi.getKnowmapConfig(configId),
})

const docsQuery = useQuery({
  queryKey: agentKeys.knowmapDocuments(configId),
  queryFn: () => agentsApi.listKnowmapDocuments(configId),
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: () => keyGroupsApi.listForProject(projectId),
})

const config = computed<KnowmapConfig | undefined>(() => configQuery.data.value)
const docs = computed<KnowmapDocument[]>(() => docsQuery.data.value ?? [])
const configError = computed(() => configQuery.error.value)

const effectiveState = computed(() => liveState.value[configId] ?? config.value?.last_build_state ?? 'idle')
const isBuilding = computed(() => GRAPHRAG_IN_PROGRESS.has(effectiveState.value))

watch(
  config,
  (cfg) => {
    if (cfg && GRAPHRAG_IN_PROGRESS.has(cfg.last_build_state)) {
      watchBuild(configId, cfg.last_build_state)
    }
  },
  { immediate: true },
)

watch(effectiveState, (state) => {
  if (!GRAPHRAG_IN_PROGRESS.has(state)) {
    qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
  }
})

const buildMutation = useMutation({
  mutationFn: () => agentsApi.rebuildKnowmap(configId),
  onSuccess: () => toast.success(t('agents.knowmapDetail.buildStarted')),
  onError: () => {
    // The trigger never reached the server — clear the optimistic 'running'
    // state startBuild() seeded, or the badge would stay stuck on "Running"
    // even though no build actually started.
    const next = { ...liveState.value }
    delete next[configId]
    liveState.value = next
    unwatchBuild(configId)
    toast.error(t('agents.knowmapDetail.buildFailed'))
  },
})

function startBuild(): void {
  watchBuild(configId, 'running')
  buildMutation.mutate()
}

function openGraph(): void {
  void router.push({ name: 'agents.knowmapGraph', params: { projectId, configId } })
}

// --- Per-agent document scoping (R11.23) ---
const agentsQuery = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: () => agentsApi.list(projectId),
})

// Only agents bound to THIS config may appear on a document's allowlist.
const boundAgents = computed<Agent[]>(() =>
  (agentsQuery.data.value ?? []).filter((a) => a.knowmap_config_id === configId),
)
// Upload allowlist: default to every bound agent so a fresh upload is visible
// by default (the backend treats an empty allowlist as "no agent may see it").
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
const editDoc = ref<KnowmapDocument | null>(null)
const editAgentIds = ref<string[]>([])
function openAgentsEditor(doc: KnowmapDocument): void {
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
    await agentsApi.setKnowmapDocumentAgents(editDoc.value.id, [...editAgentIds.value])
  },
  onSuccess: () => {
    editDoc.value = null
    toast.success(t('agents.knowmap.agentsSaved'))
    qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
  },
  onError: () => toast.error(t('agents.knowmap.agentsSaveFailed')),
})

const breadcrumbs = computed(() => [
  { label: t('agents.breadcrumb.knowledgeMaps'), to: { name: 'agents.knowmapConfigs', params: { projectId } } },
  { label: config.value?.name ?? '...' },
])

const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)
const keyGroupOptions = computed(() =>
  (keyGroupsQuery.data.value ?? []).map((g) => ({ value: g.id, label: g.name })),
)

// --- Settings form ---
const formSchema = toTypedSchema(knowmapConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, values } =
  useForm<KnowmapConfigCreateInput>({ validationSchema: formSchema })

const [name] = defineField('name')
const [builderKeyGroupId] = defineField('builder_key_group_id')
const [chunkStrategy] = defineField('chunk_strategy')

const {
  chunkSizeTokens,
  chunkOverlapTokens,
  similarityThreshold,
  assembleChunkParams,
  loadChunkParams,
} = useChunkParamsForm()

watch(
  () => configQuery.data.value,
  (cfg) => {
    if (!cfg) return
    resetForm({
      values: {
        name: cfg.name,
        builder_key_group_id: cfg.builder_key_group_id,
        chunk_strategy: cfg.chunk_strategy as 'fixed' | 'semantic',
        chunk_params: cfg.chunk_params,
      },
    })
    loadChunkParams(cfg.chunk_params as Record<string, unknown>)
  },
  { immediate: true },
)

const { applyServerErrors } = useServerErrors(setErrors)

// chunk_strategy is immutable post-creation — only the patchable fields are sent.
const saveMutation = useMutation({
  mutationFn: (payload: KnowmapConfigPatchInput) =>
    agentsApi.patchKnowmapConfig(configId, payload),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.knowmapConfig(configId) })
    toast.success(t('agents.detail.saved'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.detail.saveFailed'))
  },
})

const onSaveSettings = handleSubmit((formValues) => {
  saveMutation.mutate({
    name: formValues.name,
    builder_key_group_id: formValues.builder_key_group_id,
    chunk_params: assembleChunkParams(formValues.chunk_strategy),
  })
})

const deleteConfigMutation = useMutation({
  mutationFn: () => agentsApi.deleteKnowmapConfig(configId),
  onSuccess: () => {
    router.push({ name: 'agents.knowmapConfigs', params: { projectId } })
    toast.success(t('agents.knowmapList.deleted'))
  },
  onError: () => toast.error(t('agents.knowmapList.deleteFailed')),
})

async function onDeleteConfig(): Promise<void> {
  const ok = await confirm({
    title: t('agents.knowmapList.deleteTitle'),
    message: t('agents.knowmapList.deleteConfirm', { name: config.value?.name ?? '' }),
    variant: 'error',
  })
  if (!ok) return
  deleteConfigMutation.mutate()
}

// --- Document upload (Owner-gated, R11.23/SEC) ---
const uploading = ref(false)

async function onFiles(files: File[]): Promise<void> {
  uploading.value = true
  const agentIds = [...uploadAgentIds.value]
  try {
    for (const file of files) {
      if (file.size <= RAG_MULTIPART_MAX) {
        await agentsApi.uploadKnowmapDocumentMultipart(configId, file, agentIds)
      } else {
        // The allowlist rides in tus metadata so the finaliser applies it
        // atomically on the new document (no racy post-upload PATCH).
        await tusUpload({
          file,
          purpose: 'knowmap_source',
          projectId,
          knowmapConfigId: configId,
          knowmapAgentIds: agentIds,
        })
      }
    }
    toast.success(t('agents.knowmap.uploadStarted'))
    qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
  } catch {
    toast.error(t('agents.knowmap.uploadFailed'))
  } finally {
    uploading.value = false
  }
}

// --- Document delete ---
const deleteDocMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteKnowmapDocument(id),
  onSuccess: () => {
    toast.success(t('agents.knowmap.deleted'))
    qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
  },
  onError: () => toast.error(t('agents.knowmap.deleteFailed')),
})

async function confirmDeleteDoc(doc: KnowmapDocument): Promise<void> {
  const ok = await confirm({
    title: t('agents.knowmap.deleteTitle'),
    message: t('agents.knowmap.deleteConfirm', { name: doc.filename }),
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
    ...(docs.value.length > 0 && { badge: String(docs.value.length) }),
  },
])

function onTabChange(tab: string | number): void {
  const key = String(tab)
  activeTab.value = key
  router.replace({ query: { ...route.query, tab: key } })
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

const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
function typedSTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & { data?: T[] }
    $slots: {
      [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown
    }
  }
}
const DocsTable = typedSTable<KnowmapDocument>()
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
      {{ t('agents.knowmapList.loadError') }}
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
            variant="secondary"
            @click="openGraph"
          >
            <template #icon-left>
              <ShareIcon class="w-4 h-4" />
            </template>
            {{ t('agents.knowmapDetail.viewGraph') }}
          </SButton>
          <SButton
            variant="secondary"
            :loading="buildMutation.isPending.value || isBuilding"
            :disabled="isBuilding"
            @click="startBuild"
          >
            <template #icon-left>
              <ArrowPathIcon class="w-4 h-4" />
            </template>
            {{ t('agents.knowmapDetail.rebuild') }}
          </SButton>
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

      <SBadge
        :variant="graphragBuildStateVariant(effectiveState)"
        class="mt-3"
      >
        {{ t(graphragBuildStateLabelKey(effectiveState)) }}
      </SBadge>
      <span
        v-if="effectiveState === 'failed' && config.last_build_error"
        class="ml-2 text-sm text-[var(--color-danger)]"
      >{{ config.last_build_error }}</span>

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
        id="tabpanel-settings"
        role="tabpanel"
        aria-labelledby="settings"
      >
        <form
          class="mt-6 space-y-6"
          @submit.prevent="onSaveSettings"
        >
          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.knowmapForm.name') }}
            </h3>
            <SFormField
              :label="t('agents.knowmapForm.name')"
              name="name"
              :error="errors.name ?? ''"
              required
            >
              <SInput
                v-model="name"
                :error="!!errors.name"
              />
            </SFormField>
          </SCard>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.knowmapForm.builderKeyGroup') }}
            </h3>
            <SFormField
              :label="t('agents.knowmapForm.builderKeyGroup')"
              name="builder_key_group_id"
              :error="errors.builder_key_group_id ?? ''"
              required
            >
              <SSelect
                v-model="builderKeyGroupId"
                :options="keyGroupOptions"
                :placeholder="t('agents.knowmapForm.builderKeyGroupPlaceholder')"
                :disabled="!hasKeyGroups"
              />
            </SFormField>
            <p
              v-if="config.embed_provider && config.embed_model"
              class="text-sm text-[var(--color-muted)] mt-2"
            >
              {{ t('agents.knowmapForm.embedResolved', { provider: config.embed_provider, model: config.embed_model }) }}
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
            <p class="text-sm text-[var(--color-muted)] mt-2">
              {{ t('agents.ragForm.immutableHint') }}
            </p>
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
        </form>
      </div>

      <!-- Tab: Documents -->
      <div
        v-show="activeTab === 'documents'"
        id="tabpanel-documents"
        role="tabpanel"
        aria-labelledby="documents"
      >
        <div class="mt-6 space-y-6">
          <SCard v-if="decided && isAuthorized">
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.knowmap.upload') }}
            </h3>
            <SFileUpload
              accept=".pdf,.txt,.md,.docx,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              :max-size="33554432"
              multiple
              :disabled="uploading"
              @files="onFiles"
            >
              <p class="text-sm text-[var(--color-muted)]">
                {{ t('agents.knowmap.sizeHint') }}
              </p>
            </SFileUpload>

            <!-- Per-agent allowlist applied to uploads in this batch (R11.23). -->
            <div class="mt-4">
              <p class="text-sm font-medium mb-1">
                {{ t('agents.knowmap.visibleToAgents') }}
              </p>
              <p class="text-sm text-[var(--color-muted)] mb-2">
                {{ t('agents.knowmap.visibleToAgentsHint') }}
              </p>
              <p
                v-if="boundAgents.length === 0"
                class="text-sm text-[var(--color-muted)]"
              >
                {{ t('agents.knowmap.noBoundAgents') }}
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

          <p
            v-else-if="decided && !isAuthorized"
            class="text-sm text-[var(--color-muted)]"
          >
            {{ t('agents.knowmap.ownerRequired') }}
          </p>

          <SCard>
            <h3 class="text-lg font-semibold mb-4">
              {{ t('agents.ragForm.tabs.documents') }}
            </h3>

            <DocsTable
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
                  :disabled="!(decided && isAuthorized)"
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
                  v-if="decided && isAuthorized"
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
                  :title="t('agents.knowmap.emptyTitle')"
                  :text="t('agents.knowmap.emptyDescription')"
                />
              </template>
            </DocsTable>
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
          {{ t('agents.knowmap.visibleToAgentsHint') }}
        </p>
        <p
          v-if="boundAgents.length === 0"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('agents.knowmap.noBoundAgents') }}
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
