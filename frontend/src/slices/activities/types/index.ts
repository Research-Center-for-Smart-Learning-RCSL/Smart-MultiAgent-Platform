// Slice-local domain types. The wire shapes come from the generated client; the
// slice adds the `ActivityOutcome` view-model the store and outcome badge key on.

import type {
  ActivitySessionOut,
  ActivitySubmissionOut,
  ActivityTypeOut,
  ActivityActivationOut,
} from '@shared/api-client'
import type { ActivityValidationStatus } from '../sdk/types'

export type ActivityType = ActivityTypeOut
export type ActivitySubmission = ActivitySubmissionOut
export type ActivitySession = ActivitySessionOut
export type ActivityActivation = ActivityActivationOut

/** Compact activation state used by HTTP hydration and the ids-only room WS events. */
export interface ActivationView {
  id: string
  activityTypeId: string
  startedByUserId: string | null
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
