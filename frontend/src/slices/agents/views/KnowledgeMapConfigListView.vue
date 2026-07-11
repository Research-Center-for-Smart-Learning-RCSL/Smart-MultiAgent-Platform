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
  CircleStackIcon,
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
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { keyGroupsApi, keysKeys } from '@slices/keys'
import { agentsApi, type KnowmapConfig } from '../api'
import { agentKeys } from '../queries'
import { useProjectBreadcrumbs } from '../composables/useProjectBreadcrumbs'
import { knowmapConfigCreateSchema, type KnowmapConfigCreateInput } from '../types/schemas'
import { useChunkParamsForm } from '../composables/useChunkParamsForm'
import { graphragBuildStateVariant, graphragBuildStateLabelKey } from '../lib/graphragBuildState'
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

const { breadcrumbs } = useProjectBreadcrumbs(projectId, [
  { label: t('agents.breadcrumb.knowledgeMaps') },
])

const configsQuery = useQuery({
  queryKey: agentKeys.knowmapConfigs(projectId),
  queryFn: () => agentsApi.listKnowmapConfigs(projectId),
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: () => keyGroupsApi.listForProject(projectId),
})

const configs = computed<KnowmapConfig[]>(() => configsQuery.data.value ?? [])
const loading = computed(() => configsQuery.isLoading.value)

const hasKeyGroups = computed(() => (keyGroupsQuery.data.value?.length ?? 0) > 0)
const keyGroupOptions = computed(() =>
  (keyGroupsQuery.data.value ?? []).map((g) => ({ value: g.id, label: g.name })),
)

const filteredConfigs = computed(() => {
  if (!search.value) return configs.value
  const q = search.value.toLowerCase()
  return configs.value.filter((c) => c.name.toLowerCase().includes(q))
})

const { currentPage, totalPages, paginatedItems, pageSize } =
  useClientPagination(filteredConfigs)

// --- Create form ---
const schema = toTypedSchema(knowmapConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, values } =
  useForm<KnowmapConfigCreateInput>({
    validationSchema: schema,
    initialValues: {
      name: '',
      builder_key_group_id: '',
      chunk_strategy: 'fixed',
      chunk_params: {},
    },
  })

const [name] = defineField('name')
const [builderKeyGroupId] = defineField('builder_key_group_id')
const [chunkStrategy] = defineField('chunk_strategy')

const {
  chunkSizeTokens,
  chunkOverlapTokens,
  similarityThreshold,
  resetChunkDefaults,
  assembleChunkParams,
} = useChunkParamsForm()

function openCreateModal(): void {
  resetForm()
  resetChunkDefaults()
  if (keyGroupOptions.value.length) builderKeyGroupId.value = keyGroupOptions.value[0]!.value
  showModal.value = true
}

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: (payload: KnowmapConfigCreateInput) =>
    agentsApi.createKnowmapConfig(projectId, payload),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.knowmapConfigs(projectId) })
    showModal.value = false
    toast.success(t('agents.knowmapList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.knowmapList.createFailed'))
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
  mutationFn: (id: string) => agentsApi.deleteKnowmapConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.knowmapConfigs(projectId) })
    toast.success(t('agents.knowmapList.deleted'))
  },
  onError: () => toast.error(t('agents.knowmapList.deleteFailed')),
})

async function confirmDelete(cfg: KnowmapConfig): Promise<void> {
  const ok = await confirm({
    title: t('agents.knowmapList.deleteTitle'),
    message: t('agents.knowmapList.deleteConfirm', { name: cfg.name }),
    variant: 'warning',
  })
  if (!ok) return
  deleteMutation.mutate(cfg.id)
}

function goToConfig(configId: string): void {
  router.push({
    name: 'agents.knowmapConfig',
    params: { projectId, configId },
  })
}

function onAction(key: string, row: KnowmapConfig): void {
  if (key === 'edit') goToConfig(row.id)
  else if (key === 'delete') void confirmDelete(row)
}

function onRowClick(row: KnowmapConfig): void {
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
  { key: 'name', label: t('agents.knowmapList.colName') },
  { key: 'embedding', label: t('agents.knowmapList.colEmbed'), width: '180px' },
  { key: 'last_build_state', label: t('agents.knowmapList.colState'), width: '120px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])

// STable's row generic (`T extends Record<string, unknown> = Record<string, unknown>`)
// falls back to its default type instead of inferring from `:data`/slot usage here (a
// Volar limitation with generic script-setup props that declare a default type
// argument). Pin it explicitly so `row` in slots/emits resolves to `KnowmapConfig`
// instead of `Record<string, unknown>`.
const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
function typedSTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & {
      data?: T[]
      onRowClick?: (row: T) => void
    }
    $slots: {
      [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown
    }
  }
}
const KnowmapConfigTable = typedSTable<KnowmapConfig>()
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.knowmapList.title')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <STooltip
          v-if="!hasKeyGroups"
          :content="t('agents.knowmapList.noKeyGroups')"
        >
          <SButton
            variant="primary"
            disabled
          >
            <template #icon-left>
              <PlusIcon class="w-4 h-4" />
            </template>
            {{ t('agents.knowmapList.create') }}
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
          {{ t('agents.knowmapList.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div class="mt-6">
      <SSearchInput
        v-model="search"
        :placeholder="t('agents.knowmapList.searchPlaceholder')"
        class="w-64"
      />
    </div>

    <KnowmapConfigTable
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

      <template #cell-embedding="{ row }">
        <span
          v-if="row.embed_provider && row.embed_model"
          class="font-mono text-sm"
        >{{ row.embed_provider }}/{{ row.embed_model }}</span>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >{{ t('agents.knowmapList.embedPending') }}</span>
      </template>

      <template #cell-last_build_state="{ row }">
        <SBadge :variant="graphragBuildStateVariant(row.last_build_state)">
          {{ t(graphragBuildStateLabelKey(row.last_build_state)) }}
        </SBadge>
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
          :icon="CircleStackIcon"
          :title="t('agents.knowmapList.emptyTitle')"
          :text="t('agents.knowmapList.emptyDescription')"
        >
          <template #action>
            <SButton
              v-if="hasKeyGroups"
              variant="primary"
              @click="openCreateModal"
            >
              {{ t('agents.knowmapList.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </KnowmapConfigTable>

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
      :title="t('agents.knowmapList.create')"
      size="lg"
      @close="showModal = false"
    >
      <form
        @submit.prevent="onSubmit"
      >
        <SFormField
          :label="t('agents.knowmapForm.name')"
          name="name"
          :error="errors.name ?? ''"
          required
        >
          <SInput
            v-model="name"
            :maxlength="INPUT_LIMITS.NAME"
            :error="!!errors.name"
          />
        </SFormField>

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
          />
        </SFormField>

        <SFormField
          :label="t('agents.ragForm.chunkStrategy')"
          name="chunk_strategy"
          :error="errors.chunk_strategy ?? ''"
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
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showModal = false"
          >
            {{ t('agents.knowmapList.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="createMutation.isPending.value"
            @click="onSubmit"
          >
            {{ t('agents.knowmapForm.submit') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
