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
  ShareIcon,
  ArrowPathIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  SSelect,
  SButton,
  SBadge,
  SAlert,
  SSkeleton,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
  useBreakpoint,
} from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { useProjectRole } from '@slices/tenancy'
import {
  agentsApi,
  GRAPHRAG_IN_PROGRESS,
  type KnowmapConfig,
} from '../api'
import { agentKeys } from '../queries'
import {
  knowmapConfigCreateSchema,
  type KnowmapConfigCreateInput,
  type KnowmapConfigPatchInput,
} from '../types/schemas'
import { useKnowmapSocket } from '../composables/useKnowmapSocket'
import { useKnowmapDocuments } from '../composables/useKnowmapDocuments'
import KnowmapDocumentsTab from '../components/KnowmapDocumentsTab.vue'
import KnowmapSettingsTab from '../components/KnowmapSettingsTab.vue'
import { useChunkParamsForm } from '../composables/useChunkParamsForm'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'

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

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: () => keyGroupsApi.listForProject(projectId),
})

const config = computed<KnowmapConfig | undefined>(() => configQuery.data.value)
const configError = computed(() => configQuery.error.value)
// F-20 (R10.04): chunk params describe the whole corpus and cannot be re-tuned
// once documents exist (the backend rejects a changing patch with 409). Disable
// the inputs as a UX guard once the config has any document.
const chunkParamsLocked = computed(() => docs.value.length > 0)

const effectiveState = computed(() => liveState.value[configId] ?? config.value?.last_build_state ?? 'idle')
const isBuilding = computed(() => GRAPHRAG_IN_PROGRESS.has(effectiveState.value))
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
} = useKnowmapDocuments(configId, projectId, effectiveState, watchBuild)

// F-22: subscribe to build state unconditionally once the config loads (not
// gated on an already-in-progress state), so an automatic rebuild triggered by
// an upload/delete while the page shows idle still delivers its `running` frame
// to a live subscriber. A defined initial state seeds the engine so the backstop
// poll can engage once it becomes in-progress; watch() is idempotent.
watch(
  config,
  (cfg) => {
    if (cfg) {
      watchBuild(configId, cfg.last_build_state ?? 'idle')
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
const { handleSubmit, errors, defineField, resetForm, setErrors } =
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
  onSuccess: (result) => {
    qc.invalidateQueries({ queryKey: agentKeys.knowmapConfig(configId) })
    // F-14 (R11.25): a builder-group change that collided with attached agents'
    // consumer group auto-detaches them. Tell the designer so they can re-attach
    // with a compatible group, rather than the change appearing to silently succeed.
    const detachedCount = result.detached_agent_ids?.length ?? 0
    if (detachedCount > 0) {
      toast.warning(t('agents.knowmapDetail.agentsDetached', { count: detachedCount }))
    } else {
      toast.success(t('agents.detail.saved'))
    }
  },
  onError: (err) => {
    // F-13: a builder-group swap to a different embedding model on a config that
    // already holds indexed vectors is rejected (409) — surface the actionable
    // clear-and-recreate guidance rather than the generic save-failed toast.
    if (isProblemWithType(err, '/knowmap-embedding-model-change-blocked')) {
      toast.error(t('agents.knowmapDetail.embedModelChangeBlocked'))
      return
    }
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

      <KnowmapSettingsTab
        v-show="activeTab === 'settings'"
        v-model:name="name"
        v-model:builder-key-group-id="builderKeyGroupId"
        v-model:chunk-strategy="chunkStrategy"
        v-model:chunk-size-tokens="chunkSizeTokens"
        v-model:chunk-overlap-tokens="chunkOverlapTokens"
        v-model:similarity-threshold="similarityThreshold"
        :chunk-params-locked="chunkParamsLocked"
        :embed-model="config.embed_model"
        :embed-provider="config.embed_provider"
        :errors="errors"
        :has-key-groups="hasKeyGroups"
        :key-group-options="keyGroupOptions"
        @submit="onSaveSettings"
      />

      <KnowmapDocumentsTab
        v-show="activeTab === 'documents'"
        :authorized="decided && isAuthorized"
        :bound-agents="boundAgents"
        :docs="docs"
        :edit-agent-ids="editAgentIds"
        :edit-doc="editDoc"
        :loading="docsQuery.isLoading.value"
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
