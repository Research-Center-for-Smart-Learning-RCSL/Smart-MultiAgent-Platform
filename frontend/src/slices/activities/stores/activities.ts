// Ephemeral per-room activity outcomes, keyed by submission id (R30.17). The
// chatroom WS switch (in slices/conversation, which depends on activities per
// Q-3) drives `applyCreated` / `applyValidated`; the host seeds richer data from
// the submit HTTP response via `upsertFromSubmission`. Keyed by submission id so
// a pending -> validated transition updates one entry with no list refetch.

import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { registerCleanup } from '@shared/stores/useAppCleanup'
import { normalizeValidationStatus } from '../types'
import type {
  ActivationView,
  ActivityActivation,
  ActivityGroupProposal,
  ActivityMemberGroupRef,
  ActivityOutcome,
  ActivitySubmission,
} from '../types'

/** One room's group-proposal state for the current round.
 *
 *  `proposals` holds ONLY what the authorization-narrowed HTTP read returned.
 *  The room broadcast reaches every participant, including members of groups
 *  this caller may not read ([R30.42]), so a WS event never inserts — it
 *  updates what is already here, and records an unrecognized group in
 *  `unseenGroupIds` for the composable to decide whether a refetch is even
 *  this caller's business. */
export interface ProposalRoomState {
  activationId: string | null
  proposals: Record<string, ActivityGroupProposal>
  eligibleGroups: ActivityMemberGroupRef[]
  unseenGroupIds: string[]
  version: number
}

/** A room broadcast's proposal payload: ids, a status, and counts. The worker's
 *  expiry sweep holds no tally, so everything but the status is optional. */
export interface ProposalEvent {
  proposalId: string
  memberGroupId: string | null
  status: string
  requiredApprovals?: number | null
  approvals?: number | null
  rejections?: number | null
  undecided?: number | null
  voterCount?: number | null
}

function emptyProposalRoom(): ProposalRoomState {
  return { activationId: null, proposals: {}, eligibleGroups: [], unseenGroupIds: [], version: 0 }
}

export const useActivitiesStore = defineStore('activities', () => {
  // Map<roomId, Map<submissionId, ActivityOutcome>>
  const outcomes = reactive<Record<string, Record<string, ActivityOutcome>>>({})
  const activations = reactive<Record<string, ActivationView | null>>({})
  const activationVersions = reactive<Record<string, number>>({})
  const proposalRooms = reactive<Record<string, ProposalRoomState>>({})

  function ensureRoom(roomId: string): Record<string, ActivityOutcome> {
    if (!outcomes[roomId]) outcomes[roomId] = {}
    return outcomes[roomId]
  }

  /** Seed/refresh from an authoritative submission (submit response or list). */
  function upsertFromSubmission(roomId: string, sub: ActivitySubmission): void {
    ensureRoom(roomId)[sub.id] = {
      submissionId: sub.id,
      activityTypeId: sub.activity_type_id,
      status: normalizeValidationStatus(sub.validation_status),
      isValid: sub.is_valid,
      subScores: sub.sub_scores ?? {},
    }
  }

  /** WS `activity.created`: the submission exists; status may already be final
   *  (in-process validators) or `pending` (async). Never downgrade richer data
   *  the submit response may already have written. */
  function applyCreated(
    roomId: string,
    ev: { submissionId: string; activityTypeId: string | null; status: string },
  ): void {
    const room = ensureRoom(roomId)
    const prev = room[ev.submissionId]
    room[ev.submissionId] = {
      submissionId: ev.submissionId,
      activityTypeId: ev.activityTypeId ?? prev?.activityTypeId ?? null,
      status: normalizeValidationStatus(ev.status),
      isValid: prev?.isValid ?? null,
      subScores: prev?.subScores ?? {},
    }
  }

  /** WS `activity.validated`: pending -> validated/error. The event carries no
   *  is_valid/sub_scores, so keep whatever we already know (see FU-2). */
  function applyValidated(
    roomId: string,
    ev: { submissionId: string; status: string },
  ): void {
    const room = ensureRoom(roomId)
    const prev = room[ev.submissionId]
    room[ev.submissionId] = {
      submissionId: ev.submissionId,
      activityTypeId: prev?.activityTypeId ?? null,
      status: normalizeValidationStatus(ev.status),
      isValid: prev?.isValid ?? null,
      subScores: prev?.subScores ?? {},
    }
  }

  function getOutcome(roomId: string, submissionId: string): ActivityOutcome | undefined {
    return outcomes[roomId]?.[submissionId]
  }

  function setActivation(roomId: string, activation: ActivityActivation | ActivationView): void {
    activations[roomId] = 'activity_type_id' in activation
      ? {
          id: activation.id,
          activityTypeId: activation.activity_type_id,
          startedByUserId: activation.started_by_user_id,
          activityType: activation.activity_type ?? null,
          startedByAgentId: activation.started_by_agent_id ?? null,
          startedByAgentName: activation.started_by_agent_name ?? null,
        }
      : activation
    activationVersions[roomId] = (activationVersions[roomId] ?? 0) + 1
  }

  function clearActivation(roomId: string, activationId?: string): void {
    if (!activationId || activations[roomId]?.id === activationId) {
      activations[roomId] = null
      activationVersions[roomId] = (activationVersions[roomId] ?? 0) + 1
    }
  }

  function getActivation(roomId: string): ActivationView | null | undefined {
    return activations[roomId]
  }

  function getActivationVersion(roomId: string): number {
    return activationVersions[roomId] ?? 0
  }

  // ---- group proposals ([R30.41], [R30.42]) --------------------------------

  function ensureProposalRoom(roomId: string): ProposalRoomState {
    if (!proposalRooms[roomId]) proposalRooms[roomId] = emptyProposalRoom()
    return proposalRooms[roomId]!
  }

  /** Adopt the server's answer for one round wholesale.
   *
   *  Replaces rather than merges: this read IS the authorization boundary, so a
   *  proposal it did not return is one this caller may no longer see, and
   *  keeping it would leave a card on screen that the server has stopped
   *  vouching for. */
  function setRound(
    roomId: string,
    round: {
      activationId: string
      proposals: ActivityGroupProposal[]
      eligibleGroups: ActivityMemberGroupRef[]
    },
  ): void {
    const room = ensureProposalRoom(roomId)
    room.activationId = round.activationId
    room.proposals = Object.fromEntries(round.proposals.map((p) => [p.id, p]))
    room.eligibleGroups = round.eligibleGroups
    room.unseenGroupIds = []
    room.version += 1
  }

  /** Write back a proposal the caller's own request returned (propose, vote,
   *  withdraw). Authoritative and complete, unlike a broadcast. */
  function upsertProposal(roomId: string, proposal: ActivityGroupProposal): void {
    const room = ensureProposalRoom(roomId)
    room.proposals = { ...room.proposals, [proposal.id]: proposal }
    room.unseenGroupIds = room.unseenGroupIds.filter((id) => id !== proposal.member_group_id)
    room.version += 1
  }

  /** WS `activity.proposal.opened|voted|resolved`: ids and counts only.
   *
   *  Updates a known proposal in place; an unknown one is recorded by group id
   *  and NOT inserted, because this channel is a blind relay to the whole room
   *  and the payload carries no evidence the caller may read that group's vote.
   *  Whether to refetch is the composable's call, made against the eligible set
   *  the server returned. */
  function applyProposalEvent(roomId: string, ev: ProposalEvent): void {
    const room = ensureProposalRoom(roomId)
    const known = room.proposals[ev.proposalId]
    if (known) {
      room.proposals = {
        ...room.proposals,
        [ev.proposalId]: {
          ...known,
          status: ev.status,
          // Each count is adopted only when the event carried one: the worker's
          // expiry sweep sends a status with no tally, and folding its absent
          // counts in as 0 would report a settled vote as unanimous abstention.
          required_approvals: ev.requiredApprovals ?? known.required_approvals,
          approvals: ev.approvals ?? known.approvals,
          rejections: ev.rejections ?? known.rejections,
          undecided: ev.undecided ?? known.undecided,
          voter_count: ev.voterCount ?? known.voter_count,
        },
      }
    } else if (ev.memberGroupId && !room.unseenGroupIds.includes(ev.memberGroupId)) {
      room.unseenGroupIds = [...room.unseenGroupIds, ev.memberGroupId]
    }
    room.version += 1
  }

  function getProposalRoom(roomId: string): ProposalRoomState | undefined {
    return proposalRooms[roomId]
  }

  function clearProposals(roomId: string): void {
    delete proposalRooms[roomId]
  }

  function resetRoom(roomId: string): void {
    delete outcomes[roomId]
    delete activations[roomId]
    delete activationVersions[roomId]
    delete proposalRooms[roomId]
  }

  function clearAll(): void {
    Object.keys(outcomes).forEach((k) => delete outcomes[k])
    Object.keys(activations).forEach((k) => delete activations[k])
    Object.keys(activationVersions).forEach((k) => delete activationVersions[k])
    Object.keys(proposalRooms).forEach((k) => delete proposalRooms[k])
  }

  // Reset on session.clear() without the session store importing this one (H14).
  registerCleanup(clearAll)

  return {
    outcomes,
    activations,
    proposalRooms,
    upsertFromSubmission,
    applyCreated,
    applyValidated,
    getOutcome,
    setActivation,
    clearActivation,
    getActivation,
    getActivationVersion,
    setRound,
    upsertProposal,
    applyProposalEvent,
    getProposalRoom,
    clearProposals,
    resetRoom,
    clearAll,
  }
})
