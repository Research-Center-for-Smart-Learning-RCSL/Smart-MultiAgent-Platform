// Slice-local domain types. The wire shapes come from the generated client; the
// slice adds the `ActivityOutcome` view-model the store and outcome badge key on.

import type {
  ActivityActivationProgressOut,
  ActivityGroupProposalOut,
  ActivityGroupVoteOut,
  ActivityMemberGroupRefOut,
  ActivitySessionOut,
  ActivitySubmissionOut,
  ActivityTypeOut,
  ActivityTypePublicOut,
  ActivityActivationOut,
} from '@shared/api-client'
import type { ActivityValidationStatus } from '../sdk/types'

export type ActivityType = ActivityTypeOut
/** The participant rendering contract (R30.26): id/key/name/payload_schema
 *  only, reachable through the room-access chain — never `validator_config`. */
export type ActivityTypePublic = ActivityTypePublicOut
export type ActivitySubmission = ActivitySubmissionOut
export type ActivitySession = ActivitySessionOut
export type ActivityActivation = ActivityActivationOut
/** Counts only ([R30.22]) — the facilitator's view of one round. There is
 *  deliberately no per-subject roster behind this. */
export type ActivityActivationProgress = ActivityActivationProgressOut

/** Compact activation state used by HTTP hydration and the ids-only room WS events. */
export interface ActivationView {
  id: string
  activityTypeId: string
  /** Always the human whose authority the round runs on — the facilitator for an
   *  ordinary round, the granting teacher for a delegated one ([R30.37]). */
  startedByUserId: string | null
  /** Embedded rendering contract (Q-1); `null` when the broadcast/read carried
   *  none (missed event, cross-project type) — the panel falls back to a
   *  room-scoped fetch in that case. */
  activityType: ActivityTypePublic | null
  /** The agent that started this round, when one did ([R30.37]). Both are absent
   *  for a human-started round; the name alone can also be absent if the agent has
   *  since been deleted, which is why they are two fields and not one. */
  startedByAgentId?: string | null
  startedByAgentName?: string | null
}

/** A group's proposal and where its vote stands ([R30.41]). Carries the payload,
 *  because its readers are the people being asked to approve it — unlike the
 *  room broadcast, which is counts only. */
export type ActivityGroupProposal = ActivityGroupProposalOut
/** One pinned voter's decision. Empty for a caller not entitled to the
 *  per-person record ([R30.42]); the counts on the proposal are still there. */
export type ActivityGroupVote = ActivityGroupVoteOut
/** A group the caller may propose for: id and display name, nothing else. */
export type ActivityMemberGroupRef = ActivityMemberGroupRefOut

/** A proposal's lifecycle. `open` is the only state that accepts a vote; every
 *  other one is terminal ([R30.41]). */
export type ProposalStatus = 'open' | 'accepted' | 'rejected' | 'withdrawn' | 'expired'

const TERMINAL_PROPOSAL_STATUSES = new Set(['accepted', 'rejected', 'withdrawn', 'expired'])

/** Whether a proposal has settled. An unrecognized status is treated as open,
 *  matching `normalizeValidationStatus`'s bias: the client keeps showing a card
 *  it cannot classify rather than silently hiding a live vote. */
export function isProposalOpen(status: string): boolean {
  return !TERMINAL_PROPOSAL_STATUSES.has(status)
}

/** The consent fraction a type declares ([R30.40]). `group_config` is a bare
 *  JSON object on the wire; this is the only shape the panel reads out of it. */
export interface GroupConsent {
  numerator: number
  denominator: number
}

/** Read the consent fraction off a type's `group_config`, or null when the type
 *  is individual-only or its config is not the shape this client understands.
 *
 *  Null is the safe answer in both cases: the panel falls back to the individual
 *  path, and the server is the authority on whether a group submission is
 *  possible at all. */
export function readGroupConsent(groupConfig: Record<string, unknown> | null | undefined): GroupConsent | null {
  const consent = (groupConfig ?? {}).consent
  if (!consent || typeof consent !== 'object') return null
  const { numerator, denominator } = consent as Record<string, unknown>
  if (!Number.isInteger(numerator) || !Number.isInteger(denominator)) return null
  if ((numerator as number) <= 0 || (denominator as number) <= 0) return null
  return { numerator: numerator as number, denominator: denominator as number }
}

export type { ActivityValidationStatus, ActivitySubmissionResult } from '../sdk/types'

/** Per-submission outcome the store tracks and the badge renders. */
export interface ActivityOutcome {
  submissionId: string
  activityTypeId: string | null
  status: ActivityValidationStatus
  isValid: boolean | null
  subScores: Record<string, unknown>
}

/** Coerce a raw backend `validation_status` string to the known union; an
 *  unrecognized value is treated as still-pending rather than crashing. */
export function normalizeValidationStatus(raw: string): ActivityValidationStatus {
  return raw === 'validated' || raw === 'error' ? raw : 'pending'
}
