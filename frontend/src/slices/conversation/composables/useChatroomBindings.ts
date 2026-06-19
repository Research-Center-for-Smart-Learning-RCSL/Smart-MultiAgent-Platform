// Composable: chatroom agent binding management (list, add, remove).
// Extracted from ChatroomSettingsView.vue (H16 SoC fix).

import { computed, ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import {
  addChatroomAgent,
  getWorkspace,
  listChatroomAgents,
  listProjectAgents,
  removeChatroomAgent,
} from '../api'
import { patchAgentWakeupConfig, toEditableWakeup } from '@slices/workflow'
import type { WakeupConfig } from '@shared/types/workflow'
import type { Agent } from '@slices/agents'
import type { Chatroom } from '../types'

export interface BoundAgent {
  id: string
  name?: string
  wakeup_config?: WakeupConfig
}

export function useChatroomBindings(
  chatroomId: string,
  /** Reactive getter for the current room (may be null before load). */
  getRoom: () => Chatroom | null,
) {
  const { t } = useI18n()
  const toast = useToast()

  const projectAgents = ref<Agent[]>([])
  const boundAgentIds = ref<string[]>([])
  const selectedAgentId = ref('')
  const bindingBusy = ref(false)
  const bindingError = ref<string | null>(null)

  const boundAgents = computed<BoundAgent[]>(() =>
    boundAgentIds.value
      .map((id) => projectAgents.value.find((a) => a.id === id))
      .filter((a): a is Agent => a != null)
      .map((a) => ({
        id: a.id,
        name: a.name,
        wakeup_config: toEditableWakeup(a.wakeup_config),
      })),
  )

  const availableAgents = computed<Agent[]>(() =>
    projectAgents.value.filter(
      (a) => !a.deleted_at && !boundAgentIds.value.includes(a.id),
    ),
  )

  const orphanAgentIds = computed<string[]>(() =>
    boundAgentIds.value.filter(
      (id) => !projectAgents.value.some((a) => a.id === id),
    ),
  )

  /** Resolve project, then load its agents + this room's bound set. */
  async function loadBindings(): Promise<void> {
    const room = getRoom()
    if (!room) return
    bindingError.value = null
    try {
      const ws = await getWorkspace(room.workspace_id)
      const [agents, boundIds] = await Promise.all([
        listProjectAgents(ws.project_id),
        listChatroomAgents(chatroomId),
      ])
      projectAgents.value = agents
      boundAgentIds.value = boundIds
    } catch {
      bindingError.value = 'conversation.settings.bindingsLoadFailed'
    }
  }

  async function onAddAgent(): Promise<void> {
    if (!selectedAgentId.value || bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await addChatroomAgent(chatroomId, selectedAgentId.value)
      selectedAgentId.value = ''
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.bindFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  async function onRemoveAgent(agentId: string): Promise<void> {
    if (bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await removeChatroomAgent(chatroomId, agentId)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.unbindFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  async function saveWakeupConfig(agentId: string, config: WakeupConfig): Promise<void> {
    try {
      await patchAgentWakeupConfig(agentId, config)
      toast.success(t('conversation.settings.wakeupConfigSaved'))
    } catch {
      toast.error(t('conversation.settings.wakeupConfigFailed'))
    }
  }

  return {
    projectAgents,
    boundAgentIds,
    selectedAgentId,
    bindingBusy,
    bindingError,
    boundAgents,
    availableAgents,
    orphanAgentIds,
    loadBindings,
    onAddAgent,
    onRemoveAgent,
    saveWakeupConfig,
  }
}
