<script setup lang="ts">
// Shared contextual Concept Map panel (Phase 4α WS3/WS4). Rendered inside each
// owner's settings (agent_group / chatroom / workspace) to show and manage THAT
// owner's Concept Map: build state, build/rebuild, graph link, the recency
// half-life (R11.21), and create-if-missing. Owner-gated via useProjectRole. The
// privacy opt-in (concept_map_enabled) is NOT here — it hits owner-slice
// endpoints this (agents) slice can't reach, so each parent renders it.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { SCard, SBadge, SButton, SFormField, SInput, SSelect, SLoadingSpinner } from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { agentsApi, GRAPHRAG_IN_PROGRESS, type GraphragConfig, type ConceptMapOwnerKind } from '../api'
import { agentKeys } from '../queries'
import { useGraphragSocket } from '../composables/useGraphragSocket'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'

const props = defineProps<{
  projectId: string
  ownerKind: ConceptMapOwnerKind
  ownerId: string
}>()

const { t } = useI18n()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const { isAuthorized, decided } = useProjectRole(() => props.projectId)

const configsQuery = useQuery({
  queryKey: computed(() => agentKeys.graphragConfigs(props.projectId)),
  enabled: computed(() => !!props.projectId),
  queryFn: () => agentsApi.listGraphragConfigs(props.projectId),
})

const map = computed<GraphragConfig | null>(
  () =>
    configsQuery.data.value?.find(
      (c) => c.owner_kind === props.ownerKind && c.owner_id === props.ownerId,
    ) ?? null,
)

// --- Live build state ---
const { liveState, watch: watchBuild, unwatch: unwatchBuild } = useGraphragSocket(props.projectId)
const effectiveState = computed(() =>
  map.value ? (liveState.value[map.value.id] ?? map.value.last_build_state) : 'idle',
)
const isBuilding = computed(() => GRAPHRAG_IN_PROGRESS.has(effectiveState.value))
watch(
  map,
  (m) => {
    if (m && GRAPHRAG_IN_PROGRESS.has(m.last_build_state)) watchBuild(m.id, m.last_build_state)
  },
  { immediate: true },
)

const buildMutation = useMutation({
  mutationFn: (id: string) => agentsApi.buildGraphrag(id),
  onSuccess: () => toast.success(t('agents.graphragList.buildStarted')),
  onError: (_err, id) => {
    const next = { ...liveState.value }
    delete next[id]
    liveState.value = next
    unwatchBuild(id)
    toast.error(t('agents.graphragList.buildFailed'))
  },
})
function startBuild(): void {
  if (!map.value) return
  const id = map.value.id
  liveState.value = { ...liveState.value, [id]: 'running' }
  watchBuild(id, 'running')
  buildMutation.mutate(id)
}

function openGraph(): void {
  if (!map.value) return
  void router.push({
    name: 'agents.graphragGraph',
    params: { projectId: props.projectId, configId: map.value.id },
  })
}

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '--'
}

// --- Recency half-life edit (R11.21, WS4 temporal) ---
const recencyDraft = ref<string | number>('')
watch(
  map,
  (m) => {
    recencyDraft.value = m?.recency_half_life_days ?? ''
  },
  { immediate: true },
)
const recencyDirty = computed(() => {
  const current = map.value?.recency_half_life_days ?? ''
  const draft = recencyDraft.value === '' ? '' : Number(recencyDraft.value)
  return String(draft) !== String(current)
})
const patchMutation = useMutation({
  mutationFn: async (recency: number | null) => {
    if (!map.value) return
    await agentsApi.patchGraphragConfig(map.value.id, { recency_half_life_days: recency })
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(props.projectId) })
    toast.success(t('agents.conceptMapPanel.recencySaved'))
  },
  onError: () => toast.error(t('agents.conceptMapPanel.recencySaveFailed')),
})
function saveRecency(): void {
  patchMutation.mutate(recencyDraft.value === '' ? null : Number(recencyDraft.value))
}

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteGraphragConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(props.projectId) })
    qc.invalidateQueries({ queryKey: agentKeys.graphragOwnerOptions(props.projectId) })
    toast.success(t('agents.graphragList.deleted'))
  },
  onError: () => toast.error(t('agents.graphragList.deleteFailed')),
})
async function confirmDelete(): Promise<void> {
  if (!map.value) return
  const ok = await confirm({
    title: t('agents.graphragList.deleteTitle'),
    message: t('agents.conceptMapPanel.deleteConfirm'),
    variant: 'error',
  })
  if (ok) deleteMutation.mutate(map.value.id)
}

// --- Create (owner, when no map yet) ---
const keyGroupsQuery = useQuery({
  queryKey: computed(() => keysKeys.keyGroups(props.projectId)),
  enabled: computed(() => !!props.projectId && isAuthorized.value && !map.value),
  queryFn: () => keyGroupsApi.listForProject(props.projectId),
})
const keyGroupOptions = computed(() =>
  (keyGroupsQuery.data.value ?? []).map((g) => ({ value: g.id, label: g.name })),
)
const createBuilder = ref<string | null>(null)
const createRecency = ref<string | number>('')
const createServerError = ref('')
const { applyServerErrors } = useServerErrors((fields) => {
  createServerError.value = Object.values(fields)[0] ?? ''
})
const createMutation = useMutation({
  mutationFn: async () =>
    (
      await agentsApi.createGraphragConfig(props.projectId, {
        owner_kind: props.ownerKind,
        owner_id: props.ownerId,
        builder_key_group_id: createBuilder.value ?? '',
        trigger_config: {},
        recency_half_life_days: createRecency.value === '' ? null : Number(createRecency.value),
      })
    ),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(props.projectId) })
    qc.invalidateQueries({ queryKey: agentKeys.graphragOwnerOptions(props.projectId) })
    createBuilder.value = null
    createRecency.value = ''
    createServerError.value = ''
    toast.success(t('agents.graphragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.graphragList.createFailed'))
  },
})
const canCreate = computed(() => isAuthorized.value && !!createBuilder.value)
</script>

<template>
  <SCard>
    <h3 class="text-lg font-semibold mb-1">
      {{ t('agents.conceptMapPanel.title') }}
    </h3>
    <p class="text-sm text-[var(--color-muted)] mb-4">
      {{ t('agents.conceptMapPanel.subtitle') }}
    </p>

    <div
      v-if="configsQuery.isLoading.value"
      class="flex justify-center py-6"
    >
      <SLoadingSpinner />
    </div>

    <!-- Existing map -->
    <template v-else-if="map">
      <div class="flex items-center gap-3 flex-wrap">
        <SBadge
          :variant="graphragBuildStateVariant(effectiveState)"
          :dot="isBuilding"
        >
          {{ t(graphragBuildStateLabelKey(effectiveState)) }}
        </SBadge>
        <span class="text-sm text-[var(--color-muted)]">
          {{ t('agents.graphragList.colLastBuilt') }}: {{ formatDate(map.last_build_at) }}
        </span>
      </div>
      <p
        v-if="map.last_build_error"
        class="text-sm text-[var(--color-danger)] mt-2"
      >
        {{ map.last_build_error }}
      </p>

      <div class="flex items-center gap-2 mt-4 flex-wrap">
        <SButton
          variant="primary"
          size="sm"
          :disabled="isBuilding || !isAuthorized"
          @click="startBuild"
        >
          {{ t('agents.graphragList.build') }}
        </SButton>
        <SButton
          variant="secondary"
          size="sm"
          @click="openGraph"
        >
          {{ t('agents.graphragList.viewGraph') }}
        </SButton>
        <SButton
          variant="ghost"
          size="sm"
          :disabled="!isAuthorized"
          @click="confirmDelete"
        >
          {{ t('agents.graphragList.delete') }}
        </SButton>
      </div>

      <!-- Temporal: recency half-life (R11.21) -->
      <div class="mt-5">
        <SFormField
          :label="t('agents.conceptMaps.recencyHalfLife')"
          name="recency_half_life_days"
          :help="t('agents.conceptMaps.recencyHelp')"
        >
          <div class="flex items-center gap-2">
            <SInput
              v-model="recencyDraft"
              type="number"
              min="1"
              :disabled="!isAuthorized"
              class="max-w-[160px]"
            />
            <SButton
              variant="secondary"
              size="sm"
              :disabled="!recencyDirty || !isAuthorized"
              :loading="patchMutation.isPending.value"
              @click="saveRecency"
            >
              {{ t('agents.conceptMapPanel.save') }}
            </SButton>
          </div>
        </SFormField>
      </div>
    </template>

    <!-- No map: an authorized user (owner or admin) can create; a non-owner sees a note -->
    <template v-else>
      <template v-if="isAuthorized">
        <p class="text-sm text-[var(--color-muted)] mb-3">
          {{ t('agents.conceptMapPanel.noMapOwner') }}
        </p>
        <SFormField
          :label="t('agents.graphragForm.builderKeyGroup')"
          name="builder_key_group_id"
          :error="createServerError"
          :help="t('agents.graphragList.builderHint')"
          required
        >
          <SSelect
            v-model="createBuilder"
            :options="keyGroupOptions"
            :placeholder="t('agents.graphragForm.builderKeyGroupPlaceholder')"
          />
        </SFormField>
        <SFormField
          :label="t('agents.conceptMaps.recencyHalfLife')"
          name="create_recency"
          :help="t('agents.conceptMaps.recencyHelp')"
        >
          <SInput
            v-model="createRecency"
            type="number"
            min="1"
            class="max-w-[160px]"
          />
        </SFormField>
        <SButton
          variant="primary"
          size="sm"
          class="mt-2"
          :disabled="!canCreate"
          :loading="createMutation.isPending.value"
          @click="createMutation.mutate()"
        >
          {{ t('agents.conceptMapPanel.create') }}
        </SButton>
      </template>
      <p
        v-else-if="decided"
        class="text-sm text-[var(--color-muted)]"
      >
        {{ t('agents.conceptMapPanel.noMapReadonly') }}
      </p>
    </template>
  </SCard>
</template>
