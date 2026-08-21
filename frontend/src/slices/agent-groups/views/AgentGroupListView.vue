<script setup lang="ts">
// Agent Groups list (Phase 4α WS1, R11.07). Project-scoped CRUD; create/rename/
// delete are Project-Owner-gated (the backend is the authority — a non-owner 403s).
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { PlusIcon, UserGroupIcon, PencilSquareIcon, TrashIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SModal,
  SFormField,
  SInput,
  SEmptyState,
} from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import { agentGroupsApi, type AgentGroup } from '../api'
import { agentGroupKeys } from '../queries'
import {
  agentGroupCreateSchema,
  agentGroupUpdateSchema,
  type AgentGroupCreateInput,
} from '../types/schemas'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const projectId = route.params.projectId as string
const { isAuthorized, decided } = useProjectRole(() => projectId)

// Mutating controls always render (never v-if-hidden) but stay disabled until
// authorization resolves true — this avoids both a real owner's controls
// flashing hidden-then-shown (spec §8) AND exposing a live, clickable control to
// a non-owner during the loading window. The read-only note still waits for
// `decided` so it never flashes to a confirmed owner/admin.
const showReadonlyNote = computed(() => !isAuthorized.value && decided.value)

const groupsQuery = useQuery({
  queryKey: agentGroupKeys.groups(projectId),
  queryFn: () => agentGroupsApi.list(projectId),
})
const groups = computed<AgentGroup[]>(() => groupsQuery.data.value ?? [])

// --- Create ---
const showCreateModal = ref(false)
const createSchema = toTypedSchema(agentGroupCreateSchema)
const {
  handleSubmit: handleCreate,
  errors: createErrors,
  defineField: defineCreateField,
  resetForm: resetCreate,
  setErrors: setCreateErrors,
} = useForm<AgentGroupCreateInput>({ validationSchema: createSchema, initialValues: { name: '' } })
const [createName] = defineCreateField('name')
const { applyServerErrors: applyCreateErrors } = useServerErrors(setCreateErrors)

const createMutation = useMutation({
  mutationFn: (values: AgentGroupCreateInput) => agentGroupsApi.create(projectId, values),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.groups(projectId) })
    showCreateModal.value = false
    toast.success(t('agentGroups.list.created'))
  },
  onError: (err) => {
    if (!applyCreateErrors(err)) toast.error(t('agentGroups.list.createFailed'))
  },
})
function openCreate(): void {
  resetCreate()
  showCreateModal.value = true
}
const onCreate = handleCreate((values) => createMutation.mutate(values))

// --- Rename ---
const showRenameModal = ref(false)
const renameTarget = ref<AgentGroup | null>(null)
const renameSchema = toTypedSchema(agentGroupUpdateSchema)
const {
  handleSubmit: handleRename,
  errors: renameErrors,
  defineField: defineRenameField,
  resetForm: resetRename,
  setErrors: setRenameErrors,
} = useForm<AgentGroupCreateInput>({ validationSchema: renameSchema, initialValues: { name: '' } })
const [renameName] = defineRenameField('name')
const { applyServerErrors: applyRenameErrors } = useServerErrors(setRenameErrors)

const renameMutation = useMutation({
  mutationFn: async (name: string) => {
    if (!renameTarget.value) return
    await agentGroupsApi.rename(renameTarget.value.id, { name })
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.groups(projectId) })
    showRenameModal.value = false
    toast.success(t('agentGroups.detail.renameSaved'))
  },
  onError: (err) => {
    if (!applyRenameErrors(err)) toast.error(t('agentGroups.detail.renameFailed'))
  },
})
function openRename(group: AgentGroup): void {
  renameTarget.value = group
  resetRename({ values: { name: group.name } })
  showRenameModal.value = true
}
const onRename = handleRename((values) => renameMutation.mutate(values.name))

// --- Delete ---
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentGroupsApi.remove(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.groups(projectId) })
    toast.success(t('agentGroups.list.deleted'))
  },
  onError: () => toast.error(t('agentGroups.list.deleteFailed')),
})
async function confirmDelete(group: AgentGroup): Promise<void> {
  const ok = await confirm({
    title: t('agentGroups.list.deleteTitle'),
    message: t('agentGroups.list.deleteConfirm', { name: group.name }),
    variant: 'error',
  })
  if (ok) deleteMutation.mutate(group.id)
}

function openDetail(group: AgentGroup): void {
  void router.push({ name: 'agentGroups.detail', params: { groupId: group.id } })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString()
}

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('agentGroups.list.colName') },
  { key: 'created_at', label: t('agentGroups.list.colCreated'), width: '160px' },
  { key: 'actions', label: '', width: '200px', align: 'right' },
])

const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
function typedSTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & { data?: T[] }
    $slots: { [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown }
  }
}
const GroupTable = typedSTable<AgentGroup>()
</script>

<template>
  <div>
    <SPageHeader :title="t('agentGroups.list.title')">
      <template #actions>
        <SButton
          variant="primary"
          :disabled="!isAuthorized"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agentGroups.list.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <p
      v-if="showReadonlyNote"
      class="mt-4 text-sm text-[var(--color-muted)]"
    >
      {{ t('agentGroups.list.ownerOnly') }}
    </p>

    <GroupTable
      :columns="columns"
      :data="groups"
      :loading="groupsQuery.isLoading.value"
      row-key="id"
      class="mt-6"
    >
      <template #cell-name="{ row }">
        <button
          type="button"
          class="font-medium text-[var(--color-accent)] hover:underline"
          @click="openDetail(row)"
        >
          {{ row.name }}
        </button>
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #actions="{ row }">
        <div class="flex items-center justify-end gap-2">
          <SButton
            variant="ghost"
            size="sm"
            @click="openDetail(row)"
          >
            {{ t('agentGroups.list.open') }}
          </SButton>
          <SButton
            variant="ghost"
            size="sm"
            icon-only
            :disabled="!isAuthorized"
            :aria-label="t('agentGroups.list.rename')"
            @click="openRename(row)"
          >
            <PencilSquareIcon class="w-4 h-4" />
          </SButton>
          <SButton
            variant="ghost"
            size="sm"
            icon-only
            :disabled="!isAuthorized"
            :aria-label="t('agentGroups.list.delete')"
            @click="confirmDelete(row)"
          >
            <TrashIcon class="w-4 h-4" />
          </SButton>
        </div>
      </template>

      <template #empty>
        <SEmptyState
          :icon="UserGroupIcon"
          :title="t('agentGroups.list.emptyTitle')"
          :text="t('agentGroups.list.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              :disabled="!isAuthorized"
              @click="openCreate"
            >
              {{ t('agentGroups.list.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </GroupTable>

    <!-- Create modal -->
    <SModal
      :open="showCreateModal"
      :title="t('agentGroups.list.createTitle')"
      size="sm"
      @close="showCreateModal = false"
    >
      <form @submit.prevent="onCreate">
        <SFormField
          :label="t('agentGroups.list.nameLabel')"
          name="name"
          :error="createErrors.name ?? ''"
          required
        >
          <SInput
            v-model="createName"
            :placeholder="t('agentGroups.list.namePlaceholder')"
          />
        </SFormField>
      </form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showCreateModal = false"
          >
            {{ t('agentGroups.list.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="createMutation.isPending.value"
            @click="onCreate"
          >
            {{ t('agentGroups.list.submit') }}
          </SButton>
        </div>
      </template>
    </SModal>

    <!-- Rename modal -->
    <SModal
      :open="showRenameModal"
      :title="t('agentGroups.detail.renameTitle')"
      size="sm"
      @close="showRenameModal = false"
    >
      <form @submit.prevent="onRename">
        <SFormField
          :label="t('agentGroups.detail.nameLabel')"
          name="name"
          :error="renameErrors.name ?? ''"
          required
        >
          <SInput v-model="renameName" />
        </SFormField>
      </form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showRenameModal = false"
          >
            {{ t('agentGroups.detail.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :loading="renameMutation.isPending.value"
            @click="onRename"
          >
            {{ t('agentGroups.detail.save') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </div>
</template>
