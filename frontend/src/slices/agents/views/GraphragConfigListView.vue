<script setup lang="ts">
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
  STooltip,
  SLoadingSpinner,
} from '@shared/ui'
import {
  useConfirmDialog,
  useServerErrors,
  useToast,
} from '@shared/composables'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import {
  agentsApi,
  GRAPHRAG_IN_PROGRESS,
  type GraphragConfig,
  type GraphragStatus,
} from '../api'
import { agentKeys } from '../queries'
import { useProjectBreadcrumbs } from '../composables/useProjectBreadcrumbs'
import { useGraphragSocket } from '../composables/useGraphragSocket'
import {
  graphragConfigCreateSchema,
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
  { label: t('agents.breadcrumb.graphrag') },
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

const agentsQuery = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => (await agentsApi.list(projectId)).data,
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: async () => (await keyGroupsApi.listForProject(projectId)).data,
})

const configs = computed<GraphragConfig[]>(() => configsQuery.data.value ?? [])

const agentById = computed(() =>
  new Map((agentsQuery.data.value ?? []).map((a) => [a.id, a])),
)
const keyGroupById = computed(() =>
  new Map((keyGroupsQuery.data.value ?? []).map((g) => [g.id, g.name])),
)

const configuredAgentIds = computed(
  () => new Set(configs.value.map((c) => c.agent_id)),
)
const availableAgents = computed(() =>
  (agentsQuery.data.value ?? []).filter((a) => !configuredAgentIds.value.has(a.id)),
)
const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)
const canCreate = computed(() => availableAgents.value.length > 0 && hasKeyGroups.value)

// A config is only "active" when its agent points back at it (turn_engine reads
// agent.graphrag_config_id). A built-but-unbound config has already consumed the
// builder key budget yet performs no retrieval, so surface that explicitly to
// stop users believing a finished build is wired up when it is not.
const isBound = (cfg: GraphragConfig): boolean =>
  agentById.value.get(cfg.agent_id)?.graphrag_config_id === cfg.id

// --- Build state management (live via WebSocket) ---
const { liveState, watch: watchBuild, unwatch: unwatchBuild } = useGraphragSocket(projectId)

const effectiveState = (cfg: GraphragConfig): GraphragConfig['last_build_state'] =>
  liveState.value[cfg.id] ?? cfg.last_build_state
const isBuilding = (cfg: GraphragConfig): boolean => GRAPHRAG_IN_PROGRESS.has(effectiveState(cfg))

// Pick up any config that is already mid-build when the list loads (a build
// triggered elsewhere, or a page reload during one) so its progress stays live.
// Seed the known state so the backstop poll works even if the socket can't
// connect (audit C6).
watch(
  configs,
  (list) => {
    for (const cfg of list) {
      if (GRAPHRAG_IN_PROGRESS.has(cfg.last_build_state)) {
        watchBuild(cfg.id, cfg.last_build_state)
      }
    }
  },
  { immediate: true },
)

const buildStateVariant = (state: string): 'neutral' | 'info' | 'danger' | 'warning' => {
  const map: Record<string, 'neutral' | 'info' | 'danger' | 'warning'> = {
    idle: 'neutral',
    running: 'info',
    neo4j_committed: 'info',
    qdrant_committed: 'info',
    failed: 'danger',
    failed_compensating: 'warning',
  }
  return map[state] ?? 'neutral'
}

const buildStateLabel = (state: string): string => {
  const map: Record<string, string> = {
    idle: t('agents.graphragList.states.idle'),
    running: t('agents.graphragList.states.running'),
    neo4j_committed: t('agents.graphragList.states.neo4jCommitted'),
    qdrant_committed: t('agents.graphragList.states.qdrantCommitted'),
    failed: t('agents.graphragList.states.failed'),
    failed_compensating: t('agents.graphragList.states.compensating'),
  }
  return map[state] ?? state
}

const buildMutation = useMutation({
  mutationFn: (id: string) => agentsApi.buildGraphrag(id),
  onSuccess: () => toast.success(t('agents.graphragList.buildStarted')),
  onError: (_err, id) => {
    // The build never started, so roll back the optimistic 'running' state —
    // otherwise no WS event will ever arrive to clear it and the card sticks.
    const next = { ...liveState.value }
    delete next[id]
    liveState.value = next
    // Tear down the subscription opened optimistically in startBuild, or the WS
    // channel and the backstop poll timer would leak (audit review #5).
    unwatchBuild(id)
    toast.error(t('agents.graphragList.buildFailed'))
  },
})

function startBuild(id: string): void {
  // Optimistic running state + subscribe before the request returns so we
  // never miss the early build.state events.
  liveState.value = { ...liveState.value, [id]: 'running' }
  watchBuild(id, 'running')
  buildMutation.mutate(id)
}

function openGraph(cfg: GraphragConfig): void {
  void router.push({
    name: 'agents.graphragGraph',
    params: { projectId, configId: cfg.id },
  })
}

// --- Create form ---
const schema = toTypedSchema(graphragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } =
  useForm<GraphragConfigCreateInput>({
    validationSchema: schema,
    initialValues: { agent_id: '', builder_key_group_id: '', trigger_config: {} },
  })

const [agentId] = defineField('agent_id')
const [builderKeyGroupId] = defineField('builder_key_group_id')

const triggerEveryN = ref<number | null>(null)
const triggerSilence = ref<number | null>(null)
const triggerManual = ref(false)

const builderKeyGroups = computed(() => {
  const consumer = agentId.value ? agentById.value.get(agentId.value)?.key_group_id : undefined
  return (keyGroupsQuery.data.value ?? []).filter((g) => g.id !== consumer)
})

watch(agentId, () => {
  if (
    builderKeyGroupId.value &&
    !builderKeyGroups.value.some((g) => g.id === builderKeyGroupId.value)
  ) {
    builderKeyGroupId.value = ''
  }
})

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (values: GraphragConfigCreateInput) =>
    (await agentsApi.createGraphragConfig(projectId, values)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    showCreateModal.value = false
    toast.success(t('agents.graphragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.graphragList.createFailed'))
  },
})

function openCreateModal(): void {
  resetForm()
  triggerEveryN.value = null
  triggerSilence.value = null
  triggerManual.value = false
  showCreateModal.value = true
}

const onSubmit = handleSubmit((values) => {
  const trigger_config: Record<string, unknown> = {}
  if (triggerEveryN.value) trigger_config.every_n_messages = triggerEveryN.value
  if (triggerSilence.value) trigger_config.silence_minutes = triggerSilence.value
  if (triggerManual.value) trigger_config.manual = true
  createMutation.mutate({ ...values, trigger_config })
})

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteGraphragConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
    toast.success(t('agents.graphragList.deleted'))
  },
  onError: () => toast.error(t('agents.graphragList.deleteFailed')),
})

async function confirmDelete(cfg: GraphragConfig): Promise<void> {
  const label = agentById.value.get(cfg.agent_id)?.name ?? cfg.agent_id
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
  // Audit M12: show explicit loading + error states instead of a blank drawer.
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
  { key: 'delete', label: t('common.delete', 'Delete'), icon: TrashIcon, danger: true },
])

const agentOptions = computed(() =>
  availableAgents.value.map((a) => ({ value: a.id, label: a.name })),
)
const builderKeyGroupOptions = computed(() =>
  builderKeyGroups.value.map((g) => ({ value: g.id, label: g.name })),
)

const accordionItems = computed(() => [
  { key: 'trigger', title: t('agents.graphragForm.trigger') },
])

const columns = computed<Column[]>(() => [
  { key: 'agent_id', label: t('agents.graphragList.colAgent') },
  { key: 'builder_key_group_id', label: t('agents.graphragList.colBuilder'), width: '160px' },
  { key: 'last_build_state', label: t('agents.graphragList.colState'), width: '120px' },
  { key: 'binding', label: t('agents.graphragList.colBinding'), width: '110px' },
  { key: 'last_build_at', label: t('agents.graphragList.colLastBuilt'), width: '120px' },
  { key: 'actions', label: '', width: '120px', align: 'right' },
])
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.graphragList.title')"
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
          {{ t('agents.graphragList.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="!agentsQuery.isLoading.value && (agentsQuery.data.value?.length ?? 0) === 0"
      variant="warning"
      class="mt-4"
    >
      {{ t('agents.graphragList.noAgents') }}
    </SAlert>
    <SAlert
      v-else-if="!keyGroupsQuery.isLoading.value && !hasKeyGroups"
      variant="warning"
      class="mt-4"
    >
      {{ t('agents.graphragList.noKeyGroups') }}
    </SAlert>

    <STable
      :columns="columns"
      :data="configs"
      :loading="configsQuery.isLoading.value"
      row-key="id"
      class="mt-6"
    >
      <template #cell-agent_id="{ row }">
        <span class="font-medium">
          {{ agentById.get(row.agent_id)?.name ?? row.agent_id }}
        </span>
      </template>

      <template #cell-builder_key_group_id="{ row }">
        {{ keyGroupById.get(row.builder_key_group_id) ?? row.builder_key_group_id }}
      </template>

      <template #cell-last_build_state="{ row }">
        <SBadge
          :variant="buildStateVariant(effectiveState(row))"
          :dot="isBuilding(row)"
        >
          {{ buildStateLabel(effectiveState(row)) }}
        </SBadge>
      </template>

      <template #cell-binding="{ row }">
        <SBadge
          v-if="isBound(row)"
          variant="success"
        >
          {{ t('agents.graphragList.bound') }}
        </SBadge>
        <STooltip
          v-else
          :content="t('agents.graphragList.unboundHint')"
        >
          <SBadge variant="warning">
            {{ t('agents.graphragList.unbound') }}
          </SBadge>
        </STooltip>
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
          :title="t('agents.graphragList.emptyTitle')"
          :text="t('agents.graphragList.emptyDescription')"
        >
          <template #action>
            <SButton
              v-if="canCreate"
              variant="primary"
              @click="openCreateModal"
            >
              {{ t('agents.graphragList.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <!-- Create modal -->
    <SModal
      :open="showCreateModal"
      :title="t('agents.graphragList.create')"
      size="md"
      @close="showCreateModal = false"
    >
      <form @submit.prevent="onSubmit">
        <SFormField
          :label="t('agents.graphragForm.agent')"
          name="agent_id"
          :error="errors.agent_id"
          required
        >
          <SSelect
            v-model="agentId"
            :options="agentOptions"
            :placeholder="t('agents.graphragForm.agentPlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('agents.graphragForm.builderKeyGroup')"
          name="builder_key_group_id"
          :error="errors.builder_key_group_id"
          required
        >
          <SSelect
            v-model="builderKeyGroupId"
            :options="builderKeyGroupOptions"
            :placeholder="t('agents.graphragForm.builderKeyGroupPlaceholder')"
            :disabled="!agentId"
          />
        </SFormField>

        <SAccordion
          :items="accordionItems"
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
                v-model="triggerEveryN"
                type="number"
              />
            </SFormField>
            <SFormField
              :label="t('agents.graphragForm.triggerSilence')"
              name="trigger_silence"
            >
              <SInput
                v-model="triggerSilence"
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
            <SBadge :variant="buildStateVariant(drawerStatus.state)">
              {{ buildStateLabel(drawerStatus.state) }}
            </SBadge>
          </div>
          <div v-if="statusDrawerConfig">
            <p class="text-sm font-medium text-[var(--color-muted)] mb-1">
              {{ t('agents.graphragList.colBinding') }}
            </p>
            <SBadge
              v-if="isBound(statusDrawerConfig)"
              variant="success"
            >
              {{ t('agents.graphragList.bound') }}
            </SBadge>
            <template v-else>
              <SBadge variant="warning">
                {{ t('agents.graphragList.unbound') }}
              </SBadge>
              <p class="text-sm text-[var(--color-muted)] mt-1">
                {{ t('agents.graphragList.unboundHint') }}
              </p>
            </template>
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
