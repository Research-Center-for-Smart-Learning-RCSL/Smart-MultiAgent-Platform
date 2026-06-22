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
      <button
        class="btn btn-primary btn-sm"
        @click="onTrigger"
      >
        {{ $t('workflow.runs.triggerManual') }}
      </button>
    </SPageHeader>

    <div class="mb-3">
      <label class="text-xs text-muted flex items-center gap-1">
        <input
          v-model="showArchive"
          type="checkbox"
        >
        {{ $t('workflow.runs.includeArchive') }}
      </label>
    </div>

    <p
      v-if="query.isLoading.value"
      class="text-muted"
    >
      …
    </p>
    <div
      v-else-if="runsList.length"
      class="overflow-x-auto"
    >
      <table class="table">
        <thead>
          <tr>
            <th scope="col">
              {{ $t('workflow.runs.state') }}
            </th>
            <th scope="col">
              {{ $t('workflow.runs.trigger') }}
            </th>
            <th scope="col">
              {{ $t('workflow.runs.started') }}
            </th>
            <th scope="col">
              {{ $t('workflow.runs.ended') }}
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in runsList"
            :key="r.id"
          >
            <td>
              <SStatusBadge :status="r.state" />
              <span
                v-if="r.archived"
                class="ml-1 text-2xs text-muted"
              >
                ({{ $t('workflow.runs.archived') }})
              </span>
            </td>
            <td>
              {{ r.trigger_type }}
            </td>
            <td class="text-muted">
              {{ new Date(r.started_at).toLocaleString() }}
            </td>
            <td class="text-muted">
              {{ r.ended_at ? new Date(r.ended_at).toLocaleString() : '—' }}
            </td>
            <td>
              <router-link
                :to="{ name: 'workflow.run', params: { runId: r.id } }"
                class="text-accent hover:underline text-xs"
              >
                {{ $t('workflow.runs.inspect') }}
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p
      v-else
      class="text-muted"
    >
      {{ $t('workflow.runs.empty') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { SPageHeader, SStatusBadge } from '@shared/ui'
import { listRuns, triggerRun } from '../api'
import { wfKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const qc = useQueryClient()
const workflowId = route.params.workflowId as string
const showArchive = ref(false)

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
