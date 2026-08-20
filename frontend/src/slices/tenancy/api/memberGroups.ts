// Member Group API (§13.2a) — named subsets of one project's members, used to
// scope chat-room visibility below project level.
//
// Group membership is not a role: it grants nothing outside the room ACL, and
// the backend refuses to put anyone in a group who is not already a member of
// the parent project.

import { MemberGroupsService } from '@shared/api-client'
import type { PaginationParams } from '@shared/transport'

export interface MemberGroup {
  id: string
  project_id: string
  name: string
  version: number
  created_at: string
}

export interface MemberGroupMember {
  user_id: string
  joined_at: string
}

export const memberGroupsApi = {
  // A project owner receives every group; anyone else receives only the groups
  // they belong to, so an empty list is a legitimate answer, not an error.
  list: (projectId: string, params?: PaginationParams): Promise<MemberGroup[]> =>
    MemberGroupsService.listMemberGroupsApiProjectsProjectIdMemberGroupsGet({
      projectId,
      ...(params ?? {}),
    }),
  create: (projectId: string, name: string): Promise<MemberGroup> =>
    MemberGroupsService.createMemberGroupApiProjectsProjectIdMemberGroupsPost({
      projectId,
      requestBody: { name },
    }),
  rename: (groupId: string, name: string, version: number): Promise<MemberGroup> =>
    MemberGroupsService.renameMemberGroupApiMemberGroupsGroupIdPatch({
      groupId,
      ifMatch: String(version),
      requestBody: { name },
    }),
  remove: (groupId: string): Promise<void> =>
    MemberGroupsService.deleteMemberGroupApiMemberGroupsGroupIdDelete({ groupId }),
  listMembers: (groupId: string): Promise<MemberGroupMember[]> =>
    MemberGroupsService.listMemberGroupMembersApiMemberGroupsGroupIdMembersGet({ groupId }),
  addMember: (groupId: string, userId: string): Promise<void> =>
    MemberGroupsService.addMemberGroupMemberApiMemberGroupsGroupIdMembersPost({
      groupId,
      requestBody: { user_id: userId },
    }),
  removeMember: (groupId: string, userId: string): Promise<void> =>
    MemberGroupsService.removeMemberGroupMemberApiMemberGroupsGroupIdMembersUserIdDelete({
      groupId,
      userId,
    }),
}
