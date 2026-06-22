<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { SPageHeader } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { tusUpload } from '@shared/transport'
import { agentsApi, RAG_MULTIPART_MAX, type RagDocument } from '../api'
import { agentKeys } from '../queries'
import { useRagConfigSocket } from '../composables/useRagConfigSocket'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const configId = route.params.configId as string
const toast = useToast()
const { confirm } = useConfirmDialog()

// Live ingestion status (ws:rag-configs/{id}) — drives the in-flight badge and
// refetches the document list when the worker reaches a terminal state.
const { progress } = useRagConfigSocket(configId, projectId)

const configQuery = useQuery({
  queryKey: agentKeys.ragConfig(configId),
  queryFn: async () => (await agentsApi.getRagConfig(configId)).data,
})

const docsQuery = useQuery({
  queryKey: agentKeys.ragDocuments(configId),
  queryFn: async () => (await agentsApi.listDocuments(configId)).data,
})

watch(
  () => progress.value.state,
  (state) => {
    if (state === 'ready' || state === 'failed') {
      qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
    }
  },
)

const uploading = ref(false)
const uploadPct = ref(0)
const fileInput = ref<HTMLInputElement | null>(null)

async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  uploadPct.value = 0
  try {
    if (file.size <= RAG_MULTIPART_MAX) {
      // ≤ 32 MB — synchronous multipart; the row lands 'ready' in one request.
      await agentsApi.uploadDocumentMultipart(configId, file)
    } else {
      // Larger — resumable tus; the backend registers the doc and hands it to
      // the rag_ingest_document worker (status flows over the socket).
      await tusUpload({
        file,
        purpose: 'rag_source',
        projectId,
        ragConfigId: configId,
        onProgress: (uploaded, total) => {
          uploadPct.value = total > 0 ? Math.round((uploaded / total) * 100) : 0
        },
      })
    }
    toast.success(t('agents.rag.uploadStarted'))
    qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
  } catch {
    toast.error(t('agents.rag.uploadFailed'))
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteDocument(id),
  onSuccess: () => {
    toast.success(t('agents.rag.deleted'))
    qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
  },
  onError: () => toast.error(t('agents.rag.deleteFailed')),
})

async function confirmDelete(doc: RagDocument): Promise<void> {
  const ok = await confirm({ title: t('agents.rag.deleteTitle'), message: t('agents.rag.deleteConfirm', { name: doc.filename }), variant: 'warning' })
  if (!ok) return
  deleteMutation.mutate(doc.id)
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <section class="rag-config px-4 py-4 sm:p-6">
    <SPageHeader :title="configQuery.data.value?.name ?? t('agents.rag.title')" />
    <p class="rag-config__subtitle mb-4">
      {{ t('agents.rag.subtitle') }}
    </p>

    <div class="rag-config__upload">
      <label class="btn btn-primary">
        {{ uploading ? t('agents.rag.uploading') : t('agents.rag.upload') }}
        <input
          ref="fileInput"
          type="file"
          class="sr-only"
          :disabled="uploading"
          @change="onFile"
        >
      </label>
      <span
        v-if="uploading && uploadPct > 0"
        class="rag-config__pct"
      >{{ uploadPct }}%</span>
    </div>
    <p class="rag-config__hint">
      {{ t('agents.rag.sizeHint') }}
    </p>

    <p v-if="docsQuery.isLoading.value">
      {{ t('agents.rag.loading') }}
    </p>
    <div
      v-else
      class="overflow-x-auto"
    >
    <table class="table">
      <thead>
        <tr>
          <th scope="col">{{ t('agents.rag.colName') }}</th>
          <th scope="col">{{ t('agents.rag.colSize') }}</th>
          <th scope="col">{{ t('agents.rag.colStatus') }}</th>
          <th scope="col">{{ t('agents.rag.colActions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="d in docsQuery.data.value ?? []"
          :key="d.id"
        >
          <td>{{ d.filename }}</td>
          <td>{{ humanSize(d.size_bytes) }}</td>
          <td>{{ t(`agents.rag.statusLabel.${d.status}`) }}</td>
          <td>
            <button
              class="btn btn-danger"
              type="button"
              @click="confirmDelete(d)"
            >
              {{ t('agents.rag.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="(docsQuery.data.value ?? []).length === 0">
          <td colspan="4">
            {{ t('agents.rag.empty') }}
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  </section>
</template>
