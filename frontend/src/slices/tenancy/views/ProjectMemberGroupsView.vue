<script setup lang="ts">
// Member Groups (section 13.2a) - named subsets of this project's members, used
// to scope chat-room visibility below project level.
//
// Two audiences share this view. A project owner manages the groups; anyone else
// sees only the groups they belong to, because which groups exist and who is in
// them is exactly what one team should not be able to enumerate about another
// (R13.31). The narrowing is enforced server-side; this view only reflects it.

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, STable, SBadge, SButton,
  SFormField, SInput, SSelect, SAlert, SEmptyState, SLoadingSpinner,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { UserGroupIcon, TrashIcon } from '@heroicons/vue/24/outline'
import { memberGroupsApi, type MemberGroup } from '../api/memberGroups'
import { projectsApi } from '../api/projects'
import { tenancyKeys } from '../queries'
import { useProjectRole } from '../composables/useProjectRole'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirmDialog()
const qc = useQueryClient()

const projectId = computed(() => route.params.id as string)
const { isAuthorized } = useProjectRole(projectId)

const { data: project } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value),
})

const { data: groups, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.memberGroups(projectId.value)),
  queryFn: () => memberGroupsApi.list(projectId.value),
})

// The roster is only needed to render the picker and to name members by email,
// both of which are manager-only surfaces, so it is fetched for them alone.
const { data: projectMembers } = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
  queryFn: () => projectsApi.listMembers(projectId.value),
  enabled: isAuthorized,
})

const selectedGroupId = ref<string | null>(null)
const selectedGroup = computed<MemberGroup | null>(
  () => groups.value?.find(g => g.id === selectedGroupId.value) ?? null,
)

const { data: members, isLoading: membersLoading } = useQuery({
  queryKey: computed(() => tenancyKeys.memberGroupMembers(selectedGroupId.value ?? 'none')),
  queryFn: () => memberGroupsApi.listMembers(selectedGroupId.value as string),
  enabled: computed(() => selectedGroupId.value !== null),
})

const newName = ref('')
const createPending = ref(false)
const createError = ref<string | null>(null)
const addUserId = ref('')

const columns = computed(() => [
  { key: 'name', label: t('tenancy.memberGroup.name'), sortable: true },
  { key: 'created_at', label: t('tenancy.settings.created'), width: '140px' },
  { key: 'actions', label: '', width: '96px' },
])

type MemberGroupRow = MemberGroup & Record<string, unknown>
const tableData = computed<MemberGroupRow[]>(
  () => (groups.value ?? []) as unknown as MemberGroupRow[],
)

const emailByUserId = computed(() => {
  const map = new Map<string, string>()
  for (const m of projectMembers.value ?? []) map.set(m.user_id, m.email)
  return map
})

/** Project members not already in the selected group - the picker's options. */
const addableOptions = computed(() => {
  const present = new Set((members.value ?? []).map(m => m.user_id))
  return (projectMembers.value ?? [])
    .filter(m => !present.has(m.user_id))
    .map(m => ({ value: m.user_id, label: m.email }))
})

function invalidateGroups(): void {
  qc.invalidateQueries({ queryKey: tenancyKeys.memberGroups(projectId.value) })
}

function invalidateMembers(groupId: string): void {
  qc.invalidateQueries({ queryKey: tenancyKeys.memberGroupMembers(groupId) })
}

async function onCreate(): Promise<void> {
  const name = newName.value.trim()
  if (!name) return
  createPending.value = true
  createError.value = null
  try {
    await memberGroupsApi.create(projectId.value, name)
    newName.value = ''
    invalidateGroups()
    toast.success(t('tenancy.memberGroup.created', { name }))
  } catch {
    createError.value = t('tenancy.memberGroup.createFailed')
  } finally {
    createPending.value = false
  }
}

async function onDelete(group: MemberGroup): Promise<void> {
  const ok = await confirm({
    title: t('tenancy.memberGroup.deleteTitle'),
    message: t('tenancy.memberGroup.deleteBody', { name: group.name }),
    confirmLabel: t('tenancy.memberGroup.deleteConfirm'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await memberGroupsApi.remove(group.id)
    if (selectedGroupId.value === group.id) selectedGroupId.value = null
    invalidateGroups()
    toast.success(t('tenancy.memberGroup.deleted'))
  } catch {
    toast.error(t('tenancy.memberGroup.deleteFailed'))
  }
}

async function onAddMember(): Promise<void> {
  const groupId = selectedGroupId.value
  const userId = addUserId.value
  if (!groupId || !userId) return
  try {
    await memberGroupsApi.addMember(groupId, userId)
    addUserId.value = ''
    invalidateMembers(groupId)
    toast.success(t('tenancy.memberGroup.memberAdded'))
  } catch {
    toast.error(t('tenancy.memberGroup.memberAddFailed'))
  }
}

async function onRemoveMember(userId: string): Promise<void> {
  const groupId = selectedGroupId.value
  if (!groupId) return
  try {
    await memberGroupsApi.removeMember(groupId, userId)
    invalidateMembers(groupId)
    toast.success(t('tenancy.memberGroup.memberRemoved'))
  } catch {
    toast.error(t('tenancy.memberGroup.memberRemoveFailed'))
  }
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.projects'), to: { name: 'tenancy.projectList' } },
  {
    label: project.value?.name ?? '...',
    to: { name: 'tenancy.projectDetail', params: { id: projectId.value } },
  },
  { label: t('tenancy.breadcrumb.memberGroups') },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.breadcrumb.memberGroups')"
      :subtitle="t('tenancy.memberGroup.subtitle')"
      :breadcrumbs="breadcrumbs"
    />

    <SCard
      v-if="isAuthorized"
      variant="flat"
      class="create-card"
    >
      <form
        class="create-form"
        @submit.prevent="onCreate"
      >
        <SFormField
          :label="t('tenancy.memberGroup.name')"
          name="groupName"
          :error="createError ?? ''"
          required
          class="create-name"
        >
          <SInput
            v-model="newName"
            :placeholder="t('tenancy.memberGroup.namePlaceholder')"
            :error="!!createError"
            :disabled="createPending"
          />
        </SFormField>
        <SButton
          type="submit"
          variant="primary"
          :loading="createPending"
          :disabled="createPending || !newName.trim()"
        >
          {{ t('tenancy.memberGroup.create') }}
        </SButton>
      </form>
    </SCard>

    <SAlert
      v-if="isError"
      variant="danger"
    >
      {{ t('tenancy.memberGroup.loadError') }}
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          @click="() => refetch()"
        >
          {{ t('tenancy.common.retry') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      v-else
      :columns="columns"
      :data="tableData"
      :loading="isLoading"
      row-key="id"
    >
      <template #cell-name="{ row }">
        <button
          type="button"
          class="group-name"
          @click="selectedGroupId = selectedGroupId === row.id ? null : row.id"
        >
          {{ row.name }}
        </button>
        <SBadge
          v-if="selectedGroupId === row.id"
          variant="info"
        >
          {{ t('tenancy.memberGroup.open') }}
        </SBadge>
      </template>

      <template #cell-created_at="{ row }">
        {{ new Date(row.created_at).toLocaleDateString() }}
      </template>

      <template #cell-actions="{ row }">
        <SButton
          v-if="isAuthorized"
          variant="ghost"
          size="sm"
          icon-only
          :aria-label="t('tenancy.memberGroup.deleteTitle')"
          @click="onDelete(row)"
        >
          <TrashIcon class="w-4 h-4" />
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="UserGroupIcon"
          :title="isAuthorized
            ? t('tenancy.memberGroup.emptyOwner')
            : t('tenancy.memberGroup.emptyMember')"
        />
      </template>
    </STable>

    <SCard
      v-if="selectedGroup"
      class="members-card"
    >
      <template #header>
        {{ t('tenancy.memberGroup.membersOf', { name: selectedGroup.name }) }}
      </template>

      <form
        v-if="isAuthorized"
        class="add-form"
        @submit.prevent="onAddMember"
      >
        <SFormField
          :label="t('tenancy.memberGroup.addMember')"
          name="addMember"
          class="add-select"
        >
          <SSelect
            v-model="addUserId"
            :options="addableOptions"
            :placeholder="t('tenancy.memberGroup.addMemberPlaceholder')"
          />
        </SFormField>
        <SButton
          type="submit"
          variant="secondary"
          :disabled="!addUserId"
        >
          {{ t('tenancy.memberGroup.add') }}
        </SButton>
      </form>

      <SLoadingSpinner v-if="membersLoading" />
      <ul
        v-else-if="members && members.length"
        class="member-list"
      >
        <li
          v-for="m in members"
          :key="m.user_id"
        >
          <span>{{ emailByUserId.get(m.user_id) ?? m.user_id }}</span>
          <SButton
            v-if="isAuthorized"
            variant="ghost"
            size="sm"
            @click="onRemoveMember(m.user_id)"
          >
            {{ t('tenancy.memberGroup.remove') }}
          </SButton>
        </li>
      </ul>
      <SEmptyState
        v-else
        :icon="UserGroupIcon"
        :title="t('tenancy.memberGroup.noMembers')"
      />
    </SCard>
  </div>
</template>

<style scoped>
@import '../styles/member-form.css';

.group-name {
  background: none;
  border: none;
  padding: 0;
  color: var(--color-primary-600);
  cursor: pointer;
  font: inherit;
}

.members-card {
  margin-top: var(--space-4);
}

.add-form {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
  margin-bottom: var(--space-3);
}

.add-select {
  flex: 1;
  min-width: 0;
}

.member-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.member-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
</style>
