import { computed, ref, watch } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType, tusUpload } from '@shared/transport'
import { agentsApi, RAG_MULTIPART_MAX, type Agent, type RagDocument } from '../api'
import { agentKeys } from '../queries'

export function useRagDocuments(configId: string, projectId: string) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const toast = useToast()
  const { confirm } = useConfirmDialog()
  const docsQuery = useQuery({
    queryKey: agentKeys.ragDocuments(configId),
    queryFn: () => agentsApi.listDocuments(configId),
  })
  const agentsQuery = useQuery({
    queryKey: agentKeys.agents(projectId),
    queryFn: () => agentsApi.list(projectId),
  })
  const docs = computed<RagDocument[]>(() => docsQuery.data.value ?? [])
  const boundAgents = computed<Agent[]>(() =>
    (agentsQuery.data.value ?? []).filter((agent) => agent.rag_config_id === configId),
  )
  const uploadAgentIds = ref<string[]>([])
  const uploadAgentsSeeded = ref(false)
  watch(
    boundAgents,
    (agents) => {
      if (!uploadAgentsSeeded.value && agents.length) {
        uploadAgentIds.value = agents.map((agent) => agent.id)
        uploadAgentsSeeded.value = true
      }
    },
    { immediate: true },
  )
  const editDoc = ref<RagDocument | null>(null)
  const editAgentIds = ref<string[]>([])
  const uploading = ref(false)

  function toggleUploadAgent(id: string, on: boolean): void {
    uploadAgentIds.value = on
      ? [...new Set([...uploadAgentIds.value, id])]
      : uploadAgentIds.value.filter((candidate) => candidate !== id)
  }
  function openAgentsEditor(doc: RagDocument): void {
    editDoc.value = doc
    editAgentIds.value = [...doc.agent_ids]
  }
  function toggleEditAgent(id: string, on: boolean): void {
    editAgentIds.value = on
      ? [...new Set([...editAgentIds.value, id])]
      : editAgentIds.value.filter((candidate) => candidate !== id)
  }

  const setAgentsMutation = useMutation({
    mutationFn: async () => {
      if (!editDoc.value) return
      await agentsApi.setDocumentAgents(editDoc.value.id, [...editAgentIds.value])
    },
    onSuccess: () => {
      editDoc.value = null
      toast.success(t('agents.rag.agentsSaved'))
      qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
    },
    onError: () => toast.error(t('agents.rag.agentsSaveFailed')),
  })

  async function onFiles(files: File[]): Promise<void> {
    uploading.value = true
    let accepted = 0
    const agentIds = [...uploadAgentIds.value]
    try {
      for (const file of files) {
        if (file.size <= RAG_MULTIPART_MAX) {
          await agentsApi.uploadDocumentMultipart(configId, file, agentIds)
        } else {
          await tusUpload({
            file,
            purpose: 'rag_source',
            projectId,
            ragConfigId: configId,
            ragAgentIds: agentIds,
          })
        }
        accepted += 1
      }
      toast.success(t('agents.rag.uploadStarted'))
    } catch (error) {
      if (isProblemWithType(error, '/document-unprocessable')) {
        toast.error(t('agents.rag.uploadUnprocessable'))
      } else if (isProblemWithType(error, '/document-allowlist-conflict')) {
        toast.error(t('agents.rag.uploadAllowlistConflict'))
      } else {
        toast.error(t('agents.rag.uploadFailed'))
      }
    } finally {
      if (accepted > 0) {
        await qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
      }
      uploading.value = false
    }
  }

  const deleteDocMutation = useMutation({
    mutationFn: (id: string) => agentsApi.deleteDocument(id),
    onSuccess: () => {
      toast.success(t('agents.rag.deleted'))
      qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
    },
    onError: () => toast.error(t('agents.rag.deleteFailed')),
  })
  async function confirmDeleteDoc(doc: RagDocument): Promise<void> {
    const ok = await confirm({
      title: t('agents.rag.deleteTitle'),
      message: t('agents.rag.deleteConfirm', { name: doc.filename }),
      variant: 'warning',
    })
    if (ok) deleteDocMutation.mutate(doc.id)
  }

  return {
    boundAgents,
    confirmDeleteDoc,
    docs,
    docsQuery,
    editAgentIds,
    editDoc,
    onFiles,
    openAgentsEditor,
    setAgentsMutation,
    toggleEditAgent,
    toggleUploadAgent,
    uploadAgentIds,
    uploading,
  }
}
