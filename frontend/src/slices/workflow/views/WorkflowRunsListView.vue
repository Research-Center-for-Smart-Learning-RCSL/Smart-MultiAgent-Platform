<template>
  <section class="workflow-runs p-4">
    <SPageHeader :title="$t('workflow.runs.title')">
      <template #prepend>
        <router-link
          :to="{ name: 'workflow.list', params: { workspaceId: route.params.workspaceId } }"
          class="text-sm text-muted hover:underline"
        >
          &larr; {{ $t('workflow.runs.backToList') }}
        </router-link>
      </template>
      <SButton
        v-if="isAuthorized"
        variant="secondary"
        size="sm"
        as="router-link"
        :to="{ name: 'workflow.backstage', params: { workspaceId, workflowId } }"
      >
        {{ $t('workflow.runs.backstage') }}
      </SButton>
      <SButton
        variant="primary"
        size="sm"
        @click="onTrigger"
      >
        {{ $t('workflow.runs.triggerManual') }}
      </SButton>
    </SPageHeader>

    <div class="mb-3">
      <SCheckbox
        id="runs-show-archive"
        v-model="showArchive"
      >
        {{ $t('workflow.runs.includeArchive') }}
      </SCheckbox>
    </div>

    <SAlert
      v-if="query.isError.value"
      variant="danger"
    >
      {{ $t('workflow.runs.loadError') }}
      <template #actions>
        <button
          class="underline"
          @click="query.refetch()"
        >
          {{ $t('workflow.runs.retry') }}
        </button>
      </template>
    </SAlert>
    <RunsTable
      v-else
      :columns="columns"
      :data="runsList"
      :loading="query.isLoading.value"
      :loading-label="$t('workflow.runs.title')"
      row-key="id"
      responsive-mode="card-list"
    >
      <template #cell-state="{ row }">
        <SStatusBadge :status="row.state" />
        <span
          v-if="row.archived"
          class="ml-1 text-2xs text-muted"
        >
          ({{ $t('workflow.runs.archived') }})
        </span>
      </template>
      <template #cell-started_at="{ row }">
        <span class="text-muted">{{ formatDateTime(row.started_at) }}</span>
      </template>
      <template #cell-ended_at="{ row }">
        <span class="text-muted">{{ row.ended_at ? formatDateTime(row.ended_at) : '—' }}</span>
      </template>
      <template #actions="{ row }">
        <router-link
          :to="{ name: 'workflow.run', params: { runId: row.id } }"
          class="text-accent hover:underline text-xs"
        >
          {{ $t('workflow.runs.inspect') }}
        </router-link>
      </template>
      <template #empty>
        <SEmptyState
          :icon="PlayCircleIcon"
          :title="$t('workflow.runs.empty')"
          :text="$t('workflow.runs.emptyHint')"
        />
      </template>
    </RunsTable>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'
import { PlayCircleIcon } from '@heroicons/vue/24/outline'
import { useToast } from '@shared/composables'
import { formatDateTime } from '@shared/utils/datetime'
import {
  SAlert,
  SButton,
  SCheckbox,
  SEmptyState,
  SPageHeader,
  SStatusBadge,
  STable,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { listRuns, triggerRun } from '../api'
import type { WorkflowRun } from '../types'
import { wfKeys } from '../queries'
import { useProjectRole } from '../composables/useProjectRole'

// Same STable row-generic pin as WorkflowListView / agents list.
const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]
const RunsTable = STable as unknown as new () => {
  $props: Omit<STablePropsBase, 'data'> & { data?: WorkflowRun[] }
}

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const qc = useQueryClient()
const workflowId = route.params.workflowId as string
const workspaceId = route.params.workspaceId as string
const showArchive = ref(false)

// Backstage is admin/owner-only; only surface the link to those who can enter.
const { isAuthorized } = useProjectRole(workspaceId)

const columns = computed<Column[]>(() => [
  { key: 'state', label: t('workflow.runs.state'), cellType: 'badge' },
  { key: 'trigger_type', label: t('workflow.runs.trigger'), cellType: 'text' },
  { key: 'started_at', label: t('workflow.runs.started'), cellType: 'date', hideBelow: 'md' },
  { key: 'ended_at', label: t('workflow.runs.ended'), cellType: 'date', hideBelow: 'md' },
])

const query = useQuery({
  queryKey: computed(() => [...wfKeys.runs(workflowId), showArchive.value] as const),
  queryFn: () => listRuns(workflowId, { includeArchive: showArchive.value }),
})

const runsList = computed(() => query.data.value ?? [])

async function onTrigger(): Promise<void> {
  try {
    await triggerRun(workflowId)
    qc.invalidateQueries({ queryKey: wfKeys.runs(workflowId) })
  } catch {
    toast.error(t('workflow.runs.triggerFailed'))
  }
}
</script>
