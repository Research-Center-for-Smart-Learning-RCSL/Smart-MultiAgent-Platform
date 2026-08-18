// Slice-local domain types. The wire shapes come from the generated client; the
// slice adds the `ActivityOutcome` view-model the store and outcome badge key on.

import type {
  ActivityActivationProgressOut,
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
