import { KeyGroupsService } from '@shared/api-client'
import type { GroupOut } from '@shared/api-client'

export interface KeyGroup {
  id: string
  project_id: string
  name: string
  created_at: string
  member_count: number
  // Distinct providers of the group's actively-carried keys. Empty when the
  // group has no serviceable keys (e.g. all withdrawn). Used to flag agents
  // whose `model_hint` is no longer serviceable by their bound group.
  providers: string[]
}

export interface Rotation {
  rotate_on_error_codes: number[]
  rotate_on_token_quota: boolean
  retry_on_error: boolean
  retry_initial_delay_ms: number
  retry_multiplier: number
  retry_max_delay_ms: number
  retry_max: number
  retry_jitter_pct: number
}

export interface Limits {
  max_input_tokens_per_hour: number | null
  max_output_tokens_per_hour: number | null
  max_requests_per_hour: number | null
}

export interface KeyGroupMember {
  key_id: string
  priority: number
  rotation: Rotation
  limits: Limits
}

export interface KeyGroupDetail {
  group: KeyGroup
  members: KeyGroupMember[]
}

export type MemberPatch = Partial<Rotation & Limits & { priority: number }>

// `GroupOut.member_count`/`providers` are optional in the contract (the pydantic
// model defaults them) but the slice's KeyGroup requires both; the backend always
// populates them, so these defaults satisfy the type without a real null case.
function toKeyGroup(g: GroupOut): KeyGroup {
  return {
    id: g.id,
    project_id: g.project_id,
    name: g.name,
    created_at: g.created_at,
    member_count: g.member_count ?? 0,
    providers: g.providers ?? [],
  }
}

// Thin wrappers over the generated KeyGroupsService (R24.13); each resolves the
// bare body. MemberOut is assignable to KeyGroupMember (RotationOut/LimitsOut
// match Rotation/Limits field-for-field); GroupOut is bridged via toKeyGroup.
export const keyGroupsApi = {
  listForProject: async (projectId: string): Promise<KeyGroup[]> =>
    (await KeyGroupsService.listGroupsApiProjectsProjectIdKeyGroupsGet({ projectId })).map(
      toKeyGroup,
    ),
  create: async (projectId: string, name: string): Promise<KeyGroup> =>
    toKeyGroup(
      await KeyGroupsService.createGroupApiProjectsProjectIdKeyGroupsPost({
        projectId,
        requestBody: { name },
      }),
    ),
  get: async (groupId: string): Promise<KeyGroupDetail> => {
    const detail = await KeyGroupsService.readGroupApiKeyGroupsGroupIdGet({ groupId })
    return { group: toKeyGroup(detail.group), members: detail.members }
  },
  rename: (groupId: string, name: string): Promise<void> =>
    KeyGroupsService.renameGroupApiKeyGroupsGroupIdPatch({ groupId, requestBody: { name } }),
  remove: (groupId: string): Promise<void> =>
    KeyGroupsService.deleteGroupApiKeyGroupsGroupIdDelete({ groupId }),

  addMember: (groupId: string, keyId: string): Promise<KeyGroupMember> =>
    KeyGroupsService.addMemberApiKeyGroupsGroupIdKeysPost({
      groupId,
      requestBody: { key_id: keyId },
    }),
  patchMember: (groupId: string, keyId: string, patch: MemberPatch): Promise<void> =>
    KeyGroupsService.patchMemberApiKeyGroupsGroupIdKeysKeyIdPatch({
      groupId,
      keyId,
      requestBody: patch,
    }),
  removeMember: (groupId: string, keyId: string): Promise<void> =>
    KeyGroupsService.removeMemberApiKeyGroupsGroupIdKeysKeyIdDelete({ groupId, keyId }),
  reorder: (groupId: string, priorities: Record<string, number>): Promise<void> =>
    KeyGroupsService.reorderMembersApiKeyGroupsGroupIdReorderPost({
      groupId,
      requestBody: { priorities },
    }),
}
