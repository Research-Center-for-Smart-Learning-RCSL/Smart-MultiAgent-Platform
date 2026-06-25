<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  PlusIcon,
  PencilSquareIcon,
  DocumentDuplicateIcon,
  TrashIcon,
  CpuChipIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SSearchInput,
  SSelect,
  STable,
  SBadge,
  SButton,
  SDropdown,
  SEmptyState,
  SAlert,
  SPagination,
  SCard,
} from '@shared/ui'
import {
  useConfirmDialog,
  useToast,
  useClientPagination,
  useBreakpoint,
} from '@shared/composables'
import { keyGroupsApi, keysKeys, type KeyGroup } from '@slices/keys'
import { agentsApi, type Agent } from '../api'
import { agentKeys } from '../queries'
import type { AgentCreateInput } from '../types/schemas'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const { isMobile } = useBreakpoint()
const projectId = route.params.projectId as string

const search = ref('')
const modelFilter = ref<string>('')

const query = useQuery({
  queryKey: agentKeys.agents(projectId),
  queryFn: async () => (await agentsApi.list(projectId)).data,
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(projectId),
  queryFn: async () => (await keyGroupsApi.listForProject(projectId)).data,
})

const ragConfigsQuery = useQuery({
  queryKey: agentKeys.ragConfigs(projectId),
  queryFn: async () => (await agentsApi.listRagConfigs(projectId)).data,
})

const agents = computed<Agent[]>(() => query.data.value ?? [])
const loading = computed(() => query.isLoading.value)
const error = computed(() => query.error.value)

const keyGroupById = computed(() =>
  new Map((keyGroupsQuery.data.value ?? []).map((g: KeyGroup) => [g.id, g.name])),
)
const ragConfigById = computed(() =>
  new Map((ragConfigsQuery.data.value ?? []).map((c) => [c.id, c.name])),
)

const filteredAgents = computed(() => {
  let items = agents.value
  if (search.value) {
    const q = search.value.toLowerCase()
    items = items.filter((a) => a.name.toLowerCase().includes(q))
  }
  if (modelFilter.value) {
    items = items.filter((a) => a.model_hint === modelFilter.value)
  }
  return items
})

const { currentPage, totalPages, paginatedItems, pageSize } =
  useClientPagination(filteredAgents)

const modelOptions = computed(() => [
  { value: '', label: t('agents.list.filterAll') },
  { value: 'claude', label: t('agents.form.modelHints.claude') },
  { value: 'openai', label: t('agents.form.modelHints.openai') },
  { value: 'gemini', label: t('agents.form.modelHints.gemini') },
])

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('agents.form.name') },
  { key: 'model_hint', label: t('agents.form.modelHint'), width: '100px' },
  { key: 'key_group_id', label: t('agents.form.keyGroup'), width: '140px' },
  { key: 'rag_config_id', label: t('agents.form.ragConfig'), width: '100px' },
  { key: 'a2a_enabled', label: 'A2A', width: '60px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])

const actionItems = computed(() => [
  { key: 'edit', label: t('common.edit', 'Edit'), icon: PencilSquareIcon },
  { key: 'duplicate', label: t('agents.list.duplicate'), icon: DocumentDuplicateIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('common.delete', 'Delete'), icon: TrashIcon, danger: true },
])

function goToAgent(agentId: string): void {
  router.push({ name: 'agents.detail', params: { agentId } })
}

function goToCreate(): void {
  router.push({ name: 'agents.detail', params: { agentId: 'new' }, query: { projectId } })
}

const deleteMutation = useMutation({
  mutationFn: (agent: Agent) => agentsApi.remove(agent.id, agent.version),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.agents(projectId) })
    toast.success(t('agents.list.deleted'))
  },
  onError: () => toast.error(t('agents.list.deleteFailed')),
})

async function confirmDelete(agent: Agent): Promise<void> {
  const ok = await confirm({
    title: t('agents.detail.deleteConfirmTitle'),
    message: t('agents.list.confirmDelete'),
    variant: 'error',
    confirmLabel: t('agents.detail.delete'),
  })
  if (!ok) return
  deleteMutation.mutate(agent)
}

const duplicateMutation = useMutation({
  mutationFn: (agent: Agent) =>
    agentsApi.create(projectId, {
      name: `${agent.name} (copy)`,
      model_hint: agent.model_hint as AgentCreateInput['model_hint'],
      model_id: agent.model_id ?? null,
      key_group_id: agent.key_group_id,
      system_prompt: agent.system_prompt,
      prompt_strategy: agent.prompt_strategy as AgentCreateInput['prompt_strategy'],
      rag_config_id: agent.rag_config_id,
      graphrag_config_id: null,
      context_mode: agent.context_mode as AgentCreateInput['context_mode'],
      context_token_cap: agent.context_token_cap,
      a2a_enabled: agent.a2a_enabled,
    }),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.agents(projectId) })
    toast.success(t('agents.list.duplicated'))
  },
  onError: () => toast.error(t('agents.list.createFailed')),
})

function onAction(key: string, row: Agent): void {
  if (key === 'edit') goToAgent(row.id)
  else if (key === 'duplicate') duplicateMutation.mutate(row)
  else if (key === 'delete') void confirmDelete(row)
}

function onRowClick(row: Agent): void {
  goToAgent(row.id)
}
</script>

<template>
  <main class="p-6">
    <SPageHeader :title="t('agents.list.title')">
      <template #actions>
        <SButton
          variant="primary"
          @click="goToCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agents.list.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div class="flex flex-wrap items-center gap-4 mt-6">
      <SSearchInput
        v-model="search"
        :placeholder="t('agents.list.searchPlaceholder')"
        class="w-64"
      />
      <SSelect
        v-model="modelFilter"
        :options="modelOptions"
        size="sm"
        class="w-36"
      />
    </div>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ t('agents.list.loadError') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="query.refetch()"
        >
          {{ t('agents.detail.reload') }}
        </SButton>
      </template>
    </SAlert>

    <!-- Mobile: card layout -->
    <div
      v-if="isMobile && !loading && filteredAgents.length > 0"
      class="mt-6 space-y-3"
    >
      <SCard
        v-for="agent in paginatedItems"
        :key="agent.id"
        class="cursor-pointer"
        @click="goToAgent(agent.id)"
      >
        <div class="flex items-center justify-between">
          <div>
            <p class="font-medium">
              {{ agent.name }}
            </p>
            <SBadge
              variant="neutral"
              size="sm"
              class="mt-1"
            >
              {{ agent.model_hint }}
            </SBadge>
          </div>
          <SDropdown
            :items="actionItems"
            placement="bottom-end"
            @select="onAction($event, agent)"
          >
            <template #trigger>
              <SButton
                variant="ghost"
                icon-only
                size="sm"
                @click.stop
              >
                <EllipsisVerticalIcon class="w-4 h-4" />
              </SButton>
            </template>
          </SDropdown>
        </div>
      </SCard>
    </div>

    <!-- Desktop: table layout -->
    <STable
      v-if="!isMobile"
      :columns="columns"
      :data="paginatedItems"
      :loading="loading"
      row-key="id"
      sticky-header
      class="mt-6"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <span class="font-medium cursor-pointer text-[var(--color-accent)]">
          {{ row.name }}
        </span>
      </template>

      <template #cell-model_hint="{ row }">
        <SBadge variant="neutral">
          {{ row.model_hint }}
        </SBadge>
      </template>

      <template #cell-key_group_id="{ row }">
        {{ keyGroupById.get(row.key_group_id) ?? '--' }}
      </template>

      <template #cell-rag_config_id="{ row }">
        <template v-if="row.rag_config_id">
          {{ ragConfigById.get(row.rag_config_id) ?? '--' }}
        </template>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >--</span>
      </template>

      <template #cell-a2a_enabled="{ row }">
        <SBadge
          v-if="row.a2a_enabled"
          variant="success"
        >
          {{ t('agents.list.a2aOn') }}
        </SBadge>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >--</span>
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
          :icon="CpuChipIcon"
          :title="t('agents.list.emptyTitle')"
          :text="t('agents.list.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="goToCreate"
            >
              {{ t('agents.list.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <SPagination
      v-if="filteredAgents.length > pageSize"
      :page="currentPage"
      :total-pages="totalPages"
      :total-items="filteredAgents.length"
      :page-size="pageSize"
      class="mt-4"
      @update:page="currentPage = $event"
    />
  </main>
</template>
