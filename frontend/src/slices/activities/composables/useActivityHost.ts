// Host-mediation core (R30.17). Owns the single submit path every plugin and the
// schema form funnel through, plus the reactive outcome for the last submission.
// Kept out of the component so it is unit-testable with a mocked api-client.

import { computed, ref, toValue, type ComputedRef, type MaybeRefOrGetter, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { submitActivity } from '../api'
import { useActivitiesStore } from '../stores/activities'
import { normalizeValidationStatus, type ActivityOutcome, type ActivitySubmissionResult } from '../types'

export interface UseActivityHostOptions {
  chatroomId: MaybeRefOrGetter<string>
  activityTypeId: MaybeRefOrGetter<string>
  sessionId?: MaybeRefOrGetter<string | null>
  subjectUserId?: MaybeRefOrGetter<string | null>
}

export interface UseActivityHost {
  submit(payload: unknown): Promise<ActivitySubmissionResult>
  submitting: Ref<boolean>
  errorMessage: Ref<string | null>
  lastSubmissionId: Ref<string | null>
  outcome: ComputedRef<ActivityOutcome | undefined>
}

export function useActivityHost(options: UseActivityHostOptions): UseActivityHost {
  const store = useActivitiesStore()
  const { t } = useI18n()

  const submitting = ref(false)
  const errorMessage = ref<string | null>(null)
  const lastSubmissionId = ref<string | null>(null)

  const outcome = computed<ActivityOutcome | undefined>(() =>
    lastSubmissionId.value
      ? store.getOutcome(toValue(options.chatroomId), lastSubmissionId.value)
      : undefined,
  )

  // The client score in `payload` is forwarded verbatim but never consulted for
  // the outcome — the returned result is the backend's (AC-5).
  async function submit(payload: unknown): Promise<ActivitySubmissionResult> {
    const chatroomId = toValue(options.chatroomId)
    errorMessage.value = null
    submitting.value = true
    try {
      const sub = await submitActivity(chatroomId, {
        activity_type_id: toValue(options.activityTypeId),
        payload: (payload ?? {}) as Record<string, unknown>,
        session_id: toValue(options.sessionId) ?? null,
        subject_user_id: toValue(options.subjectUserId) ?? null,
      })
      store.upsertFromSubmission(chatroomId, sub)
      lastSubmissionId.value = sub.id
      return {
        submissionId: sub.id,
        status: normalizeValidationStatus(sub.validation_status),
        isValid: sub.is_valid,
        subScores: sub.sub_scores ?? {},
      }
    } catch (err) {
      errorMessage.value =
        err instanceof ApiError ? err.message : t('activities.host.submitFailed')
      throw err
    } finally {
      submitting.value = false
    }
  }

  return { submit, submitting, errorMessage, lastSubmissionId, outcome }
}
