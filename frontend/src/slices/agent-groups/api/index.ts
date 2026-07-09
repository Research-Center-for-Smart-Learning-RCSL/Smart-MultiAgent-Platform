import { http } from '@shared/transport'
import type { AgentGroupCreateInput, AgentGroupUpdateInput } from '../types/schemas'

// Mirrors backend `AgentGroupOut` (backend/app/api/v1/agent_groups.py). A group is
// a first-class, project-scoped owner of a Concept Map; `concept_map_enabled` is
// the wide-layer privacy opt-in (R11.10), toggled via its own endpoint.
export interface AgentGroup {
  id: string
  project_id: string
  name: string
  concept_map_enabled: boolean
  created_at: string
}

// Mirrors `AgentGroupMembersOut` — a flat list of member agent ids.
export interface AgentGroupMembers {
  members: string[]
}

export interface ConceptMapStatus {
  group_id: string
  concept_map_enabled: boolean
}

export const agentGroupsApi = {
  list: (projectId: string) => http.get<AgentGroup[]>(`/projects/${projectId}/agent-groups`),

  create: (projectId: string, payload: AgentGroupCreateInput) =>
    http.post<AgentGroup>(`/projects/${projectId}/agent-groups`, payload),

  get: (groupId: string) => http.get<AgentGroup>(`/agent-groups/${groupId}`),

  rename: (groupId: string, payload: AgentGroupUpdateInput) =>
    http.patch<AgentGroup>(`/agent-groups/${groupId}`, payload),

  remove: (groupId: string) => http.delete(`/agent-groups/${groupId}`),

  listMembers: (groupId: string) => http.get<AgentGroupMembers>(`/agent-groups/${groupId}/members`),

  addMember: (groupId: string, agentId: string) =>
    http.post<AgentGroupMembers>(`/agent-groups/${groupId}/members`, { agent_id: agentId }),

  removeMember: (groupId: string, agentId: string) =>
    http.delete(`/agent-groups/${groupId}/members/${agentId}`),

  // Project-Owner-gated privacy opt-in for the group's Concept Map (R11.10).
  setConceptMapEnabled: (groupId: string, enabled: boolean) =>
    http.put<ConceptMapStatus>(`/agent-groups/${groupId}/concept-map-enabled`, { enabled }),
}
