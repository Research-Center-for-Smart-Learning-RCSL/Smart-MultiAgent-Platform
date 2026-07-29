<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { DocumentIcon, TrashIcon, UserGroupIcon } from '@heroicons/vue/24/outline'
import {
  SBadge,
  SButton,
  SCard,
  SCheckbox,
  SEmptyState,
  SFileUpload,
  SModal,
  SProgressBar,
  typedTable,
} from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import type { Agent, RagDocument } from '../api'
import type { RagIngestionProgress } from '../composables/useRagConfigSocket'

const props = defineProps<{
  boundAgents: Agent[]
  docs: RagDocument[]
  editAgentIds: string[]
  editDoc: RagDocument | null
  loading: boolean
  progress: RagIngestionProgress
  savingAgents: boolean
  uploadAgentIds: string[]
  uploading: boolean
}>()
const emit = defineEmits<{
  closeEditor: []
  deleteDocument: [document: RagDocument]
  files: [files: File[]]
  openEditor: [document: RagDocument]
  saveAgents: []
  toggleEditAgent: [id: string, selected: boolean]
  toggleUploadAgent: [id: string, selected: boolean]
}>()
const { t } = useI18n()
const columns = computed<Column[]>(() => [
  { key: 'filename', label: t('agents.rag.colName') },
  { key: 'size_bytes', label: t('agents.rag.colSize'), width: '80px' },
  { key: 'status', label: t('agents.rag.colStatus'), width: '100px' },
  { key: 'scan_status', label: t('agents.rag.colScanned'), width: '100px' },
  { key: 'agents', label: t('agents.rag.colAgents'), width: '140px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])
const DocsTable = typedTable<RagDocument>()
const showProgress = computed(() => ['ingesting', 'indexing'].includes(props.progress.state))
const progressValue = computed(() =>
  props.progress.state === 'ingesting' && props.progress.documentsTotal > 0
    ? Math.round((props.progress.documentsProcessed / props.progress.documentsTotal) * 100)
    : 0,
)
const progressText = computed(() => {
  if (props.progress.state === 'ingesting' && props.progress.documentsTotal > 0) {
    return t('agents.rag.ingestionProgress', {
      processed: props.progress.documentsProcessed,
      total: props.progress.documentsTotal,
    })
  }
  if (props.progress.state === 'indexing') return t('agents.rag.indexing')
  if (props.progress.state === 'ingesting') return t('agents.rag.ingestionStarted')
  return ''
})
function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
function statusVariant(status: string): 'info' | 'success' | 'danger' | 'warning' {
  return { ingesting: 'info', ready: 'success', failed: 'danger', quarantined: 'warning' }[
    status
  ] as 'info' | 'success' | 'danger' | 'warning'
}
function scanVariant(status: string): 'neutral' | 'success' | 'danger' {
  return { pending: 'neutral', clean: 'success', quarantined: 'danger', skipped: 'neutral' }[
    status
  ] as 'neutral' | 'success' | 'danger'
}
</script>

<template>
  <div class="mt-6 space-y-6">
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.rag.upload') }}
      </h3>
      <SFileUpload
        accept=".pdf,.txt,.md,.docx,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        :max-size="33554432"
        multiple
        :disabled="uploading"
        @files="emit('files', $event)"
      >
        <p class="text-sm text-[var(--color-muted)]">
          {{ t('agents.rag.sizeHint') }}
        </p>
      </SFileUpload>
      <div class="mt-4">
        <p class="text-sm font-medium mb-1">
          {{ t('agents.rag.visibleToAgents') }}
        </p>
        <p class="text-sm text-[var(--color-muted)] mb-2">
          {{ t('agents.rag.visibleToAgentsHint') }}
        </p>
        <p
          v-if="boundAgents.length === 0"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('agents.rag.noBoundAgents') }}
        </p>
        <div
          v-else
          class="flex flex-col gap-1"
        >
          <SCheckbox
            v-for="agent in boundAgents"
            :key="agent.id"
            :model-value="uploadAgentIds.includes(agent.id)"
            @update:model-value="emit('toggleUploadAgent', agent.id, $event)"
          >
            {{ agent.name }}
          </SCheckbox>
        </div>
      </div>
    </SCard>
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.ragForm.tabs.documents') }}
      </h3>
      <DocsTable
        :columns="columns"
        :data="docs"
        :loading="loading"
        row-key="id"
      >
        <template #cell-size_bytes="{ row }">
          {{ humanSize(row.size_bytes) }}
        </template>
        <template #cell-status="{ row }">
          <SBadge :variant="statusVariant(row.status)">
            {{
              row.failure_code
                ? t(`agents.rag.failure.${row.failure_code}`)
                : t(`agents.rag.status.${row.status}`)
            }}
          </SBadge>
        </template>
        <template #cell-scan_status="{ row }">
          <SBadge :variant="scanVariant(row.scan_status)">
            {{ t(`agents.rag.scan.${row.scan_status}`) }}
          </SBadge>
        </template>
        <template #cell-agents="{ row }">
          <SButton
            variant="ghost"
            size="sm"
            @click="emit('openEditor', row)"
          >
            <template #icon-left>
              <UserGroupIcon class="w-4 h-4" />
            </template>
            <span :class="{ 'text-[var(--color-warning)]': row.agent_ids.length === 0 }">
              {{
                row.agent_ids.length === 0
                  ? t('agents.rag.agentsNone')
                  : t('agents.rag.agentsCount', { count: row.agent_ids.length })
              }}
            </span>
          </SButton>
        </template>
        <template #actions="{ row }">
          <SButton
            variant="ghost"
            icon-only
            size="sm"
            @click="emit('deleteDocument', row)"
          >
            <TrashIcon class="w-4 h-4 text-[var(--color-danger)]" />
          </SButton>
        </template>
        <template #empty>
          <SEmptyState
            :icon="DocumentIcon"
            :title="t('agents.rag.emptyTitle')"
            :text="t('agents.rag.emptyDescription')"
          />
        </template>
      </DocsTable>
      <div
        v-if="showProgress"
        class="mt-4"
      >
        <SProgressBar
          :value="progressValue"
          :indeterminate="
            progress.state === 'indexing' ||
              (progress.state === 'ingesting' && progress.documentsTotal === 0)
          "
          variant="info"
        />
        <p class="text-sm text-[var(--color-muted)] mt-1">
          {{ progressText }}
        </p>
      </div>
    </SCard>
  </div>
  <SModal
    :open="editDoc !== null"
    :title="t('agents.rag.agentsModalTitle')"
    size="md"
    @close="emit('closeEditor')"
  >
    <p class="text-sm text-[var(--color-muted)] mb-3">
      {{ t('agents.rag.visibleToAgentsHint') }}
    </p>
    <p
      v-if="boundAgents.length === 0"
      class="text-sm text-[var(--color-muted)]"
    >
      {{ t('agents.rag.noBoundAgents') }}
    </p>
    <div
      v-else
      class="flex flex-col gap-1"
    >
      <SCheckbox
        v-for="agent in boundAgents"
        :key="agent.id"
        :model-value="editAgentIds.includes(agent.id)"
        @update:model-value="emit('toggleEditAgent', agent.id, $event)"
      >
        {{ agent.name }}
      </SCheckbox>
    </div>
    <template #footer>
      <div class="flex justify-end gap-3">
        <SButton
          variant="secondary"
          @click="emit('closeEditor')"
        >
          {{ t('agents.ragList.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="savingAgents"
          @click="emit('saveAgents')"
        >
          {{ t('agents.detail.save') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
