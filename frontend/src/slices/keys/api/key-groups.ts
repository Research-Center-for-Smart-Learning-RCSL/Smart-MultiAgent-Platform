import { http } from '@shared/transport'

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

export const keyGroupsApi = {
  listForProject: (projectId: string) =>
    http.get<KeyGroup[]>(`/projects/${projectId}/key-groups`),
  create: (projectId: string, name: string) =>
    http.post<KeyGroup>(`/projects/${projectId}/key-groups`, { name }),
  get: (groupId: string) => http.get<KeyGroupDetail>(`/key-groups/${groupId}`),
  rename: (groupId: string, name: string) =>
    http.patch(`/key-groups/${groupId}`, { name }),
  remove: (groupId: string) => http.delete(`/key-groups/${groupId}`),

  addMember: (groupId: string, keyId: string) =>
    http.post<KeyGroupMember>(`/key-groups/${groupId}/keys`, { key_id: keyId }),
  patchMember: (groupId: string, keyId: string, patch: MemberPatch) =>
    http.patch(`/key-groups/${groupId}/keys/${keyId}`, patch),
  removeMember: (groupId: string, keyId: string) =>
    http.delete(`/key-groups/${groupId}/keys/${keyId}`),
  reorder: (groupId: string, priorities: Record<string, number>) =>
    http.post(`/key-groups/${groupId}/reorder`, { priorities }),
}
