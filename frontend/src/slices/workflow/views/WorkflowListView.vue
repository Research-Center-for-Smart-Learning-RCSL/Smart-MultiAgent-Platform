<template>
  <section class="workflow-list">
    <header class="flex items-center justify-between mb-4">
      <h1 class="text-xl font-semibold">{{ $t('workflow.list.title') }}</h1>
      <form class="flex gap-2" @submit.prevent="onCreate">
        <input
          v-model="newName"
          required
          minlength="1"
          maxlength="200"
          class="border rounded px-2 py-1"
          :placeholder="$t('workflow.list.namePlaceholder')"
        />
        <button
          type="submit"
          class="btn btn-primary"
          :disabled="createMutation.isPending.value"
        >
          {{ $t('workflow.list.create') }}
        </button>
      </form>
    </header>

    <p v-if="query.isLoading.value" class="text-gray-500">…</p>
    <p v-else-if="query.isError.value" class="text-red-600">
      {{ $t('workflow.list.loadError') }}
    </p>

    <table v-else-if="query.data.value?.length" class="w-full text-sm">
      <thead>
        <tr class="border-b text-left">
          <th class="py-2">{{ $t('workflow.list.name') }}</th>
          <th class="py-2">{{ $t('workflow.list.version') }}</th>
          <th class="py-2">{{ $t('workflow.list.created') }}</th>
          <th class="py-2" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="wf in query.data.value" :key="wf.id" class="border-b">
          <td class="py-2">
            <router-link
              :to="{ name: 'workflow.editor', params: { workflowId: wf.id } }"
              class="text-blue-600 hover:underline"
            >
              {{ wf.name }}
            </router-link>
          </td>
          <td class="py-2 text-gray-500">v{{ wf.version }}</td>
          <td class="py-2 text-gray-500">
            {{ new Date(wf.created_at).toLocaleDateString() }}
          </td>
          <td class="py-2 flex gap-2 justify-end">
            <router-link
              :to="{ name: 'workflow.runs', params: { workflowId: wf.id } }"
              class="text-sm text-gray-600 hover:underline"
            >
              {{ $t('workflow.list.runs') }}
            </router-link>
            <button
              class="text-sm text-red-600 hover:underline"
              @click="onDelete(wf.id)"
            >
              {{ $t('workflow.list.delete') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="text-gray-400">{{ $t('workflow.list.empty') }}</p>
  </section>
</template>

<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage } from 'element-plus'
import { createWorkflow, deleteWorkflow, listWorkflows } from '../api'
import { wfKeys } from '../queries'

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
      definition: {
        entry_node_id: 'trigger_1',
        nodes: [
          { id: 'trigger_1', type: 'trigger', config: { trigger_type: 'manual' } },
          { id: 'end_1', type: 'end', config: { status: 'success' } },
        ],
        edges: [{ id: 'e1', from: 'trigger_1', to: 'end_1', from_port: 'default' }],
      },
    }),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: wfKeys.workflows(workspaceId) })
  },
  onError: () => ElMessage.error('Failed to create workflow.'),
})

async function onCreate(): Promise<void> {
  const name = newName.value.trim()
  if (!name) return
  await createMutation.mutateAsync(name)
  newName.value = ''
}

async function onDelete(id: string): Promise<void> {
  try {
    await deleteWorkflow(id)
    qc.invalidateQueries({ queryKey: wfKeys.workflows(workspaceId) })
  } catch {
    ElMessage.error('Failed to delete workflow.')
  }
}
</script>
