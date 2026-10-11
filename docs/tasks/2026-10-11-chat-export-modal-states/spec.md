---
type: bugfix
status: approved
created: 2026-10-11
requirements: [R13.17]
depends_on: [2026-10-11-browser-file-delivery]
---

# Chat export modal: terminal states, input errors, and superseded requests

## 1. Summary

The chat export modal can strand the user in three ways. First, an export that takes longer
than about three minutes leaves the progress bar spinning forever, even after the job finishes.
A permanent error from the status endpoint does the same. Second, an end date before the start
date, or a job that fails in the worker, both produce the same "Failed to start export."
message, and the Retry button re-submits the invalid input. Third, reopening the modal while a
create request is still in flight replaces the fresh form with the previous job's state, and a
double click creates two jobs.

This dossier gives the modal a complete set of terminal states and makes the composable
ignore superseded requests. It originates from
`docs/audits/2026-10-11-chat-and-research-export/findings.md` F-3, F-8 and F-9.

## 2. Observed vs Expected

**Observed.**

- **F-3.**
  - `useChatroomExport.ts:23-25` polls at most 60 times at the 3-second default interval
    (`frontend/src/shared/composables/usePolling.ts:36`), about 180 seconds in total.
  - Once attempts run out, `tick` returns without a callback (`usePolling.ts:57`), so
    `exportJob` stays `queued` or `running`, and the modal shows the indeterminate bar with no
    action (`ChatroomExportModal.vue:47-53`).
  - Every fetch error is treated as transient (`usePolling.ts:70-74`). A permanent 403, a 404
    (`ExportJobNotFound` once the Redis record is gone) or a 500 is therefore retried until
    exhaustion and ends in the same stuck state.
  - The worker allows 600 seconds (`backend/app/workers/main.py:363`), and the Arq queue is
    shared with agent turns.
- **F-8.**
  - The submit guard checks only that the custom dates are non-empty
    (`ChatroomExportModal.vue:129`). An `end` before `start` reaches the API and is rejected
    with 422 (`backend/app/api/v1/exports.py:55-56`).
  - `runExport` maps every create error to `failed` plus the "Failed to start export." toast
    (`useChatroomExport.ts:41-44`).
  - The failed state reuses that string for a job that started and then failed in the worker
    (`ChatroomExportModal.vue:34-44`, `locales/en.json:97`).
  - Retry calls `submit` with the unchanged form values (`:39-44,195-202`).
- **F-9.**
  - `openExport` calls `resetExport()`, which clears `activeJobId` (`ChatroomView.vue:1701-1706`).
    A `createExport` still pending then resolves and writes `activeJobId` and `exportJob`
    without checking whether it was superseded (`useChatroomExport.ts:37-40`).
  - Nothing disables the submit button while the request is pending. Two quick clicks both see
    `activeJobId === null`, both create a job, and the losing job's poller runs until
    exhaustion.

**Expected.**

- The flow ends in a ready or a failed state, with Cancel and Retry on failure
  (`docs/UI/07-conversation.md` §3.9 steps 4-6).
- A superseded job never renders into a fresh form. This is
  `docs/tasks/2026-07-22-chat-export-authz-and-polling/spec.md` F-16, whose fix covered the
  polling window but not the create window.
- Invalid input is reported as invalid input, before it is sent wherever the client can know.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should a slow export behave? | Poll for about 12 minutes with a widening interval: 3 s for the first minute, then 6 s, then 10 s. On exhaustion, show a "still processing" state with a "Check again" button that resumes polling the same job. A 403, 404 or 410 from the status endpoint is fatal: it stops polling and shows the failed state. | Chosen at the 2026-10-11 spec session over replacing polling with a notification. 12 minutes covers the 600-second worker timeout plus queue wait. "Check again" never abandons a job that may still finish. The fatal set is exactly the responses that cannot change on retry. |
| Q-2 | Where do the new behaviors live in the poller? | `usePolling` gains two optional options. `isFatal(error) => boolean` stops the key and calls `onError(key, error)`. `onExhausted(key)` is called once when attempts run out. It also accepts a per-attempt interval function, `intervalMs: number \| ((attempt) => number)`. The defaults keep current behavior. | `useChatroomExport` is the only production consumer (`usePolling.ts`, `useChatroomExport.ts`; repo grep, 2026-10-11), but the poller is shared by design (`usePolling.ts:1-15`). Optional hooks keep it general without a second poller. |
| Q-3 | Client-side validation for the custom range. | The submit button is disabled and an inline field error is shown when `end < start`. Any 422 that still arrives is shown as an inline form error, and the form stays open with its values. | The client can know this condition; the API accepts future dates, so the client does not invent a stricter rule. A 422 is an input problem, so it belongs on the form, not in a dead-end failed state. |
| Q-4 | Messages and Retry for the two failure kinds. | A create failure that is not a 422 shows "Could not start the export." with Retry, which re-submits. A worker failure shows the UI doc's "Export failed. Unable to generate the archive." with Cancel and Retry. Retry returns to the configuration form with the previous values, rather than re-submitting blindly. The backend `error` text is never shown. | The UI doc names the worker-failure copy. Returning to the form lets the user change the range or format that may have caused the failure. The `error` field carries raw exception text (audit FU-2), which is not user copy. |
| Q-5 | How are superseded create requests ignored? | The composable keeps a monotonically increasing request token. `runExport` records it before awaiting, and discards the response if `reset()` or another `runExport` has advanced it since. Submit is disabled while a create is pending. | A token covers both the reopen race and the double click with one mechanism. Disabling the button is the visible half. The token is what makes a late response harmless. |
| Q-6 | Dependencies. | `depends_on: [2026-10-11-browser-file-delivery]`, an overlap prerequisite. | That dossier rewrites the same composable (adds `download()`, `useChatroomExport.ts`), the same modal's ready branch (`ChatroomExportModal.vue:14-32`), and the same locale keys. `2026-10-11-chat-export-archive-content` in turn depends on this one, because it adds a form control to the same modal. |

## 4. Reproduction

Preconditions: a signed-in user who can read a room, with the modal open.

1. **F-3.** Stop the worker
   (`docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/compose.test.yml stop backend-worker`),
   then submit an export. After about 3 minutes, the progress bar is still spinning, and it
   keeps spinning after the worker is started again and the job completes.
2. **F-3, fatal case.** Submit an export, then delete its Redis record
   (`redis-cli -n 1 DEL chat_export:<job_id>`). The modal spins for about 3 minutes, then
   stays stuck.
3. **F-8.** Choose Custom Range with an end date before the start date and submit. A "Failed to
   start export." toast appears. Each Retry repeats the 422.
4. **F-9.** Throttle the network in devtools, submit, close the modal at once, and reopen it.
   When the create resolves, the configuration form is replaced by a progress state.

## 5. Root Cause Analysis

- **F-3.** `usePolling` has exactly one way to report, `onResult`, and only for a successful
  fetch (`usePolling.ts:56-76`). Exhaustion and permanent errors therefore have no channel to
  the consumer. The root cause is the poller's missing terminal outcomes. The 60-attempt budget
  is an aggravating factor, but raising it alone still strands the UI on any slower job and on
  any 404.
- **F-8.** The composable collapses every outcome that is not a success into one state
  (`useChatroomExport.ts:41-44`), and the modal has one string for it. The root cause is the
  lack of a distinction between input errors, start errors and job failures in the
  composable's state type (`exportJob: Pick<ExportStatus, 'status' | 'url'>`, `:17`).
- **F-9.** The guard added for F-16 checks the job id at result time (`:27`), but the create
  response that assigns the job id is never checked (`:37-40`). The root cause is that
  supersession is tracked by job id, which does not exist until the create returns.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every chat export user on a slow worker, on a custom range, or with quick
hands. No data is affected. Duplicate jobs only cost worker time and add a duplicate
`message.exported` audit row.

**Sibling suspects.**

- `ResearchExportButton.vue` gives up after 60 polls with a timeout toast (`:74`). It has the
  same budget problem but does report it. It is cleared of the stuck state, and its delivery is
  rewritten by `browser-file-delivery`.
- The skill bundle polling (`skills/queries/index.ts:101-111`) uses Vue Query's
  `refetchInterval`, not `usePolling`. It is out of scope and recorded as FU-1.
- No other `usePolling` consumer exists.

## 7. Fix Design

1. **`usePolling.ts`.** Add the optional `isFatal`, `onError`, `onExhausted` and
   function-valued `intervalMs`, as in Q-2. Exhaustion deletes the key from `active` and
   `timers` before calling `onExhausted`, so a later `start(key)` resumes cleanly. The existing
   five tests must pass unchanged.
2. **`useChatroomExport.ts`.** The exported state becomes a discriminated union. The fields
   added by `browser-file-delivery` stay as that dossier specifies them.

   ```ts
   type ExportPhase =
     | { kind: 'idle' }
     | { kind: 'starting' }
     | { kind: 'running'; jobId: string; status: 'queued' | 'running' }
     | { kind: 'stalled'; jobId: string }
     | { kind: 'ready'; jobId: string }
     | { kind: 'start-failed'; opts: ExportOptions }
     | { kind: 'job-failed'; opts: ExportOptions }
     | { kind: 'invalid'; field: 'start' | 'end' | null }
   ```

   - A request token implements Q-5.
   - `checkAgain()` restarts polling from `stalled`.
   - `retry()` re-submits from `start-failed` and returns to the form with the previous
     options from `job-failed`.
   - The fatal set is HTTP 403, 404 and 410, read from the `@shared/errors` problem status.
     It must not depend on the generated `ApiError` (frontend gate #13).
3. **`ChatroomExportModal.vue`.** The modal renders from `phase.kind`, and the form keeps its
   values while it is shown again.
   - **`starting`.** The submit button is disabled and shows `SLoadingSpinner`.
   - **`stalled`.** A `ClockIcon` with "Still processing. Large rooms can take several
     minutes." and a "Check again" button.
   - **Two failure states.** Per Q-4.
   - **`invalid`.** An inline error on the offending date field, with submit disabled.
   - **Client validation.** Per Q-3, through the form's computed validity.
4. **Locales.** New keys in `conversation.chatroom` (en and zh-TW): `exportStalled`,
   `exportCheckAgain`, `exportStartFailed`, `exportJobFailed`, `exportJobFailedDetail`
   and `exportRangeInvalid`. `exportFailed` is removed once nothing reads
   it. No literal `@` (the `reference_i18n_literal_at` pitfall).

5. **Quality notes.**
   - *Existing debt not to imitate.* `ChatroomExportModal.vue:175` casts `'date' as unknown as
     'text'` around `SInput`'s prop union. Keep the cast local, and do not widen `SInput` in
     this change.
   - *Patterns.*
     - Composables own state and API calls, and the modal is presentational, emitting intents
       (`frontend/CLAUDE.md` Patterns).
     - Errors are read through `@shared/errors`, never through the generated `ApiError`
       (gate #13).
     - Icons come from `@heroicons/vue/24/outline`.
   - *Reuse.*
     - `usePolling`, extended rather than forked.
     - `useToast`.
     - `SFormField`'s `error` prop for inline field errors (it sets `aria-invalid`,
       `SFormField.vue:7,27-30`).
     - `SProgressBar`.
     - A disabled `SButton` with `SLoadingSpinner` for the pending state (`SButton` has no
       loading prop).
     - The existing `conversation.chatroom` locale namespace.

## 8. Regression Test Plan

Write these failing tests first:

- **`usePolling.test.ts`.**
  - `onExhausted` fires once after `maxAttempts`, and `start` afterwards polls again.
  - An `isFatal` error stops polling after one attempt and calls `onError`.
  - A function `intervalMs` is consulted per attempt.
  - The existing tests stay green.

  These fail today because the options do not exist.
- **`useChatroomExport.test.ts`.**
  - **Fake timers.** A job still `running` after the budget yields `stalled`; `checkAgain()`
    then receiving `ready` yields `ready`.
  - **A 404 from `getExport`.** Yields `job-failed` after one attempt.
  - **A create that resolves after `reset()`.** Leaves `idle`. This is the F-9 regression; it
    fails today because the late response writes the slot.
  - **Two `runExport` calls before the first resolves.** Only the second job is polled.
  - **A 422 create.** Yields `invalid`.
- **`ChatroomExportModal.test.ts` (new; no modal test exists today).**
  - Submit is disabled for `end < start` and while starting.
  - Each phase renders its copy and actions.
  - Retry from `job-failed` shows the form with the previous values.

## 9. Risks and Rollback

- **Longer polling.** A long-open modal polls longer. At about 100 requests per export it is
  negligible against the `OTHER` budget.
- **The `exportJob` shape change.** It touches `ChatroomView.vue:450`, the only reader, and
  `ChatroomView.test.ts:263-319`. Both are updated in this change.
- **Rollback.** Revert the series. There is no backend change and no persisted state.

## 10. Acceptance Criteria

- [ ] AC-1: The §8 tests fail before the fix and pass after.
- [ ] AC-2: With the worker stopped, the modal reaches "Still processing" within about 12
  minutes. After the worker is restarted and the job completes, "Check again" shows the ready
  state and the download works.
- [ ] AC-3: A status 404 (Redis record deleted) shows the failed state within one poll
  interval.
- [ ] AC-4: An end date before the start date cannot be submitted and shows an inline error. A
  server 422 keeps the form open with its values.
- [ ] AC-5: A worker failure shows "Export failed. Unable to generate the archive.", and Retry
  returns to the form with the previous values.
- [ ] AC-6: Submit, close, reopen: the form stays a form. A double click creates one job
  (verified by the worker log or the audit rows).
- [ ] AC-7: `pnpm lint`, `pnpm typecheck`, and the i18n gate pass. The new keys exist in both
  locales.

## 11. SRS Delta

None. The UI doc already specifies the ready and failed end states and the Retry action.
§3.9 gains one sentence for the stalled state when this dossier is approved (applied 2026-10-11): "If the job is
still running after the polling window, the modal shows 'Still processing' with a 'Check
again' action that resumes polling."

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** The skill bundle export polls with `refetchInterval` and has its own exhaustion
  semantics (`skills/queries/index.ts:101-111`). Check whether it can strand the bundle dialog
  the same way.
