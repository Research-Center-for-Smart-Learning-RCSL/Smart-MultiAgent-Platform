<script setup lang="ts">
// Agent Group detail (Phase 4α WS1/WS3/WS4). Member management (add/remove,
// Project-Owner-gated), the group-owned Concept Map panel (shared from agents),
// and the wide-layer privacy opt-in (R11.10) — all owner-gated via useProjectRole.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { TrashIcon } from '@heroicons/vue/24/outline'
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

const membersQuery = useQuery({
  queryKey: computed(() => agentGroupKeys.members(groupId)),
  queryFn: async () => (await agentGroupsApi.listMembers(groupId)).members,
})
const memberIds = computed<string[]>(() => membersQuery.data.value ?? [])

const agentsQuery = useQuery({
  queryKey: computed(() => ['agents', 'list', projectId.value]),
  enabled: computed(() => !!projectId.value),
  queryFn: () => agentsApi.list(projectId.value),
})
const agentById = computed(
  () => new Map((agentsQuery.data.value ?? []).map((a) => [a.id, a])),
)
const memberRows = computed(() =>
  memberIds.value.map((id) => ({ id, name: agentById.value.get(id)?.name ?? id })),
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
  const ok = await confirm({
    title: t('agentGroups.detail.removeConfirmTitle'),
    message: t('agentGroups.detail.removeConfirm'),
    variant: 'warning',
  })
  if (ok) removeMutation.mutate(agentId)
}

// --- Privacy opt-in (R11.10) ---
const privacyMutation = useMutation({
  mutationFn: (enabled: boolean) => agentGroupsApi.setConceptMapEnabled(groupId, enabled),
  onSuccess: () => qc.invalidateQueries({ queryKey: agentGroupKeys.group(groupId) }),
  onError: () => toast.error(t('agentGroups.detail.privacyToggleFailed')),
})

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('agentGroups.detail.colAgent') },
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
const MemberTable = typedSTable<{ id: string; name: string }>()
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
      <SPageHeader :title="group.name" />

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
            <template #actions="{ row }">
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
    </template>
  </div>
</template>
