// Invite inbox API (tenancy).
//
// Wraps the generated InvitesService over the one instrumented axios singleton.
// The `invites`-scoped InviteOut is the only invite model carrying created_at +
// scope_name, so it is the one that backs the hand-rolled `Invite` type here (the
// org/project invite-create endpoints return different, narrower shapes — those
// live in orgs.ts / projects.ts and are await-only).

import { InvitesService } from '@shared/api-client'
import type { PaginationParams } from '@shared/transport'

export interface Invite {
  id: string
  scope_type: 'org' | 'project'
  scope_id: string
  scope_name: string
  invitee_email: string
  role: 'owner' | 'member'
  state: 'pending' | 'accepted' | 'rejected' | 'revoked' | 'expired'
  created_at: string
  expires_at: string
}

export const invitesApi = {
  list: (
    state: 'pending' | 'accepted' | 'rejected' = 'pending',
    params?: PaginationParams,
  ): Promise<Invite[]> => InvitesService.listInboxApiInvitesGet({ state, ...(params ?? {}) }),
  accept: (id: string) => InvitesService.acceptApiInvitesInviteIdAcceptPost({ inviteId: id }),
  acceptByToken: (token: string): Promise<Invite> =>
    InvitesService.acceptByTokenApiInvitesAcceptByTokenPost({ requestBody: { token } }),
  reject: (id: string) => InvitesService.rejectApiInvitesInviteIdRejectPost({ inviteId: id }),
}
