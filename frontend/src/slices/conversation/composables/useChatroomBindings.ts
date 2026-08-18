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
  setChatroomAgentActivityControl,
  setChatroomAgentRole,
} from '../api'
import { patchAgentWakeupConfig } from '@slices/workflow'
import { listActivityTypes, type ActivityType } from '@slices/activities'
import { normalizeWakeupConfig, type WakeupConfig } from '@shared/types/workflow'
import type { Agent } from '@slices/agents'
import type { Chatroom, ChatroomAgentRole } from '../types'

export interface BoundAgent {
  id: string
  name?: string
  wakeup_config: WakeupConfig
  // Present only when the caller is the room creator (R28.10).
  role?: ChatroomAgentRole
  // Delegated activity control ([R30.37]), creator-only on the same terms as
  // `role`. `undefined` means "you are not told", which is a different state from
  // `false` ("told, and this agent holds nothing").
  may_control_activities?: boolean
  activity_type_allowlist?: string[]
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
  // agent_id → role, populated only for the creator (server omits it otherwise).
  const boundRoles = ref<Record<string, ChatroomAgentRole | undefined>>({})
  // agent_id → delegated activity grant, creator-only for the same reason.
  const boundGrants = ref<Record<string, { granted: boolean; typeIds: string[] } | undefined>>({})
  // The project's usable activity types, for the grant multi-select. Loaded once
  // with the bindings.
  const activityTypes = ref<ActivityType[]>([])
  // Tracked separately from `activityTypes.length` because the two states need
  // different copy: "this project has no activity types yet, register one" is a
  // instruction the teacher can act on, and telling them that when the listing
  // merely failed sends them to create something that already exists.
  const activityTypesFailed = ref(false)
  const selectedAgentId = ref('')
  const selectedRole = ref<ChatroomAgentRole>('normal')
  const bindingBusy = ref(false)
  const bindingError = ref<string | null>(null)

  const boundAgents = computed<BoundAgent[]>(() =>
    boundAgentIds.value
      .map((id) => projectAgents.value.find((a) => a.id === id))
      .filter((a): a is Agent => a != null)
      .map((a) => {
        const role = boundRoles.value[a.id]
        const grant = boundGrants.value[a.id]
        return {
          id: a.id,
          name: a.name,
          wakeup_config: normalizeWakeupConfig(a.wakeup_config),
          ...(role !== undefined && { role }),
          ...(grant !== undefined && {
            may_control_activities: grant.granted,
            activity_type_allowlist: grant.typeIds,
          }),
        }
      }),
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
      const [agents, bound] = await Promise.all([
        listProjectAgents(ws.project_id),
        listChatroomAgents(chatroomId),
      ])
      projectAgents.value = agents
      boundAgentIds.value = bound.map((b) => b.agent_id)
      boundRoles.value = Object.fromEntries(bound.map((b) => [b.agent_id, b.role]))
      boundGrants.value = Object.fromEntries(
        bound.map((b) => [
          b.agent_id,
          b.may_control_activities === undefined
            ? undefined
            : { granted: b.may_control_activities, typeIds: b.activity_type_allowlist ?? [] },
        ]),
      )
      // Separate from the two loads above: a project with no activity types is
      // ordinary, and so is a viewer who cannot list them, so neither may fail
      // the whole bindings panel.
      await loadActivityTypes(ws.project_id)
    } catch {
      bindingError.value = 'conversation.settings.bindingsLoadFailed'
    }
  }

  async function loadActivityTypes(projectId: string): Promise<void> {
    try {
      activityTypes.value = await listActivityTypes(projectId)
      activityTypesFailed.value = false
    } catch {
      // Swallowed rather than raised: a room creator who is not a project member
      // legitimately cannot list the project's types (the endpoint is
      // membership-gated), and neither that nor a transient failure may take down
      // the whole bindings panel.
      activityTypes.value = []
      activityTypesFailed.value = true
    }
  }

  async function onAddAgent(): Promise<void> {
    if (!selectedAgentId.value || bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await addChatroomAgent(
        chatroomId,
        selectedAgentId.value,
        selectedRole.value === 'observer' ? 'observer' : undefined,
      )
      selectedAgentId.value = ''
      selectedRole.value = 'normal'
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.bindFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  async function onSetRole(agentId: string, role: ChatroomAgentRole): Promise<void> {
    if (bindingBusy.value || boundRoles.value[agentId] === role) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await setChatroomAgentRole(chatroomId, agentId, role)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.roleChangeFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  /** Grant or revoke delegated activity control for one bound agent ([R30.37]).
   *
   *  Follows `onSetRole`'s busy-guard shape and reloads rather than patching the
   *  local map, so the panel always shows what the server stored — which matters
   *  here because the server keeps the allowlist across a revoke and the client
   *  must not invent its own version of that.
   *
   *  Granting with an empty selection is refused client-side too: the server
   *  returns 422 and the DB CHECK refuses the same state, so letting the request
   *  go out would trade a clear message for an opaque one. */
  async function onSetActivityControl(
    agentId: string,
    granted: boolean,
    typeIds: string[],
  ): Promise<void> {
    if (bindingBusy.value) return
    if (granted && typeIds.length === 0) {
      bindingError.value = 'conversation.settings.activityControlNeedsTypes'
      return
    }
    bindingBusy.value = true
    bindingError.value = null
    try {
      await setChatroomAgentActivityControl(chatroomId, agentId, granted, typeIds)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.activityControlFailed'
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

  // Per-agent save coordination: the editor emits on every field commit, so a
  // second edit can arrive while the first save is in flight. Stash the latest
  // config and flush it after the running save returns, so no edit is dropped
  // and consecutive saves don't reuse a stale version (409). We deliberately do
  // not mutate the agent's local wakeup_config — that would re-render the editor
  // and revert in-progress edits; only the version needs to advance.
  const wakeupInFlight = new Set<string>()
  const wakeupPending = new Map<string, WakeupConfig>()

  async function saveWakeupConfig(agentId: string, config: WakeupConfig): Promise<void> {
    if (wakeupInFlight.has(agentId)) {
      wakeupPending.set(agentId, config)
      return
    }
    const agent = projectAgents.value.find((a) => a.id === agentId)
    if (!agent) {
      toast.error(t('conversation.settings.wakeupConfigFailed'))
      return
    }
    wakeupInFlight.add(agentId)
    try {
      // The agent PATCH needs an If-Match precondition; pass the current
      // version and adopt the bumped one so the next save doesn't conflict.
      agent.version = await patchAgentWakeupConfig(agentId, config, agent.version)
      toast.success(t('conversation.settings.wakeupConfigSaved'))
    } catch {
      toast.error(t('conversation.settings.wakeupConfigFailed'))
    } finally {
      wakeupInFlight.delete(agentId)
    }
    const next = wakeupPending.get(agentId)
    if (next) {
      wakeupPending.delete(agentId)
      await saveWakeupConfig(agentId, next)
    }
  }

  return {
    projectAgents,
    boundAgentIds,
    activityTypes,
    activityTypesFailed,
    selectedAgentId,
    selectedRole,
    bindingBusy,
    bindingError,
    boundAgents,
    availableAgents,
    orphanAgentIds,
    loadBindings,
    onAddAgent,
    onRemoveAgent,
    onSetActivityControl,
    onSetRole,
    saveWakeupConfig,
  }
}
