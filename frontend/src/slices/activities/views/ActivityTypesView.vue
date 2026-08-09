<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  PlusIcon,
  PencilSquareIcon,
  TrashIcon,
  PuzzlePieceIcon,
  EllipsisVerticalIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  SparklesIcon,
} from '@heroicons/vue/24/outline'

import {
  SPageHeader,
  SBadge,
  SButton,
  SDropdown,
  SEmptyState,
  typedTable,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import type { Column } from '@shared/ui/STable.vue'

import ActivityHelpPanel from '../components/ActivityHelpPanel.vue'
import ActivityTypeForm from '../components/ActivityTypeForm.vue'
import ExampleImportDialog from '../components/ExampleImportDialog.vue'
import { listActivityTypes, deleteActivityType } from '../api'
import { activityKeys } from '../queries'
import type { ActivityType } from '../types'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()

const projectId = route.params.projectId as string
const { decided, isAuthorized } = useProjectRole(projectId)

const showModal = ref(false)
const editType = ref<ActivityType | null>(null)
const showHelp = ref(false)
const showExamples = ref(false)

/** A platform-scoped type is owned by the platform and read-only here ([R30.23]):
 *  the server refuses an owner's edit or delete with 403, so offering the action
 *  would only produce a refusal the user could not act on. */
function isPlatform(row: ActivityType): boolean {
  return row.scope === 'platform'
}

const typesQuery = useQuery({
  queryKey: activityKeys.types(projectId),
  queryFn: () => listActivityTypes(projectId),
  enabled: computed(() => decided.value && isAuthorized.value),
})

const types = computed<ActivityType[]>(() => typesQuery.data.value ?? [])
const loading = computed(() => typesQuery.isLoading.value)
const isError = computed(() => typesQuery.isError.value)

const deleteMutation = useMutation({
  mutationFn: (typeId: string) => deleteActivityType(projectId, typeId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: activityKeys.types(projectId) })
    toast.success(t('activities.typesList.deleted'))
  },
  onError: () => {
    // 404 (already deleted) and any other failure resolve the same way: tell the
    // user and refetch so the row reflects server truth.
    qc.invalidateQueries({ queryKey: activityKeys.types(projectId) })
    toast.error(t('activities.typesList.deleteFailed'))
  },
})

async function confirmDelete(row: ActivityType): Promise<void> {
  const ok = await confirm({
    title: t('activities.typesList.deleteTitle'),
    message: t('activities.typesList.deleteConfirm', { name: row.name }),
    variant: 'warning',
  })
  if (!ok) return
  deleteMutation.mutate(row.id)
}

function onAction(action: string, row: ActivityType): void {
  if (action === 'edit') openEdit(row)
  else if (action === 'delete') void confirmDelete(row)
}

function openCreate(): void {
  editType.value = null
  showModal.value = true
}

function openEdit(row: ActivityType): void {
  editType.value = row
  showModal.value = true
}

function closeModal(): void {
  showModal.value = false
  editType.value = null
}

function onCreated(): void {
  qc.invalidateQueries({ queryKey: activityKeys.types(projectId) })
  closeModal()
  toast.success(t('activities.typesList.created'))
}

function onUpdated(): void {
  qc.invalidateQueries({ queryKey: activityKeys.types(projectId) })
  closeModal()
  toast.success(t('activities.typesList.updated'))
}

const actionItems = computed(() => [
  { key: 'edit', label: t('common.edit', 'Edit'), icon: PencilSquareIcon },
  { key: 'delete', label: t('common.delete', 'Delete'), icon: TrashIcon, danger: true },
])

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('activities.typesList.colName') },
  { key: 'key', label: t('activities.typesList.colKey'), width: '200px' },
  { key: 'validator_kind', label: t('activities.typesList.colValidator'), width: '140px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])

function onExamplesChanged(): void {
  qc.invalidateQueries({ queryKey: activityKeys.types(projectId) })
}

const ActivityTypeTable = typedTable<ActivityType>()
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('activities.typesList.title')"
      :subtitle="t('activities.typesList.subtitle')"
    >
      <template
        v-if="decided && isAuthorized"
        #actions
      >
        <SButton
          variant="secondary"
          @click="showHelp = true"
        >
          <template #icon-left>
            <InformationCircleIcon class="w-4 h-4" />
          </template>
          {{ t('activities.typesList.help') }}
        </SButton>
        <SButton
          variant="secondary"
          @click="showExamples = true"
        >
          <template #icon-left>
            <SparklesIcon class="w-4 h-4" />
          </template>
          {{ t('activities.typesList.browseExamples') }}
        </SButton>
        <SButton
          variant="primary"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('activities.typesList.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div
      v-if="decided && !isAuthorized"
      class="mt-6"
    >
      <SEmptyState
        :icon="PuzzlePieceIcon"
        :title="t('activities.typesList.forbiddenTitle')"
        :text="t('activities.typesList.forbiddenText')"
      />
    </div>

    <div
      v-else-if="decided && isError"
      class="mt-6"
    >
      <SEmptyState
        :icon="ExclamationTriangleIcon"
        :title="t('activities.typesList.errorTitle')"
        :text="t('activities.typesList.errorText')"
      >
        <template #action>
          <SButton
            variant="secondary"
            @click="typesQuery.refetch()"
          >
            {{ t('activities.typesList.retry') }}
          </SButton>
        </template>
      </SEmptyState>
    </div>

    <template v-else-if="decided">
      <ActivityTypeTable
        :columns="columns"
        :data="types"
        :loading="loading"
        row-key="id"
        class="mt-6"
      >
        <template #cell-name="{ row }">
          <span class="font-medium">{{ row.name }}</span>
          <SBadge
            v-if="isPlatform(row)"
            size="sm"
            variant="info"
            class="ml-2"
          >
            {{ t('activities.typesList.platformExample') }}
          </SBadge>
        </template>

        <template #cell-key="{ row }">
          <span class="font-mono text-sm">{{ row.key }}</span>
        </template>

        <template #cell-validator_kind="{ row }">
          <SBadge variant="neutral">
            {{ t(`activities.typeForm.validatorKind.${row.validator_kind}`, row.validator_kind) }}
          </SBadge>
        </template>

        <template #actions="{ row }">
          <SDropdown
            v-if="!isPlatform(row)"
            :items="actionItems"
            placement="bottom-end"
            @select="onAction($event, row)"
          >
            <template #trigger>
              <SButton
                variant="ghost"
                icon-only
                size="sm"
                :aria-label="t('activities.typesList.actions')"
              >
                <EllipsisVerticalIcon class="w-4 h-4" />
              </SButton>
            </template>
          </SDropdown>
        </template>

        <template #empty>
          <SEmptyState
            :icon="PuzzlePieceIcon"
            :title="t('activities.typesList.emptyTitle')"
            :text="t('activities.typesList.emptyText')"
          >
            <template #action>
              <SButton
                variant="primary"
                @click="openCreate"
              >
                {{ t('activities.typesList.create') }}
              </SButton>
            </template>
          </SEmptyState>
        </template>
      </ActivityTypeTable>
    </template>

    <ActivityTypeForm
      :project-id="projectId"
      :open="showModal"
      :edit-type="editType"
      @close="closeModal"
      @created="onCreated"
      @updated="onUpdated"
    />

    <ExampleImportDialog
      :project-id="projectId"
      :open="showExamples"
      @close="showExamples = false"
      @changed="onExamplesChanged"
    />

    <ActivityHelpPanel
      :open="showHelp"
      @close="showHelp = false"
    />
  </main>
</template>
