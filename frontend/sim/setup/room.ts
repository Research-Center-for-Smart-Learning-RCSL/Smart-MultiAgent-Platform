import type { Target } from '../config/target.ts'
import type { ApiClient } from '../lib/http.ts'
import type { Logger, Redactor } from '../lib/log.ts'
import type { Scenario } from '../scenario/schema.ts'

export interface ActivityTypeRef {
  id: string
  key: string
  name: string
}

export interface RoomSetup {
  roomId: string
  created: boolean
  guestPath: string | null
  /** Bound agent id by scenario name. */
  agentIds: Map<string, string>
  activityTypes: ActivityTypeRef[]
}

interface ChatroomOut {
  id: string
  allow_guest_links: boolean
  allow_member_groups: boolean
  version: number
}

interface AgentRef {
  agent_id: string
  name?: string | null
  role?: 'normal' | 'observer' | null
  may_control_activities?: boolean | null
  activity_type_allowlist?: string[] | null
}

export async function listActivityTypes(facilitator: ApiClient, target: Target): Promise<ActivityTypeRef[]> {
  return facilitator.get<ActivityTypeRef[]>(`/api/projects/${target.projectId}/activity-types`)
}

export function resolveType(types: ActivityTypeRef[], keyOrName: string): ActivityTypeRef {
  const hit = types.find((t) => t.key === keyOrName) ?? types.find((t) => t.name === keyOrName)
  if (!hit) throw new Error(`no activity type with key or name "${keyOrName}" is available to the project`)
  return hit
}

async function projectAgents(facilitator: ApiClient, target: Target): Promise<Array<{ id: string; name: string }>> {
  return facilitator.get(`/api/projects/${target.projectId}/agents/names?limit=500`)
}

function agentByName(all: Array<{ id: string; name: string }>, name: string): string {
  const exact = all.filter((a) => a.name === name)
  if (exact.length === 1) return (exact[0] as { id: string }).id
  if (exact.length > 1) throw new Error(`more than one agent is named "${name}" in the project`)
  throw new Error(`no agent named "${name}" in the project`)
}

/**
 * Creates the scenario's room as the facilitator, or verifies an existing one, before
 * any participant connects (spec §6.3, AC-5).
 */
export async function setupRoom(
  facilitator: ApiClient,
  target: Target,
  scenario: Scenario,
  opts: { roomOverride?: string; groupId: string | null; runTag: string; log: Logger; redactor: Redactor },
): Promise<RoomSetup> {
  const room = scenario.room
  const types = await listActivityTypes(facilitator, target)
  const all = await projectAgents(facilitator, target)
  const agentIds = new Map<string, string>()
  for (const a of room.agents) agentIds.set(a.name, agentByName(all, a.name))

  let roomId: string
  let created = false
  if (room.mode === 'create' && !opts.roomOverride) {
    const useGroups = room.access === 'memberGroups'
    const out = await facilitator.post<ChatroomOut>(`/api/workspaces/${target.workspaceId}/chatrooms`, {
      name: room.name.replace('{ts}', opts.runTag),
      allow_project_members: !useGroups,
      allow_member_groups: useGroups,
      allow_guest_links: room.guestLinks,
    })
    roomId = out.id
    created = true
    if (useGroups) {
      if (!opts.groupId) throw new Error('room access is memberGroups but the scenario declares no memberGroup')
      await facilitator.put(`/api/chatrooms/${roomId}/member-groups`, { member_group_ids: [opts.groupId] })
    }
    for (const a of room.agents) {
      const agentId = agentIds.get(a.name) as string
      await facilitator.post(`/api/chatrooms/${roomId}/agents`, { agent_id: agentId, role: a.role })
      if (a.grantActivityTypes.length > 0) {
        await facilitator.patch(`/api/chatrooms/${roomId}/agents/${agentId}/activity-control`, {
          granted: true,
          activity_type_ids: a.grantActivityTypes.map((k) => resolveType(types, k).id),
        })
      }
    }
    // Before any participant connects, so the first student arrival is the class's first arrival.
    await configureClassFlow(facilitator, roomId, scenario, types)
    opts.log.event('room.created', { roomId, agents: room.agents.map((a) => a.name) })
  } else {
    const id = opts.roomOverride ?? (room.mode === 'existing' ? room.id : undefined)
    if (!id) throw new Error('an existing room needs an id in the scenario or --room')
    roomId = id
    await verifyRoom(facilitator, roomId, scenario, agentIds, types, opts.groupId)
    opts.log.event('room.verified', { roomId })
  }

  let guestPath: string | null = null
  if (room.guestLinks) {
    const link = await facilitator.get<{ chatroom_id: string; guest_token: string }>(`/api/chatrooms/${roomId}/guest-link`)
    // Anyone holding the link joins the room as a guest; it must not reach a log or report.
    opts.redactor.add(link.guest_token)
    guestPath = `/g/${link.chatroom_id}/${link.guest_token}`
  }
  return { roomId, created, guestPath, agentIds, activityTypes: types }
}

interface ClassStartOut {
  configured: boolean
  enabled: boolean
  min_participants: number | null
  max_wait_minutes: number | null
  started_at: string | null
}

interface TimeLimitsBody {
  limits: Array<{ activity_type_id: string; time_limit_minutes: number | null }>
}

function timeLimitsBody(scenario: Scenario, types: ActivityTypeRef[]): TimeLimitsBody {
  return {
    limits: Object.entries(scenario.room.timeLimits).map(([key, minutes]) => ({
      activity_type_id: resolveType(types, key).id,
      time_limit_minutes: minutes,
    })),
  }
}

async function configureClassFlow(facilitator: ApiClient, roomId: string, scenario: Scenario, types: ActivityTypeRef[]): Promise<void> {
  const cs = scenario.room.classStart
  if (cs) {
    await facilitator.patch(`/api/chatrooms/${roomId}/class-start`, {
      enabled: true,
      min_participants: cs.minParticipants,
      max_wait_minutes: cs.maxWaitMinutes,
    })
  }
  if (Object.keys(scenario.room.timeLimits).length > 0) {
    await facilitator.put(`/api/chatrooms/${roomId}/activity-time-limits`, timeLimitsBody(scenario, types))
  }
}

async function classFlowProblems(facilitator: ApiClient, roomId: string, scenario: Scenario, types: ActivityTypeRef[]): Promise<string[]> {
  const problems: string[] = []
  const cs = scenario.room.classStart
  if (cs) {
    const got = await facilitator.get<ClassStartOut>(`/api/chatrooms/${roomId}/class-start`)
    if (!got.enabled || got.min_participants !== cs.minParticipants || got.max_wait_minutes !== cs.maxWaitMinutes) {
      problems.push(`class start is not on with ${cs.minParticipants} students and ${cs.maxWaitMinutes} minutes`)
    }
    // An already started class never fires again until the creator resets it.
    if (got.started_at) problems.push('the class has already started; reset it in the room settings')
  }
  const want = timeLimitsBody(scenario, types).limits
  if (want.length > 0) {
    const got = await facilitator.get<TimeLimitsBody>(`/api/chatrooms/${roomId}/activity-time-limits`)
    for (const w of want) {
      const hit = got.limits.find((l) => l.activity_type_id === w.activity_type_id)
      if (!hit || hit.time_limit_minutes !== w.time_limit_minutes) problems.push(`the time limit for ${w.activity_type_id} is not ${w.time_limit_minutes ?? 'none'}`)
    }
  }
  return problems
}

/** Collects every way an existing room falls short of the scenario, then fails once with all of them. */
export async function verifyRoom(
  facilitator: ApiClient,
  roomId: string,
  scenario: Scenario,
  agentIds: Map<string, string>,
  types: ActivityTypeRef[],
  groupId: string | null,
): Promise<void> {
  const problems: string[] = []
  const room = await facilitator.get<ChatroomOut>(`/api/chatrooms/${roomId}`)
  if (scenario.room.guestLinks && !room.allow_guest_links) problems.push('guest links are off')
  const bound = await facilitator.get<AgentRef[]>(`/api/chatrooms/${roomId}/agents?limit=500`)
  for (const want of scenario.room.agents) {
    const id = agentIds.get(want.name)
    const got = bound.find((b) => b.agent_id === id)
    if (!got) {
      problems.push(`agent ${want.name} is not bound`)
      continue
    }
    if (got.role && got.role !== want.role) problems.push(`agent ${want.name} is bound as ${got.role}, not ${want.role}`)
    for (const key of want.grantActivityTypes) {
      const typeId = resolveType(types, key).id
      if (!got.may_control_activities || !(got.activity_type_allowlist ?? []).includes(typeId)) {
        problems.push(`agent ${want.name} lacks the activity grant for ${key}`)
      }
    }
  }
  if (groupId && room.allow_member_groups) {
    const bg = await facilitator.get<{ member_group_ids: string[] }>(`/api/chatrooms/${roomId}/member-groups`)
    if (!bg.member_group_ids.includes(groupId)) problems.push('the scenario member group is not bound to the room')
  }
  problems.push(...(await classFlowProblems(facilitator, roomId, scenario, types)))
  if (problems.length > 0) throw new Error(`room ${roomId} does not match the scenario: ${problems.join('; ')}`)
}
