// Composable: chatroom agent binding management (list, add, remove).
// Extracted from ChatroomSettingsView.vue (H16 SoC fix).

import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import {
  addChatroomAgent,
  getWorkspace,
  listChatroomAgents,
  listProjectAgentNames,
  listProjectAgents,
  removeChatroomAgent,
  setChatroomAgentActivityControl,
  setChatroomAgentCanvasAccess,
  setChatroomAgentCanvasWriteAccess,
  setChatroomAgentDraftAccess,
  setChatroomAgentRole,
} from '../api'
import { patchAgentWakeupConfig } from '@slices/workflow'
import { activityKeys, listActivityTypes, type ActivityType } from '@slices/activities'
import { normalizeWakeupConfig, type WakeupConfig } from '@shared/types/workflow'
import type { Agent } from '@slices/agents'
import type { Chatroom, ChatroomAgentRole } from '../types'

export interface BoundAgent {
  id: string
  name?: string
  // Present only for a caller who may edit agents (matrix row 28): a non-editor
  // resolves bound agents through the names projection, which carries no config.
  wakeup_config?: WakeupConfig
  // Present only when the caller is the room creator (R28.10).
  role?: ChatroomAgentRole
  // Delegated activity control ([R30.37]), creator-only on the same terms as
  // `role`. `undefined` means "you are not told", which is a different state from
  // `false` ("told, and this agent holds nothing").
  may_control_activities?: boolean
  activity_type_allowlist?: string[]
  // Live draft reading ([R32.03]), creator-only on the same terms and with the
  // same three-state meaning: `undefined` is "you are not told".
  may_read_drafts?: boolean
  may_read_canvas?: boolean
  may_write_canvas?: boolean
}

interface AgentName {
  id: string
  name: string
}

interface KnownAgent extends AgentName {
  deleted: boolean
  wakeup_config?: WakeupConfig
}

export function useChatroomBindings(
  chatroomId: string,
  /** Reactive getter for the current room (may be null before load). */
  getRoom: () => Chatroom | null,
  {
    projectId,
    needsActivityTypes,
  }: {
    /** The room's project, once resolved. */
    projectId: () => string | undefined
    /** Whether to list the project's activity types. They are a project read
     *  ([R5.07]) and only the room creator's controls use them. */
    needsActivityTypes: () => boolean
  },
) {
  const { t } = useI18n()
  const toast = useToast()

  const projectAgents = ref<Agent[]>([])
  // The names projection, loaded instead of `projectAgents` for a caller who may
  // not read agent configuration (matrix row 28). Exactly one of the two is
  // populated per load.
  const agentNames = ref<AgentName[]>([])
  // `is_moderator` is the server's verdict of PROJECT_OWNER or ORG_OWNER (admin
  // included), the same role set that holds RESOURCE_CREATE_EDIT.
  const canEditAgents = computed(() => getRoom()?.is_moderator === true)
  const boundAgentIds = ref<string[]>([])
  // agent_id → role, populated only for the creator (server omits it otherwise).
  const boundRoles = ref<Record<string, ChatroomAgentRole | undefined>>({})
  // agent_id → delegated activity grant, creator-only for the same reason.
  const boundGrants = ref<Record<string, { granted: boolean; typeIds: string[] } | undefined>>({})
  // agent_id -> live draft grant ([R32.03]), creator-only for the same reason.
  // Its own map rather than a third field on `boundGrants`: the two grants are
  // written by different routes and a binding may hold either without the other,
  // so one record would invite reading a missing half as "false" rather than as
  // "not told".
  const boundDraftGrants = ref<Record<string, boolean | undefined>>({})
  const boundCanvasGrants = ref<Record<string, boolean | undefined>>({})
  const boundCanvasWriteGrants = ref<Record<string, boolean | undefined>>({})
  // The project's usable activity types, for the grant multi-select. A query
  // rather than part of `loadBindings`: the creator bit can settle after the
  // bindings did (the session's `me` may arrive after the room), and the query
  // follows it without a hand-rolled trigger. Its failure is kept off the
  // bindings panel: a project with no types is ordinary, and so is a creator
  // who holds no project role and so cannot list them.
  const typesQuery = useQuery({
    queryKey: computed(() => activityKeys.types(projectId() ?? '')),
    queryFn: () => listActivityTypes(projectId()!),
    enabled: computed(() => !!projectId() && needsActivityTypes()),
    retry: false,
  })
  const activityTypes = computed<ActivityType[]>(() => typesQuery.data.value ?? [])
  // Tracked separately from `activityTypes.length` because the two states need
  // different copy: "this project has no activity types yet, register one" is a
  // instruction the teacher can act on, and telling them that when the listing
  // merely failed sends them to create something that already exists.
  const activityTypesFailed = computed(() => typesQuery.isError.value)
  const selectedAgentId = ref('')
  const selectedRole = ref<ChatroomAgentRole>('normal')
  const bindingBusy = ref(false)
  const bindingError = ref<string | null>(null)

  // Every live agent of the project as this caller may see it: full records for
  // an editor, `{id, name}` otherwise.
  const knownAgents = computed<KnownAgent[]>(() =>
    projectAgents.value.length
      ? projectAgents.value.map((a) => ({
          id: a.id,
          name: a.name,
          deleted: !!a.deleted_at,
          wakeup_config: normalizeWakeupConfig(a.wakeup_config),
        }))
      : agentNames.value.map((a) => ({ id: a.id, name: a.name, deleted: false })),
  )

  const boundAgents = computed<BoundAgent[]>(() =>
    boundAgentIds.value
      .map((id) => knownAgents.value.find((a) => a.id === id))
      .filter((a): a is KnownAgent => a != null)
      .map((a) => {
        const role = boundRoles.value[a.id]
        const grant = boundGrants.value[a.id]
        const draftGrant = boundDraftGrants.value[a.id]
        const canvasGrant = boundCanvasGrants.value[a.id]
        const canvasWriteGrant = boundCanvasWriteGrants.value[a.id]
        return {
          id: a.id,
          name: a.name,
          ...(a.wakeup_config !== undefined && { wakeup_config: a.wakeup_config }),
          ...(role !== undefined && { role }),
          ...(grant !== undefined && {
            may_control_activities: grant.granted,
            activity_type_allowlist: grant.typeIds,
          }),
          ...(draftGrant !== undefined && { may_read_drafts: draftGrant }),
          ...(canvasGrant !== undefined && { may_read_canvas: canvasGrant }),
          ...(canvasWriteGrant !== undefined && { may_write_canvas: canvasWriteGrant }),
        }
      }),
  )

  const availableAgents = computed<AgentName[]>(() =>
    knownAgents.value
      .filter((a) => !a.deleted && !boundAgentIds.value.includes(a.id))
      .map((a) => ({ id: a.id, name: a.name })),
  )

  const orphanAgentIds = computed<string[]>(() =>
    boundAgentIds.value.filter(
      (id) => !knownAgents.value.some((a) => a.id === id),
    ),
  )

  /** Resolve project, then load its agents + this room's bound set. */
  async function loadBindings(): Promise<void> {
    const room = getRoom()
    if (!room) return
    bindingError.value = null
    try {
      const ws = await getWorkspace(room.workspace_id)
      const editor = room.is_moderator === true
      const [agents, names, bound] = await Promise.all([
        editor ? listProjectAgents(ws.project_id) : Promise.resolve([]),
        editor ? Promise.resolve([]) : listProjectAgentNames(ws.project_id),
        listChatroomAgents(chatroomId),
      ])
      projectAgents.value = agents
      agentNames.value = names
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
      boundDraftGrants.value = Object.fromEntries(
        bound.map((b) => [b.agent_id, b.may_read_drafts]),
      )
      boundCanvasGrants.value = Object.fromEntries(
        bound.map((b) => [b.agent_id, b.may_read_canvas]),
      )
      boundCanvasWriteGrants.value = Object.fromEntries(
        bound.map((b) => [b.agent_id, b.may_write_canvas]),
      )
    } catch {
      bindingError.value = 'conversation.settings.bindingsLoadFailed'
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

  /** Grant or revoke one binding's live-draft reading ([R32.03]).
   *
   *  No client-side precondition, unlike its activity sibling: there is nothing
   *  to select alongside it, so no state exists that the server would refuse and
   *  the client could refuse more clearly.
   *
   *  Reloads rather than patching the local map, for the same reason the other
   *  writes do: the listing is the only place that knows whether the caller is
   *  still entitled to be told the answer at all. */
  async function onSetDraftAccess(agentId: string, granted: boolean): Promise<void> {
    if (bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await setChatroomAgentDraftAccess(chatroomId, agentId, granted)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.draftAccessFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  async function onSetCanvasAccess(agentId: string, granted: boolean): Promise<void> {
    if (bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await setChatroomAgentCanvasAccess(chatroomId, agentId, granted)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.canvasAccessFailed'
    } finally {
      bindingBusy.value = false
    }
  }

  async function onSetCanvasWriteAccess(agentId: string, granted: boolean): Promise<void> {
    if (bindingBusy.value) return
    bindingBusy.value = true
    bindingError.value = null
    try {
      await setChatroomAgentCanvasWriteAccess(chatroomId, agentId, granted)
      await loadBindings()
    } catch {
      bindingError.value = 'conversation.settings.canvasWriteAccessFailed'
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
    canEditAgents,
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
    onSetDraftAccess,
    onSetCanvasAccess,
    onSetCanvasWriteAccess,
    onSetRole,
    saveWakeupConfig,
  }
}
