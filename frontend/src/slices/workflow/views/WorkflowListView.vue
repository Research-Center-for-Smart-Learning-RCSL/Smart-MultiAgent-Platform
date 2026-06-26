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
        <input
          id="workflow-name"
          v-model="newName"
          required
          minlength="1"
          maxlength="200"
          class="wf-input"
          :placeholder="$t('workflow.list.namePlaceholder')"
        >
      </SFormField>
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value"
      >
        {{ $t('workflow.list.create') }}
      </button>
    </form>

    <SLoadingSpinner
      v-if="query.isLoading.value"
      :label="$t('workflow.list.title')"
      class="justify-center py-8"
    />
    <SAlert
      v-else-if="query.isError.value"
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

    <div
      v-else-if="query.data.value?.length"
      class="overflow-x-auto"
    >
      <table class="table">
        <thead>
          <tr>
            <th scope="col">
              {{ $t('workflow.list.name') }}
            </th>
            <th scope="col">
              {{ $t('workflow.list.version') }}
            </th>
            <th scope="col">
              {{ $t('workflow.list.created') }}
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="wf in query.data.value"
            :key="wf.id"
          >
            <td>
              <router-link
                :to="{ name: 'workflow.editor', params: { workspaceId, workflowId: wf.id } }"
                class="text-accent hover:underline"
              >
                {{ wf.name }}
              </router-link>
            </td>
            <td class="text-muted">
              v{{ wf.version }}
            </td>
            <td class="text-muted">
              {{ formatDate(wf.created_at) }}
            </td>
            <td class="flex gap-2 justify-end">
              <router-link
                :to="{ name: 'workflow.runs', params: { workspaceId, workflowId: wf.id } }"
                class="text-sm text-muted hover:underline"
              >
                {{ $t('workflow.list.runs') }}
              </router-link>
              <button
                class="text-sm text-danger hover:underline"
                @click="onDelete(wf.id)"
              >
                {{ $t('workflow.list.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <SEmptyState
      v-else
      :icon="RectangleGroupIcon"
      :title="$t('workflow.list.empty')"
      :text="$t('workflow.list.emptyHint')"
    />
  </section>
</template>

<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'
import { RectangleGroupIcon } from '@heroicons/vue/24/outline'
import {
  SAlert,
  SEmptyState,
  SFormField,
  SLoadingSpinner,
  SPageHeader,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { formatDate } from '@shared/utils/datetime'
import { createWorkflow, deleteWorkflow, listWorkflows } from '../api'
import { wfKeys } from '../queries'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const qc = useQueryClient()
const workspaceId = route.params.workspaceId as string
const newName = ref('')

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
