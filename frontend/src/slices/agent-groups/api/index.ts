import { AgentGroupsService } from '@shared/api-client'
import type {
  AgentGroupOut,
  AgentGroupMembersOut,
  app__api__v1__agent_groups__ConceptMapStatusOut,
} from '@shared/api-client'
import type { AgentGroupCreateInput, AgentGroupUpdateInput } from '../types/schemas'

// Slice-local aliases over the generated models (R24.13) so call sites don't
// need to know these come from the generated client. A group is a first-class,
// project-scoped owner of a Concept Map; `concept_map_enabled` is the wide-layer
// privacy opt-in (R11.10), toggled via its own endpoint.
export type AgentGroup = AgentGroupOut
export type AgentGroupMembers = AgentGroupMembersOut
export type ConceptMapStatus = app__api__v1__agent_groups__ConceptMapStatusOut

// Thin use-case-shaped wrappers over the generated AgentGroupsService (R24.13).
// The request/response shapes and the auth/session behavior (bearer token,
// silent 401 refresh, problem+json error typing) come from
// shared/transport/axios.ts's instrumentation of the bare axios singleton the
// generated client calls into. Methods resolve to the response body directly.
export const agentGroupsApi = {
  list: (projectId: string): Promise<AgentGroup[]> =>
    AgentGroupsService.listGroupsApiProjectsProjectIdAgentGroupsGet({ projectId }),

  create: (projectId: string, payload: AgentGroupCreateInput): Promise<AgentGroup> =>
    AgentGroupsService.createGroupApiProjectsProjectIdAgentGroupsPost({ projectId, requestBody: payload }),

  get: (groupId: string): Promise<AgentGroup> =>
    AgentGroupsService.getGroupApiAgentGroupsGroupIdGet({ groupId }),

  rename: (groupId: string, payload: AgentGroupUpdateInput): Promise<AgentGroup> =>
    AgentGroupsService.renameGroupApiAgentGroupsGroupIdPatch({ groupId, requestBody: payload }),

  remove: (groupId: string): Promise<void> =>
    AgentGroupsService.deleteGroupApiAgentGroupsGroupIdDelete({ groupId }),

  listMembers: (groupId: string): Promise<AgentGroupMembers> =>
    AgentGroupsService.listMembersApiAgentGroupsGroupIdMembersGet({ groupId }),

  addMember: (groupId: string, agentId: string): Promise<AgentGroupMembers> =>
    AgentGroupsService.addMemberApiAgentGroupsGroupIdMembersPost({
      groupId,
      requestBody: { agent_id: agentId },
    }),

  removeMember: (groupId: string, agentId: string): Promise<void> =>
    AgentGroupsService.removeMemberApiAgentGroupsGroupIdMembersAgentIdDelete({ groupId, agentId }),

  // Project-Owner-gated privacy opt-in for the group's Concept Map (R11.10).
  setConceptMapEnabled: (groupId: string, enabled: boolean): Promise<ConceptMapStatus> =>
    AgentGroupsService.setConceptMapEnabledApiAgentGroupsGroupIdConceptMapEnabledPut({
      groupId,
      requestBody: { enabled },
    }),
}
