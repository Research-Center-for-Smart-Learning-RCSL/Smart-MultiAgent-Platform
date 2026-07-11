// Org + membership + original-creator-transfer API (tenancy).
//
// Wraps the generated OrgsService over the one instrumented axios singleton.
// Every generated *Out is directly assignable to the hand-rolled slice types
// below (enums match; the few optional fields the *Out omit stay assignable),
// so the methods resolve the bare body typed as the slice type with no bridge.
// `restore` resolves void — the backend returns an empty body (the old
// `http.post<Org>` typing was optimistic; consumers only await it).

import { OrgsService } from '@shared/api-client'
import type { PaginationParams } from '@shared/transport'

export interface Org {
  id: string
  name: string
  creator_user_id: string
  default_project_id?: string | null
  version: number
  created_at: string
  deleted_at?: string | null
}

export interface OrgMember {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_original_creator: boolean
  joined_at: string
}

export interface OrgQuotas {
  users: number
  projects: number
  chatrooms: number
  agents: number
  workflows: number
  computed_at: string | null
  advisory_targets: Record<string, number>
}

export interface OriginalCreatorTransfer {
  id: string
  org_id: string
  initiator_user_id: string
  target_user_id: string
  state: 'pending' | 'accepted' | 'rejected' | 'cancelled' | 'expired' | 'admin_forced'
  created_at: string
  expires_at: string
}

export const orgsApi = {
  list: (params?: PaginationParams): Promise<Org[]> =>
    OrgsService.listOrgsApiOrgsGet(params ?? {}),
  create: (name: string): Promise<Org> =>
    OrgsService.createOrgApiOrgsPost({ requestBody: { name } }),
  get: (id: string): Promise<Org> => OrgsService.readOrgApiOrgsOrgIdGet({ orgId: id }),
  rename: (id: string, name: string, version: number): Promise<Org> =>
    OrgsService.renameOrgApiOrgsOrgIdPatch({
      orgId: id,
      ifMatch: String(version),
      requestBody: { name },
    }),
  remove: (id: string): Promise<void> => OrgsService.deleteOrgApiOrgsOrgIdDelete({ orgId: id }),
  restore: (id: string): Promise<void> =>
    OrgsService.restoreOrgApiOrgsOrgIdRestorePost({ orgId: id }),
  quotas: (id: string): Promise<OrgQuotas> =>
    OrgsService.getOrgQuotasApiOrgsOrgIdQuotasGet({ orgId: id }),

  listMembers: (id: string, params?: PaginationParams): Promise<OrgMember[]> =>
    OrgsService.listMembersApiOrgsOrgIdMembersGet({ orgId: id, ...(params ?? {}) }),
  removeMember: (id: string, uid: string) =>
    OrgsService.removeMemberApiOrgsOrgIdMembersUserIdDelete({ orgId: id, userId: uid }),
  setRole: (id: string, uid: string, role: 'owner' | 'member') =>
    OrgsService.patchMemberApiOrgsOrgIdMembersUserIdPatch({
      orgId: id,
      userId: uid,
      requestBody: { role },
    }),

  invite: (id: string, email: string, role: 'owner' | 'member') =>
    OrgsService.createInviteApiOrgsOrgIdInvitesPost({
      orgId: id,
      requestBody: { email, role },
    }),

  initiateTransfer: (id: string, target_user_id: string): Promise<OriginalCreatorTransfer> =>
    OrgsService.transferInitiateApiOrgsOrgIdOriginalCreatorTransfersPost({
      orgId: id,
      requestBody: { target_user_id },
    }),
  listTransfers: (id: string, params?: PaginationParams): Promise<OriginalCreatorTransfer[]> =>
    OrgsService.transferListApiOrgsOrgIdOriginalCreatorTransfersGet({
      orgId: id,
      ...(params ?? {}),
    }),
  acceptTransfer: (id: string, tid: string) =>
    OrgsService.transferAcceptApiOrgsOrgIdOriginalCreatorTransfersTransferIdAcceptPost({
      orgId: id,
      transferId: tid,
    }),
  cancelTransfer: (id: string, tid: string) =>
    OrgsService.transferCancelApiOrgsOrgIdOriginalCreatorTransfersTransferIdDelete({
      orgId: id,
      transferId: tid,
    }),
  rejectTransfer: (id: string, tid: string) =>
    OrgsService.transferRejectApiOrgsOrgIdOriginalCreatorTransfersTransferIdRejectPost({
      orgId: id,
      transferId: tid,
    }),
}
