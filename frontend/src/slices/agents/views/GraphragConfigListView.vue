<script setup lang="ts">
// Concept Maps overview (Phase 4α, R11.09 / Q-2). The project-scoped central list
// of Concept Maps across all three owner layers (agent_group / chatroom /
// workspace), with an owner-type filter, live build-state pills, create (owner
// picker), build/rebuild, status, graph, and delete. The per-owner contextual
// panels live in each owner's settings; this is the manage/status hub.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import {
  PlusIcon,
  TrashIcon,
  EyeIcon,
  CircleStackIcon,
  EllipsisVerticalIcon,
  ShareIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SBadge,
  SButton,
  SDropdown,
  SModal,
  SDrawer,
  SFormField,
  SInput,
  SSelect,
  SToggle,
  SAccordion,
  SEmptyState,
  SAlert,
  SLoadingSpinner,
  SPagination,
} from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import {
  agentsApi,
  GRAPHRAG_IN_PROGRESS,
  type GraphragConfig,
  type GraphragStatus,
  type ConceptMapOwnerKind,
} from '../api'
import { agentKeys } from '../queries'
import { useProjectBreadcrumbs } from '../composables/useProjectBreadcrumbs'
import { useGraphragSocket } from '../composables/useGraphragSocket'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'
import {
  graphragConfigCreateSchema,
  CONCEPT_MAP_OWNER_KINDS,
  type GraphragConfigCreateInput,
} from '../types/schemas'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()
const { confirm } = useConfirmDialog()

const { breadcrumbs } = useProjectBreadcrumbs(projectId, [
  { label: t('agents.conceptMaps.title') },
])

const showCreateModal = ref(false)
const showStatusDrawer = ref(false)
const statusDrawerConfig = ref<GraphragConfig | null>(null)
const drawerStatus = ref<GraphragStatus | null>(null)
const drawerLoading = ref(false)
const drawerError = ref(false)

const configsQuery = useQuery({
  queryKey: agentKeys.graphragConfigs(projectId),
  queryFn: async () => (await agentsApi.listGraphragConfigs(projectId)).data,
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: async () => (await keyGroupsApi.listForProject(projectId)).data,
})

const ownerOptionsQuery = useQuery({
  queryKey: agentKeys.graphragOwnerOptions(projectId),
  queryFn: async () => (await agentsApi.listConceptMapOwnerOptions(projectId)).data,
})

const configs = computed<GraphragConfig[]>(() => configsQuery.data.value ?? [])

// --- Owner-type filter + pagination ---
const ownerKindFilter = ref<'' | ConceptMapOwnerKind>('')
const page = ref(1)
const PAGE_SIZE = 10

const filteredConfigs = computed(() =>
  ownerKindFilter.value ? configs.value.filter((c) => c.owner_kind === ownerKindFilter.value) : configs.value,
)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredConfigs.value.length / PAGE_SIZE)))
const pagedConfigs = computed(() =>
  filteredConfigs.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
// Reset to page 1 whenever the owner-type filter changes — otherwise the user
// stays on, say, page 3 showing an unrelated slice of the newly filtered set.
watch(ownerKindFilter, () => {
  page.value = 1
})
// Separately, clamp back down if the (possibly still-unfiltered) list shrinks
// past the current page — e.g. a delete drops the total page count.
watch(totalPages, () => {
  if (page.value > totalPages.value) page.value = totalPages.value
})

const ownerKindLabel = (kind: ConceptMapOwnerKind): string => t(`agents.conceptMaps.ownerKinds.${kind}`)
const ownerKindVariant = (kind: ConceptMapOwnerKind): 'info' | 'success' | 'neutral' =>
  ({ chatroom: 'info', agent_group: 'success', workspace: 'neutral' } as const)[kind]

const ownerFilterOptions = computed(() => [
  { value: '', label: t('agents.conceptMaps.filterAll') },
  ...CONCEPT_MAP_OWNER_KINDS.map((k) => ({ value: k, label: ownerKindLabel(k) })),
])

// --- Build state management (live via WebSocket) ---
const { liveState, watch: watchBuild, unwatch: unwatchBuild } = useGraphragSocket(projectId)

const effectiveState = (cfg: GraphragConfig): GraphragConfig['last_build_state'] =>
  liveState.value[cfg.id] ?? cfg.last_build_state
const isBuilding = (cfg: GraphragConfig): boolean => GRAPHRAG_IN_PROGRESS.has(effectiveState(cfg))

watch(
  configs,
  (list) => {
    for (const cfg of list) {
      if (GRAPHRAG_IN_PROGRESS.has(cfg.last_build_state)) watchBuild(cfg.id, cfg.last_build_state)
    }
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

function startBuild(id: string): void {
  liveState.value = { ...liveState.value, [id]: 'running' }
  watchBuild(id, 'running')
  buildMutation.mutate(id)
}

function openGraph(cfg: GraphragConfig): void {
  void router.push({ name: 'agents.graphragGraph', params: { projectId, configId: cfg.id } })
}

// --- Create form ---
const schema = toTypedSchema(graphragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<GraphragConfigCreateInput>({
  validationSchema: schema,
  initialValues: {
    owner_kind: 'agent_group',
    owner_id: '',
    builder_key_group_id: '',
    trigger_config: {},
    recency_half_life_days: null,
  },
})

const [ownerKind] = defineField('owner_kind')
const [ownerId] = defineField('owner_id')
const [builderKeyGroupId] = defineField('builder_key_group_id')

const triggerEveryN = ref<number | null>(null)
const triggerSilence = ref<number | null>(null)
const triggerManual = ref(false)
const recencyHalfLife = ref<number | null>(null)

// SInput's model-value is `string | number` (no null); bridge nullable numbers to
// '' for display. SInput type=number emits a number, so the field keeps null|number.
function nullableNumberModel(field: { value: number | null }) {
  return computed<string | number>({
    get: () => field.value ?? '',
    set: (v) => {
      field.value = v === '' ? null : Number(v)
    },
  })
}
const triggerEveryNModel = nullableNumberModel(triggerEveryN)
const triggerSilenceModel = nullableNumberModel(triggerSilence)
const recencyModel = nullableNumberModel(recencyHalfLife)

const ownerOptions = computed(() => ownerOptionsQuery.data.value ?? [])
// Only owner kinds that currently have a selectable (map-less) owner.
const availableOwnerKinds = computed(
  () => new Set(ownerOptions.value.map((o) => o.owner_kind)),
)
const ownerKindOptions = computed(() =>
  CONCEPT_MAP_OWNER_KINDS.filter((k) => availableOwnerKinds.value.has(k)).map((k) => ({
    value: k,
    label: ownerKindLabel(k),
  })),
)
const ownerOptionsForKind = computed(() =>
  ownerOptions.value
    .filter((o) => o.owner_kind === ownerKind.value)
    .map((o) => ({ value: o.owner_id, label: o.owner_name })),
)
const keyGroupOptions = computed(() =>
  (keyGroupsQuery.data.value ?? []).map((g) => ({ value: g.id, label: g.name })),
)

const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)
const hasOwners = computed(() => ownerOptions.value.length > 0)
const canCreate = computed(() => hasOwners.value && hasKeyGroups.value)

const eligibilityWarningKey = computed<string | null>(() => {
  if (!keyGroupsQuery.isLoading.value && !hasKeyGroups.value) return 'agents.conceptMaps.noKeyGroups'
  if (!ownerOptionsQuery.isLoading.value && !hasOwners.value) return 'agents.conceptMaps.noOwners'
  return null
})

// When the owner kind changes, drop an owner_id that no longer belongs to it.
watch(ownerKind, () => {
  if (ownerId.value && !ownerOptionsForKind.value.some((o) => o.value === ownerId.value)) {
    ownerId.value = ''
  }
})

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (values: GraphragConfigCreateInput) =>
    (await agentsApi.createGraphragConfig(projectId, values)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    qc.invalidateQueries({ queryKey: agentKeys.graphragOwnerOptions(projectId) })
    showCreateModal.value = false
    toast.success(t('agents.graphragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.graphragList.createFailed'))
  },
})

function openCreateModal(): void {
  resetForm()
  // Default to the first owner kind that actually has a selectable owner.
  ownerKind.value = ownerKindOptions.value[0]?.value ?? 'agent_group'
  triggerEveryN.value = null
  triggerSilence.value = null
  triggerManual.value = false
  recencyHalfLife.value = null
  showCreateModal.value = true
}

const onSubmit = handleSubmit((values) => {
  const trigger_config: Record<string, unknown> = {}
  if (triggerEveryN.value) trigger_config.every_n_messages = triggerEveryN.value
  if (triggerSilence.value) trigger_config.silence_minutes = triggerSilence.value
  if (triggerManual.value) trigger_config.manual = true
  createMutation.mutate({ ...values, trigger_config, recency_half_life_days: recencyHalfLife.value })
})

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteGraphragConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    qc.invalidateQueries({ queryKey: agentKeys.graphragOwnerOptions(projectId) })
    toast.success(t('agents.graphragList.deleted'))
  },
  onError: () => toast.error(t('agents.graphragList.deleteFailed')),
})

async function confirmDelete(cfg: GraphragConfig): Promise<void> {
  const label = cfg.owner_name ?? cfg.owner_id
  const ok = await confirm({
    title: t('agents.graphragList.deleteTitle'),
    message: t('agents.graphragList.deleteConfirm', { name: label }),
    variant: 'error',
  })
  if (!ok) return
  deleteMutation.mutate(cfg.id)
}

// --- Status drawer ---
async function openStatusDrawer(cfg: GraphragConfig): Promise<void> {
  statusDrawerConfig.value = cfg
  showStatusDrawer.value = true
  drawerStatus.value = null
  drawerLoading.value = true
  drawerError.value = false
  try {
    const { data } = await agentsApi.getGraphragStatus(cfg.id)
    drawerStatus.value = data
  } catch {
    drawerError.value = true
    toast.error(t('agents.graphragList.statusFetchFailed'))
  } finally {
    drawerLoading.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '--'
  return new Date(iso).toLocaleString()
}

// --- Actions ---
function onAction(key: string, row: GraphragConfig): void {
  if (key === 'status') void openStatusDrawer(row)
  else if (key === 'graph') openGraph(row)
  else if (key === 'delete') void confirmDelete(row)
}

const actionItems = computed(() => [
  { key: 'status', label: t('agents.graphragList.viewStatus'), icon: EyeIcon },
  { key: 'graph', label: t('agents.graphragList.viewGraph'), icon: ShareIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('agents.graphragList.delete'), icon: TrashIcon, danger: true },
])

const columns = computed<Column[]>(() => [
  { key: 'owner_name', label: t('agents.conceptMaps.colOwner') },
  { key: 'owner_kind', label: t('agents.conceptMaps.colOwnerType'), width: '140px' },
  { key: 'last_build_state', label: t('agents.graphragList.colState'), width: '120px' },
  { key: 'last_build_at', label: t('agents.graphragList.colLastBuilt'), width: '120px' },
  { key: 'actions', label: '', width: '120px', align: 'right' },
])

// Pin STable's row generic to GraphragConfig (Volar can't infer it from a default
// type arg), so slot `row` resolves correctly.
const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
function typedSTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & { data?: T[] }
    $slots: { [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown }
  }
}
const ConceptMapTable = typedSTable<GraphragConfig>()
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.conceptMaps.title')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <SButton
          variant="primary"
          :disabled="!canCreate"
          @click="openCreateModal"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agents.conceptMaps.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="eligibilityWarningKey"
      variant="warning"
      class="mt-4"
    >
      {{ t(eligibilityWarningKey) }}
    </SAlert>

    <div class="mt-6 flex items-center gap-3">
      <label
        for="owner-kind-filter"
        class="text-sm text-[var(--color-muted)]"
      >{{ t('agents.conceptMaps.filterOwnerType') }}</label>
      <SSelect
        id="owner-kind-filter"
        v-model="ownerKindFilter"
        :options="ownerFilterOptions"
        size="sm"
        class="w-48"
      />
    </div>

    <ConceptMapTable
      :columns="columns"
      :data="pagedConfigs"
      :loading="configsQuery.isLoading.value"
      row-key="id"
      class="mt-4"
    >
      <template #cell-owner_name="{ row }">
        <span class="font-medium">{{ row.owner_name ?? row.owner_id }}</span>
      </template>

      <template #cell-owner_kind="{ row }">
        <SBadge :variant="ownerKindVariant(row.owner_kind)">
          {{ ownerKindLabel(row.owner_kind) }}
        </SBadge>
      </template>

      <template #cell-last_build_state="{ row }">
        <SBadge
          :variant="graphragBuildStateVariant(effectiveState(row))"
          :dot="isBuilding(row)"
        >
          {{ t(graphragBuildStateLabelKey(effectiveState(row))) }}
        </SBadge>
      </template>

      <template #cell-last_build_at="{ row }">
        {{ formatDate(row.last_build_at) }}
      </template>

      <template #actions="{ row }">
        <div class="flex items-center gap-2">
          <SButton
            variant="ghost"
            size="sm"
            :disabled="isBuilding(row)"
            @click="startBuild(row.id)"
          >
            {{ t('agents.graphragList.build') }}
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
          :icon="CircleStackIcon"
          :title="t('agents.conceptMaps.emptyTitle')"
          :text="t('agents.conceptMaps.emptyDescription')"
        >
          <template #action>
            <SButton
              v-if="canCreate"
              variant="primary"
              @click="openCreateModal"
            >
              {{ t('agents.conceptMaps.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </ConceptMapTable>

    <SPagination
      v-if="filteredConfigs.length > PAGE_SIZE"
      :page="page"
      :total-pages="totalPages"
      :total-items="filteredConfigs.length"
      :page-size="PAGE_SIZE"
      class="mt-4"
      @update:page="page = $event"
    />

    <!-- Create modal -->
    <SModal
      :open="showCreateModal"
      :title="t('agents.conceptMaps.create')"
      size="md"
      @close="showCreateModal = false"
    >
      <form @submit.prevent="onSubmit">
        <SFormField
          :label="t('agents.conceptMaps.ownerType')"
          name="owner_kind"
          :error="errors.owner_kind ?? ''"
          required
        >
          <SSelect
            v-model="ownerKind"
            :options="ownerKindOptions"
            :placeholder="t('agents.conceptMaps.ownerTypePlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('agents.conceptMaps.owner')"
          name="owner_id"
          :error="errors.owner_id ?? ''"
          required
        >
          <SSelect
            v-model="ownerId"
            :options="ownerOptionsForKind"
            :placeholder="t('agents.conceptMaps.ownerPlaceholder')"
            :disabled="!ownerKind"
          />
        </SFormField>

        <SFormField
          :label="t('agents.graphragForm.builderKeyGroup')"
          name="builder_key_group_id"
          :error="errors.builder_key_group_id ?? ''"
          :help="t('agents.graphragList.builderHint')"
          required
        >
          <SSelect
            v-model="builderKeyGroupId"
            :options="keyGroupOptions"
            :placeholder="t('agents.graphragForm.builderKeyGroupPlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('agents.conceptMaps.recencyHalfLife')"
          name="recency_half_life_days"
          :error="errors.recency_half_life_days ?? ''"
          :help="t('agents.conceptMaps.recencyHelp')"
        >
          <SInput
            v-model="recencyModel"
            type="number"
            min="1"
          />
        </SFormField>

        <SAccordion
          :items="[{ key: 'trigger', title: t('agents.graphragForm.trigger') }]"
          class="mt-4"
        >
          <template #item-trigger>
            <p class="text-sm text-[var(--color-muted)] mb-3">
              {{ t('agents.graphragForm.triggerHelp') }}
            </p>
            <SFormField
              :label="t('agents.graphragForm.triggerEveryN')"
              name="trigger_every_n"
            >
              <SInput
                v-model="triggerEveryNModel"
                type="number"
              />
            </SFormField>
            <SFormField
              :label="t('agents.graphragForm.triggerSilence')"
              name="trigger_silence"
            >
              <SInput
                v-model="triggerSilenceModel"
                type="number"
              />
            </SFormField>
            <SFormField
              :label="t('agents.graphragForm.triggerManual')"
              name="trigger_manual"
            >
              <SToggle v-model="triggerManual" />
            </SFormField>
          </template>
        </SAccordion>
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showCreateModal = false"
          >
            {{ t('agents.graphragList.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="createMutation.isPending.value"
            @click="onSubmit"
          >
            {{ t('agents.graphragForm.submit') }}
          </SButton>
        </div>
      </template>
    </SModal>

    <!-- Status drawer -->
    <SDrawer
      :open="showStatusDrawer"
      :title="t('agents.graphragList.statusTitle')"
      side="right"
      size="md"
      @close="showStatusDrawer = false"
    >
      <div
        v-if="drawerLoading"
        class="flex justify-center py-10"
      >
        <SLoadingSpinner />
      </div>
      <SAlert
        v-else-if="drawerError"
        variant="danger"
      >
        {{ t('agents.graphragList.statusFetchFailed') }}
      </SAlert>
      <template v-else-if="drawerStatus">
        <div class="space-y-6">
          <div>
            <p class="text-sm font-medium text-[var(--color-muted)] mb-1">
              {{ t('agents.graphragList.colState') }}
            </p>
            <SBadge :variant="graphragBuildStateVariant(drawerStatus.state)">
              {{ t(graphragBuildStateLabelKey(drawerStatus.state)) }}
            </SBadge>
          </div>
          <div>
            <p class="text-sm font-medium text-[var(--color-muted)] mb-1">
              {{ t('agents.graphragList.colLastBuilt') }}
            </p>
            <p>{{ formatDate(drawerStatus.last_build_at) }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-[var(--color-muted)] mb-1">
              {{ t('agents.graphragList.lastError') }}
            </p>
            <p
              v-if="drawerStatus.last_build_error"
              class="text-[var(--color-danger)]"
            >
              {{ drawerStatus.last_build_error }}
            </p>
            <p
              v-else
              class="text-[var(--color-muted)]"
            >
              {{ t('agents.graphragList.noError') }}
            </p>
          </div>
          <SButton
            v-if="statusDrawerConfig"
            variant="primary"
            :disabled="isBuilding(statusDrawerConfig)"
            @click="startBuild(statusDrawerConfig.id)"
          >
            {{ t('agents.graphragList.build') }}
          </SButton>
        </div>
      </template>
    </SDrawer>
  </main>
</template>
