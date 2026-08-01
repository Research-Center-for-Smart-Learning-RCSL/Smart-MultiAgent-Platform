import { computed, ref, watch, type Ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType, tusUpload } from '@shared/transport'
import {
  agentsApi,
  RAG_MULTIPART_MAX,
  type Agent,
  type GraphragBuildState,
  type KnowmapDocument,
} from '../api'
import { agentKeys } from '../queries'

export function useKnowmapDocuments(
  configId: string,
  projectId: string,
  effectiveState: Ref<GraphragBuildState>,
  watchBuild: (configId: string, initialState?: GraphragBuildState) => void,
) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const toast = useToast()
  const { confirm } = useConfirmDialog()
  const docsQuery = useQuery({
    queryKey: agentKeys.knowmapDocuments(configId),
    queryFn: () => agentsApi.listKnowmapDocuments(configId),
  })
  const agentsQuery = useQuery({
    queryKey: agentKeys.agents(projectId),
    queryFn: () => agentsApi.list(projectId),
  })
  const docs = computed<KnowmapDocument[]>(() => docsQuery.data.value ?? [])
  const boundAgents = computed<Agent[]>(() =>
    (agentsQuery.data.value ?? []).filter((agent) => agent.knowmap_config_id === configId),
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
  const editDoc = ref<KnowmapDocument | null>(null)
  const editAgentIds = ref<string[]>([])
  const uploading = ref(false)

  function toggleUploadAgent(id: string, on: boolean): void {
    uploadAgentIds.value = on
      ? [...new Set([...uploadAgentIds.value, id])]
      : uploadAgentIds.value.filter((candidate) => candidate !== id)
  }
  function openAgentsEditor(doc: KnowmapDocument): void {
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
      await agentsApi.setKnowmapDocumentAgents(editDoc.value.id, [...editAgentIds.value])
    },
    onSuccess: () => {
      editDoc.value = null
      toast.success(t('agents.knowmap.agentsSaved'))
      qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
    },
    onError: () => toast.error(t('agents.knowmap.agentsSaveFailed')),
  })

  function reconcileCorpus(): void {
    watchBuild(configId, effectiveState.value)
    qc.invalidateQueries({ queryKey: agentKeys.knowmapConfig(configId) })
  }

  async function onFiles(files: File[]): Promise<void> {
    uploading.value = true
    let accepted = 0
    const agentIds = [...uploadAgentIds.value]
    try {
      for (const file of files) {
        if (file.size <= RAG_MULTIPART_MAX) {
          await agentsApi.uploadKnowmapDocumentMultipart(configId, file, agentIds)
        } else {
          await tusUpload({
            file,
            purpose: 'knowmap_source',
            projectId,
            knowmapConfigId: configId,
            knowmapAgentIds: agentIds,
          })
        }
        accepted += 1
      }
      toast.success(t('agents.knowmap.uploadStarted'))
    } catch (error) {
      if (isProblemWithType(error, '/document-unprocessable')) {
        toast.error(t('agents.knowmap.uploadUnprocessable'))
      } else if (isProblemWithType(error, '/document-allowlist-conflict')) {
        toast.error(t('agents.knowmap.uploadAllowlistConflict'))
      } else {
        toast.error(t('agents.knowmap.uploadFailed'))
      }
    } finally {
      if (accepted > 0) {
        await qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
        reconcileCorpus()
      }
      uploading.value = false
    }
  }

  const deleteDocMutation = useMutation({
    mutationFn: (id: string) => agentsApi.deleteKnowmapDocument(id),
    onSuccess: () => {
      toast.success(t('agents.knowmap.deleted'))
      qc.invalidateQueries({ queryKey: agentKeys.knowmapDocuments(configId) })
      reconcileCorpus()
    },
    onError: () => toast.error(t('agents.knowmap.deleteFailed')),
  })
  async function confirmDeleteDoc(doc: KnowmapDocument): Promise<void> {
    const ok = await confirm({
      title: t('agents.knowmap.deleteTitle'),
      message: t('agents.knowmap.deleteConfirm', { name: doc.filename }),
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
