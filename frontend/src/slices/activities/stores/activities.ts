// Ephemeral per-room activity outcomes, keyed by submission id (R30.17). The
// chatroom WS switch (in slices/conversation, which depends on activities per
// Q-3) drives `applyCreated` / `applyValidated`; the host seeds richer data from
// the submit HTTP response via `upsertFromSubmission`. Keyed by submission id so
// a pending -> validated transition updates one entry with no list refetch.

import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { registerCleanup } from '@shared/stores/useAppCleanup'
import { normalizeValidationStatus } from '../types'
import type { ActivityOutcome, ActivitySubmission } from '../types'

export const useActivitiesStore = defineStore('activities', () => {
  // Map<roomId, Map<submissionId, ActivityOutcome>>
  const outcomes = reactive<Record<string, Record<string, ActivityOutcome>>>({})

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

  function resetRoom(roomId: string): void {
    delete outcomes[roomId]
  }

  function clearAll(): void {
    Object.keys(outcomes).forEach((k) => delete outcomes[k])
  }

  // Reset on session.clear() without the session store importing this one (H14).
  registerCleanup(clearAll)

  return {
    outcomes,
    upsertFromSubmission,
    applyCreated,
    applyValidated,
    getOutcome,
    resetRoom,
    clearAll,
  }
})
