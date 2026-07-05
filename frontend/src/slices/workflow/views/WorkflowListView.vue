<template>
  <section class="workflow-list">
    <SPageHeader :title="$t('workflow.list.title')" />

    <!-- Inline create form (spec 1.3) -->
    <form
      class="flex items-end gap-2 mb-4"
      @submit.prevent="onCreate"
    >
      <SFormField
        :label="$t('workflow.list.name')"
        name="workflow-name"
        class="flex-1 max-w-sm"
      >
        <SInput
          id="workflow-name"
          v-model="newName"
          :maxlength="INPUT_LIMITS.NAME"
          :placeholder="$t('workflow.list.namePlaceholder')"
        />
      </SFormField>
      <SButton
        type="submit"
        variant="primary"
        :loading="createMutation.isPending.value"
      >
        {{ $t('workflow.list.create') }}
      </SButton>
    </form>

    <SAlert
      v-if="query.isError.value"
      variant="danger"
    >
      {{ $t('workflow.list.loadError') }}
      <template #actions>
        <button
          class="underline"
          @click="query.refetch()"
        >
          {{ $t('workflow.list.retry') }}
        </button>
      </template>
    </SAlert>

    <WorkflowTable
      v-else
      :columns="columns"
      :data="query.data.value ?? []"
      :loading="query.isLoading.value"
      :loading-label="$t('workflow.list.title')"
      row-key="id"
      responsive-mode="card-list"
    >
      <template #cell-name="{ row }">
        <router-link
          :to="{ name: 'workflow.editor', params: { workspaceId, workflowId: row.id } }"
          class="text-accent hover:underline"
        >
          {{ row.name }}
        </router-link>
      </template>
      <template #cell-version="{ row }">
        <span class="text-muted">v{{ row.version }}</span>
      </template>
      <template #cell-created_at="{ row }">
        <span class="text-muted">{{ formatDate(row.created_at) }}</span>
      </template>
      <template #actions="{ row }">
        <div class="flex gap-2 justify-end">
          <router-link
            :to="{ name: 'workflow.runs', params: { workspaceId, workflowId: row.id } }"
            class="text-sm text-muted hover:underline"
          >
            {{ $t('workflow.list.runs') }}
          </router-link>
          <button
            class="text-sm text-danger hover:underline"
            @click="onDelete(row.id)"
          >
            {{ $t('workflow.list.delete') }}
          </button>
        </div>
      </template>
      <template #empty>
        <SEmptyState
          :icon="RectangleGroupIcon"
          :title="$t('workflow.list.empty')"
          :text="$t('workflow.list.emptyHint')"
        />
      </template>
    </WorkflowTable>
  </section>
</template>

<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'
import { RectangleGroupIcon } from '@heroicons/vue/24/outline'
import {
  SAlert,
  SButton,
  SEmptyState,
  SFormField,
  SInput,
  SPageHeader,
  STable,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { useConfirmDialog, useToast } from '@shared/composables'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { formatDate } from '@shared/utils/datetime'
import { createWorkflow, deleteWorkflow, listWorkflows } from '../api'
import type { Workflow } from '../types'
import { wfKeys } from '../queries'

// STable's row generic does not infer through script-setup usage — pin the row
// type explicitly so #cell-* slot props are typed (same workaround as
// agents/AgentListView.vue).
const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
const WorkflowTable = STable as unknown as new () => {
  $props: Omit<STablePropsBase, 'data'> & { data?: Workflow[] }
}

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const qc = useQueryClient()
const workspaceId = route.params.workspaceId as string
const newName = ref('')

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('workflow.list.name'), cellType: 'text' },
  { key: 'version', label: t('workflow.list.version'), cellType: 'badge' },
  { key: 'created_at', label: t('workflow.list.created'), cellType: 'date', hideBelow: 'md' },
])

const query = useQuery({
  queryKey: wfKeys.workflows(workspaceId),
  queryFn: () => listWorkflows(workspaceId),
})

const createMutation = useMutation({
  mutationFn: (name: string) =>
    createWorkflow(workspaceId, {
      name,
      // Must satisfy docs/workflow.schema.json: schema_version "1.0", name,
      // node positions, and a manual trigger's required allowed_roles — else the
      // backend rejects the create with 422.
      definition: {
        schema_version: '1.0',
        name,
        entry_node_id: 'trigger_1',
        nodes: [
          { id: 'trigger_1', type: 'trigger', config: { trigger_type: 'manual', allowed_roles: ['Admin'] }, position: { x: 0, y: 0 } },
          { id: 'end_1', type: 'end', config: { status: 'success' }, position: { x: 240, y: 0 } },
        ],
        edges: [{ id: 'e1', from: 'trigger_1', to: 'end_1', from_port: 'default' }],
      },
    }),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: wfKeys.workflows(workspaceId) })
    toast.success(t('workflow.list.createSuccess'))
  },
  onError: () => toast.error(t('workflow.list.createFailed')),
})

async function onCreate(): Promise<void> {
  const name = newName.value.trim()
  if (!name) return
  await createMutation.mutateAsync(name)
  newName.value = ''
}

async function onDelete(id: string): Promise<void> {
  const ok = await confirm({
    title: t('workflow.list.deleteConfirmTitle'),
    message: t('workflow.list.deleteConfirm'),
    confirmLabel: t('workflow.list.delete'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await deleteWorkflow(id)
    qc.invalidateQueries({ queryKey: wfKeys.workflows(workspaceId) })
  } catch {
    toast.error(t('workflow.list.deleteFailed'))
  }
}
</script>
