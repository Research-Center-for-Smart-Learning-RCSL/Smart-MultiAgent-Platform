<script setup lang="ts">
// Agent Group detail (Phase 4α WS1/WS3/WS4). Member management (add/remove,
// Project-Owner-gated), the group-owned Concept Map panel (shared from agents),
// and the wide-layer privacy opt-in (R11.10) — all owner-gated via useProjectRole.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { PencilSquareIcon, TrashIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SCard,
  STable,
  SButton,
  SSelect,
  SToggle,
  SEmptyState,
  SAlert,
  SLoadingSpinner,
  SModal,
  SRadio,
  SCheckbox,
  SBadge,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import { agentsApi, ConceptMapPanel, type Agent } from '@slices/agents'
import { agentGroupsApi } from '../api'
import { agentGroupKeys } from '../queries'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const groupId = route.params.groupId as string

const groupQuery = useQuery({
  queryKey: agentGroupKeys.group(groupId),
  queryFn: () => agentGroupsApi.get(groupId),
})
const group = computed(() => groupQuery.data.value ?? null)
const projectId = computed(() => group.value?.project_id ?? '')

const { isAuthorized, decided } = useProjectRole(() => projectId.value)

// Mutating controls always render (never v-if-hidden) but stay disabled until
// authorization resolves true — avoids both a real owner's controls flashing
// hidden-then-shown (spec §8) and exposing a live, clickable control to a
// non-owner during the loading window. The read-only note still waits for
// `decided` so it never flashes to a confirmed owner/admin.
const showReadonlyNote = computed(() => !isAuthorized.value && decided.value)

const breadcrumbs = computed(() => [
  {
    label: t('agentGroups.list.title'),
    ...(projectId.value && {
      to: { name: 'agentGroups.list', params: { projectId: projectId.value } },
    }),
  },
])

const membersQuery = useQuery({
  queryKey: computed(() => agentGroupKeys.members(groupId)),
  queryFn: () => agentGroupsApi.listMembers(groupId),
})
const memberIds = computed<string[]>(() => membersQuery.data.value?.members ?? [])
// What each member reads from the group map (R11.22): null = whole group.
const readsByAgent = computed(
  () => new Map((membersQuery.data.value?.read_members ?? []).map((r) => [r.agent_id, r.read_member_ids ?? null])),
)

const agentsQuery = useQuery({
  queryKey: computed(() => ['agents', 'list', projectId.value]),
  enabled: computed(() => !!projectId.value),
  queryFn: () => agentsApi.list(projectId.value),
})
const agentById = computed(
  () => new Map((agentsQuery.data.value ?? []).map((a) => [a.id, a])),
)
function agentName(id: string): string {
  return agentById.value.get(id)?.name ?? id
}
const memberRows = computed(() =>
  memberIds.value.map((id) => ({ id, name: agentName(id), reads: readsByAgent.value.get(id) ?? null })),
)
const addableAgents = computed<Agent[]>(() =>
  (agentsQuery.data.value ?? []).filter((a) => !memberIds.value.includes(a.id)),
)
const addableOptions = computed(() => addableAgents.value.map((a) => ({ value: a.id, label: a.name })))

// --- Add / remove members ---
const selectedAgent = ref<string | null>(null)
const addMutation = useMutation({
  mutationFn: (agentId: string) => agentGroupsApi.addMember(groupId, agentId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.members(groupId) })
    selectedAgent.value = null
    toast.success(t('agentGroups.detail.memberAdded'))
  },
  onError: () => toast.error(t('agentGroups.detail.addFailed')),
})
const removeMutation = useMutation({
  mutationFn: (agentId: string) => agentGroupsApi.removeMember(groupId, agentId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.members(groupId) })
    toast.success(t('agentGroups.detail.memberRemoved'))
  },
  onError: () => toast.error(t('agentGroups.detail.removeFailed')),
})
async function confirmRemove(agentId: string): Promise<void> {
  // Members that read only this agent read nothing once it is removed (Q-8):
  // the backend keeps their list empty rather than widening it, so say so.
  const emptied = memberRows.value.filter(
    (row) => row.id !== agentId && row.reads?.length === 1 && row.reads[0] === agentId,
  )
  const ok = await confirm({
    title: t('agentGroups.detail.removeConfirmTitle'),
    message: emptied.length
      ? t('agentGroups.detail.removeConfirmEmpties', { names: emptied.map((row) => row.name).join(', ') })
      : t('agentGroups.detail.removeConfirm'),
    variant: 'warning',
  })
  if (ok) removeMutation.mutate(agentId)
}

// --- What each member reads (R11.22) ---
function readsLabel(reads: string[] | null): string {
  if (reads === null) return t('agentGroups.detail.readsWholeGroup')
  // A filter can still name an agent deleted since it was set; the backend
  // keeps the id (its past contributions stay readable) but it has no name.
  return reads
    .map((id) => (agentById.value.has(id) ? agentName(id) : t('agentGroups.detail.readsRemovedAgent')))
    .join(', ')
}

const editing = ref<{ agentId: string; mode: 'all' | 'some'; chosen: string[] } | null>(null)
const editingName = computed(() => (editing.value ? agentName(editing.value.agentId) : ''))
function openReads(agentId: string): void {
  const reads = readsByAgent.value.get(agentId) ?? null
  // Only current members can be chosen (or saved: the backend refuses any
  // other id), so a deleted agent still named by the filter is dropped here.
  const chosen = (reads ?? []).filter((id) => memberIds.value.includes(id))
  editing.value = { agentId, mode: reads === null ? 'all' : 'some', chosen }
}
function setMode(mode: string): void {
  if (editing.value) editing.value = { ...editing.value, mode: mode === 'some' ? 'some' : 'all' }
}
function toggleChosen(memberId: string, on: boolean): void {
  if (!editing.value) return
  const rest = editing.value.chosen.filter((id) => id !== memberId)
  editing.value = { ...editing.value, chosen: on ? [...rest, memberId] : rest }
}
const readsInvalid = computed(() => editing.value?.mode === 'some' && editing.value.chosen.length === 0)

const readsMutation = useMutation({
  mutationFn: (payload: { agentId: string; memberIds: string[] | null }) =>
    agentGroupsApi.setReadMembers(groupId, payload.agentId, payload.memberIds),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentGroupKeys.members(groupId) })
    editing.value = null
    toast.success(t('agentGroups.detail.readsSaved'))
  },
  onError: () => toast.error(t('agentGroups.detail.readsFailed')),
})
function saveReads(): void {
  if (!editing.value || readsInvalid.value) return
  readsMutation.mutate({
    agentId: editing.value.agentId,
    memberIds: editing.value.mode === 'all' ? null : [...editing.value.chosen],
  })
}

// --- Privacy opt-in (R11.10) ---
const privacyMutation = useMutation({
  mutationFn: (enabled: boolean) => agentGroupsApi.setConceptMapEnabled(groupId, enabled),
  onSuccess: () => qc.invalidateQueries({ queryKey: agentGroupKeys.group(groupId) }),
  onError: () => toast.error(t('agentGroups.detail.privacyToggleFailed')),
})

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('agentGroups.detail.colAgent') },
  { key: 'reads', label: t('agentGroups.detail.colReadsFrom') },
  { key: 'actions', label: '', width: '120px', align: 'right' },
])

const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
function typedSTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & { data?: T[] }
    $slots: { [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown }
  }
}
const MemberTable = typedSTable<{ id: string; name: string; reads: string[] | null }>()
</script>

<template>
  <div>
    <div
      v-if="groupQuery.isLoading.value"
      class="flex justify-center py-16"
    >
      <SLoadingSpinner />
    </div>
    <SAlert
      v-else-if="!group"
      variant="danger"
      class="mt-4"
    >
      {{ t('agentGroups.detail.notFound') }}
    </SAlert>

    <template v-else>
      <SPageHeader
        :title="group.name"
        :breadcrumbs="breadcrumbs"
      />

      <div class="mt-6 space-y-6 max-w-2xl">
        <!-- Members -->
        <SCard>
          <h2 class="text-lg font-semibold mb-1">
            {{ t('agentGroups.detail.membersTitle') }}
          </h2>
          <p class="text-sm text-[var(--color-muted)] mb-4">
            {{ t('agentGroups.detail.membersHelp') }}
          </p>

          <div class="flex items-end gap-2 mb-4">
            <div class="flex-1">
              <SSelect
                v-model="selectedAgent"
                :options="addableOptions"
                :disabled="!isAuthorized"
                :placeholder="t('agentGroups.detail.memberPlaceholder')"
              />
            </div>
            <SButton
              variant="primary"
              :disabled="!selectedAgent || !isAuthorized"
              :loading="addMutation.isPending.value"
              @click="selectedAgent && addMutation.mutate(selectedAgent)"
            >
              {{ t('agentGroups.detail.addMember') }}
            </SButton>
          </div>
          <p
            v-if="showReadonlyNote"
            class="text-sm text-[var(--color-muted)] mb-4"
          >
            {{ t('agentGroups.detail.ownerOnly') }}
          </p>

          <MemberTable
            :columns="columns"
            :data="memberRows"
            :loading="membersQuery.isLoading.value"
            row-key="id"
          >
            <template #cell-name="{ row }">
              <span class="font-medium">{{ row.name }}</span>
            </template>
            <template #cell-reads="{ row }">
              <div
                v-if="row.reads !== null && row.reads.length === 0"
                class="space-y-1"
              >
                <SBadge
                  variant="warning"
                  size="sm"
                >
                  {{ t('agentGroups.detail.readsNothing') }}
                </SBadge>
                <p class="text-xs text-[var(--color-muted)]">
                  {{ t('agentGroups.detail.readsNothingHelp') }}
                </p>
              </div>
              <span
                v-else
                class="text-sm"
              >{{ readsLabel(row.reads) }}</span>
            </template>
            <template #actions="{ row }">
              <SButton
                variant="ghost"
                size="sm"
                icon-only
                :disabled="!isAuthorized"
                :aria-label="t('agentGroups.detail.editReads')"
                @click="openReads(row.id)"
              >
                <PencilSquareIcon class="w-4 h-4" />
              </SButton>
              <SButton
                variant="ghost"
                size="sm"
                icon-only
                :disabled="!isAuthorized"
                :aria-label="t('agentGroups.detail.removeMember')"
                @click="confirmRemove(row.id)"
              >
                <TrashIcon class="w-4 h-4" />
              </SButton>
            </template>
            <template #empty>
              <SEmptyState :title="t('agentGroups.detail.noMembers')" />
            </template>
          </MemberTable>
        </SCard>

        <!-- Privacy opt-in (wide layer) -->
        <SCard>
          <h2 class="text-lg font-semibold mb-1">
            {{ t('agentGroups.detail.privacyTitle') }}
          </h2>
          <div class="flex items-center justify-between gap-4 mt-3">
            <div class="min-w-0">
              <p class="font-medium">
                {{ t('agentGroups.detail.privacyLabel') }}
              </p>
              <p class="text-sm text-[var(--color-muted)]">
                {{ t('agentGroups.detail.privacyHelp') }}
              </p>
            </div>
            <SToggle
              :model-value="group.concept_map_enabled"
              :disabled="!isAuthorized || privacyMutation.isPending.value"
              @update:model-value="(v: boolean) => privacyMutation.mutate(v)"
            />
          </div>
          <p
            v-if="showReadonlyNote"
            class="text-sm text-[var(--color-muted)] mt-2"
          >
            {{ t('agentGroups.detail.privacyOwnerOnly') }}
          </p>
        </SCard>

        <!-- Group-owned Concept Map (shared panel) -->
        <ConceptMapPanel
          v-if="projectId"
          :project-id="projectId"
          owner-kind="agent_group"
          :owner-id="groupId"
        />
      </div>

      <SModal
        :open="editing !== null"
        :title="t('agentGroups.detail.readsTitle', { name: editingName })"
        @close="editing = null"
      >
        <template v-if="editing">
          <p class="text-sm text-[var(--color-muted)] mb-4">
            {{ t('agentGroups.detail.readsHelp') }}
          </p>
          <div
            class="space-y-2"
            role="radiogroup"
          >
            <SRadio
              :model-value="editing.mode"
              value="all"
              name="reads-mode"
              @update:model-value="setMode"
            >
              {{ t('agentGroups.detail.readsModeAll') }}
            </SRadio>
            <SRadio
              :model-value="editing.mode"
              value="some"
              name="reads-mode"
              @update:model-value="setMode"
            >
              {{ t('agentGroups.detail.readsModeSome') }}
            </SRadio>
          </div>
          <div
            v-if="editing.mode === 'some'"
            class="mt-3 ml-6 space-y-2"
          >
            <SCheckbox
              v-for="row in memberRows"
              :key="row.id"
              :model-value="editing.chosen.includes(row.id)"
              @update:model-value="(on: boolean) => toggleChosen(row.id, on)"
            >
              {{ row.name }}
            </SCheckbox>
            <p
              v-if="readsInvalid"
              class="text-sm text-[var(--color-danger)]"
            >
              {{ t('agentGroups.detail.readsChooseOne') }}
            </p>
          </div>
        </template>
        <template #footer>
          <SButton
            variant="secondary"
            @click="editing = null"
          >
            {{ t('agentGroups.detail.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            :disabled="readsInvalid || !isAuthorized"
            :loading="readsMutation.isPending.value"
            @click="saveReads"
          >
            {{ t('agentGroups.detail.save') }}
          </SButton>
        </template>
      </SModal>
    </template>
  </div>
</template>
