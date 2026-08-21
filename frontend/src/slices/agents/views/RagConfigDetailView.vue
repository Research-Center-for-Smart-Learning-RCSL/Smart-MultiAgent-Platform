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
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  SSelect,
  SButton,
  SAlert,
  SSkeleton,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { projectKeysApi, CAPABILITIES, keysKeys } from '@slices/keys'
import {
  agentsApi,
  type RagConfig,
  type RagConfigPatchInput,
} from '../api'
import { agentKeys } from '../queries'
import { ragConfigCreateSchema, type RagConfigCreateInput } from '../types/schemas'
import { useRagConfigSocket } from '../composables/useRagConfigSocket'
import { useRagConfigForm } from '../composables/useRagConfigForm'
import { useRagDocuments } from '../composables/useRagDocuments'
import RagDocumentsTab from '../components/RagDocumentsTab.vue'
import RagSettingsTab from '../components/RagSettingsTab.vue'

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
  queryFn: () => agentsApi.getRagConfig(configId),
})

const projectKeysQuery = useQuery({
  queryKey: keysKeys.projectKeys(projectId),
  queryFn: () => projectKeysApi.listCarried(projectId),
})

const config = computed<RagConfig | undefined>(() => configQuery.data.value)
const configError = computed(() => configQuery.error.value)
const {
  boundAgents,
  confirmDeleteDoc,
  docs,
  docsQuery,
  editAgentIds,
  editDoc,
  onFiles,
  openAgentsEditor,
  setAgentsMutation,
  toggleEditAgent,
  toggleUploadAgent,
  uploadAgentIds,
  uploading,
} = useRagDocuments(configId, projectId)
// F-20 (R10.04): chunk params describe the whole corpus and cannot be re-tuned
// once documents exist (the backend rejects a changing patch with 409). Disable
// the inputs as a UX guard once the config has any document — like the already
// immutable chunk strategy / embedding model.
const chunkParamsLocked = computed(() => docs.value.length > 0)

// --- Per-agent document scoping ---
// Upload allowlist: default to every bound agent so a fresh upload is visible
// by default (the backend treats an empty allowlist as "no agent may see it").
// Seed ONCE when the bound agents first load — re-seeding on every refetch
// would silently discard the user's manual deselection before they upload.

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
const { handleSubmit, errors, defineField, resetForm, setErrors } =
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

// SInput's model-value accepts `string | number` (no `null`); rerank_model is
// nullable (unset when reranking is off). Bridge to '' for display only — SInput
// always emits a string for a text input, so the field keeps storing
// `string | null` exactly as before.
const rerankModelDisplay = computed<string | number>({
  get: () => rerankModel.value ?? '',
  set: (v) => {
    rerankModel.value = v === '' ? null : String(v)
  },
})

// F-19: BYO-key 'cohere' or the keyless bundled local 'bge'.
const rerankProviderOptions = computed(() => [
  { value: 'cohere', label: t('agents.ragForm.rerankProviderCohere') },
  { value: 'bge', label: t('agents.ragForm.rerankProviderBge') },
])

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
        rerank_provider: (cfg.rerank_provider as 'cohere' | 'bge' | null) ?? null,
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
  mutationFn: (payload: RagConfigPatchInput) =>
    agentsApi.patchRagConfig(configId, payload),
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

const tabs = computed(() => [
  { key: 'settings', label: t('agents.ragForm.tabs.settings'), icon: Cog6ToothIcon },
  {
    key: 'documents',
    label: t('agents.ragForm.tabs.documents'),
    icon: DocumentIcon,
    ...(docs.value.length > 0 && { badge: String(docs.value.length) }),
  },
])

// STabs emits `string | number` for model-value; tab keys here are always strings,
// so normalize defensively without changing behavior for existing callers.
function onTabChange(tab: string | number): void {
  const key = String(tab)
  activeTab.value = key
  router.replace({ query: { ...route.query, tab: key } })
}

</script>

<template>
  <div>
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

      <RagSettingsTab
        v-show="activeTab === 'settings'"
        v-model:embed-key-id="embedKeyId"
        v-model:embed-model="embedModel"
        v-model:chunk-strategy="chunkStrategy"
        v-model:chunk-size-tokens="chunkSizeTokens"
        v-model:chunk-overlap-tokens="chunkOverlapTokens"
        v-model:similarity-threshold="similarityThreshold"
        v-model:top-k="topK"
        v-model:rerank-enabled="rerankEnabled"
        v-model:rerank-provider="rerankProvider"
        v-model:rerank-key-id="rerankKeyId"
        v-model:rerank-model-display="rerankModelDisplay"
        :chunk-params-locked="chunkParamsLocked"
        :embed-key-options="embedKeyOptions"
        :errors="errors"
        :rerank-key-options="rerankKeyOptions"
        :rerank-provider-options="rerankProviderOptions"
        @submit="onSaveSettings"
      />

      <RagDocumentsTab
        v-show="activeTab === 'documents'"
        :bound-agents="boundAgents"
        :docs="docs"
        :edit-agent-ids="editAgentIds"
        :edit-doc="editDoc"
        :loading="docsQuery.isLoading.value"
        :progress="progress"
        :saving-agents="setAgentsMutation.isPending.value"
        :upload-agent-ids="uploadAgentIds"
        :uploading="uploading"
        @close-editor="editDoc = null"
        @delete-document="confirmDeleteDoc"
        @files="onFiles"
        @open-editor="openAgentsEditor"
        @save-agents="setAgentsMutation.mutate()"
        @toggle-edit-agent="toggleEditAgent"
        @toggle-upload-agent="toggleUploadAgent"
      />
    </template>
  </div>
</template>
