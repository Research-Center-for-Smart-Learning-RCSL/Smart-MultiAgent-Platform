---
type: feature
status: implemented
created: 2026-08-17
requirements: [R30.01, R30.21, R30.22, R30.26, R30.33]
depends_on: []
---

# Activity participant lifecycle — no self-serve session, sessions bound to a round, facilitator-visible completion

## 1. Summary

In a chatroom's Activity rail tab, a participant (including a guest) currently has to press
**Start** before any worksheet appears and **Finish** to close their own session
(`ActivityPanel.vue:231-239`, `:223-229`). Neither step exists for a domain reason: the submit
path already opens a session lazily when none is supplied
(`submission_service.py:358-395`), and the facilitator's end never closes participant sessions
(`activation_service.py:101-132`), so the pair is an unnecessary front gate on the participant
and a data hazard behind it.

This task removes both buttons, binds an `ActivitySession` to the `ActivityActivation` it was
answered under so a second round of the same activity in the same room is a separate attempt
history, closes a round's sessions when the facilitator ends it (the deferred FU-2 of
`2026-07-13-activities-activation-ux/spec.md:368-369`), and replaces the vestigial **Finish**
with a real, reversible **I'm done** signal that the facilitator can see as a live
completed/in-progress count.

## 2. Goals and Non-goals

**Goals**

- A participant who is in a room with an active activity sees the worksheet immediately, with
  no button to press first. Their session is created by their first submission.
- An `ActivitySession` belongs to exactly one `ActivityActivation`. Two activations of the same
  type in the same room produce two sessions per subject, each with its own `attempt_no`
  sequence.
- Ending an activation (by any of the four paths that end one) closes every session belonging
  to it, so no session outlives its round.
- A participant can declare themselves done and undo it; the declaration is separate from the
  session's open/closed lifecycle and never blocks further submissions.
- The facilitator sees a live count of completed vs in-progress participants for the running
  activation, seeded by a room-scoped read and updated over WebSocket.
- The two "Start" labels stop colliding: the facilitator's start-for-room stays, the
  participant's `join` key is gone.

**Non-goals**

- **No change to the facilitator's activation authority.** Who may start/end an activity
  ([R30.21], `activities.py:650-701`) is untouched — this task changes only what a participant
  must do inside a round the facilitator already started.
- **No change to submission authorization.** [R30.22]'s "an active activation for that exact
  type must exist" gate stays exactly as it is (`submission_service.py:102-104`).
- **No roster denominator.** The facilitator's count reports completed and in-progress
  *sessions*, not "x of y people in the room" — a roster denominator would need the
  conversation context's membership and would count people who never opened the tab.
- **No per-subject identity in the completion surface.** The facilitator sees counts, not a
  named list of who has finished. A named roster is a different privacy decision and belongs to
  its own dossier (FU-1).
- **No removal of the session open/close endpoints.** Q-1 keeps them; only their contract
  tightens.
- **No new activity types, validators, plugins, or scoring changes.**

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | The two session endpoints (`POST .../activity-sessions`, `PATCH .../activity-sessions/{id}/close`) lose their only caller once the participant buttons go. Keep or remove? | Keep both, but `open_session` now requires an active activation for that type and binds the session to it; opening with none active raises `ActivityNotActive` (409) | User's call. Keeping them preserves the admin arm (`activities.py:736`, `:756`) and the API contract; requiring an activation closes the remaining door onto a session that no round owns, which is the same hole this task closes on the lazy-open path |
| Q-2 | Existing `open` sessions have no `activation_id`. What does the migration do with them? | Backfill from the room's currently-active activation for the same type where one exists; close the rest | User's call. A class under way when the migration runs keeps its attempt history; a session with no active activation of its type can receive no submission anyway ([R30.22]) and is exactly the stale row that would otherwise be adopted by the next round |
| Q-3 | Remove the participant's Finish outright, or turn it into a facilitator-visible completion signal? | Build the completion signal in this dossier | User's call. Removing it alone leaves the facilitator with no way to know when to move on, which is the pedagogical need the button pretended to serve |
| Q-4 | Should completion be the session's `closed` status, or its own field? | Its own `completed_at` column; `status` keeps meaning "can this session still receive submissions" | Overloading `closed` would make the facilitator's end-of-round cascade (a Goal above) inflate the completion count, and would make an accidental click terminal. Two concepts, two columns |
| Q-5 | Is completion reversible, and what happens if a participant submits after declaring done? | Reversible by the participant; a submission clears the mark | An 8th-grader mis-clicking must not lock themselves out for the rest of the class. "Declared done, still submitting" is not a state worth representing, so the submit clears it rather than the UI nagging |
| Q-6 | Who receives the completion event? | The activation's `started_by_user_id`, on their user channel; non-starter facilitators (admin, moderator on a NULL-creator room) poll | Mirrors [R28.13] exactly: `observations.py:247` emits per-recipient on `user_channel`, and `useObservations.ts:110-111` polls for the viewers who pass the REST gate but never receive events. A room-channel broadcast would tell every student how many peers have finished, which in a 2-person group identifies the other one |
| Q-7 | Does anything under `docs/tasks/` block this? | No | `BOARD.md` lists two unfinished dossiers: `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`. Neither touches the activities context or `ChatroomView.vue`'s rail |

## 4. Current State

### 4.1 Three lifecycle layers, only two of them owned by the facilitator

- **`ActivityType`** — project- or platform-scoped template, owned by a Project Owner
  ([R30.23]).
- **`ActivityActivation`** — room-level, one active per room, started and ended only by the room
  creator (`activities.py:658-659`, `:685-686`; [R30.21]). This layer is already
  "the owner decides".
- **`ActivitySession`** — per `(activity_type_id, chatroom_id, subject_user_id)`
  (`tables.py:53-78`), created and closed by the participant themselves through
  `ActivityPanel.startSession`/`finishSession` (`ActivityPanel.vue:160-188`).

### 4.2 The participant's Start button gates nothing

`ActivityPanel` renders `ActivityHost` only once its local `activitySession` ref is set
(`ActivityPanel.vue:216-230`); before that it renders the Start button (`:231-239`). That ref is
component-local (`:33`) and the panel never queries whether the caller already has an open
session — `hydrate()` reads only the activation (`:60-73`). So a refresh, a tab switch that
unmounts the panel, or a reconnect drops the worksheet and the participant presses Start again;
`open_session` is idempotent (`session_service.py:69-73`) so the second press returns the same
row and changes nothing.

The button is not required by the backend at all. `submitActivity` sends
`session_id: null` when the panel has no session (`useActivityHost.ts:48-53`), and
`SubmissionService._resolve_session` opens one on the spot (`submission_service.py:378-395`).

### 4.3 Finish has a real side effect presented as a UI toggle

`finishSession` calls `closeActivitySession` (`ActivityPanel.vue:176-188`), which closes the row
(`session_repo.py:110-123`). Because `_resolve_session` looks only for an **open** session
(`submission_service.py:378-381`), a participant who finishes and then answers again gets a new
session and `attempt_no` restarts at 1 (`:120`). `ActivityAggregate` is a per subject/session/room
read model (`models.py:274-277`), so one stray click splits a subject's history into two
aggregates. The button returns nothing to the participant: it submits nothing, notifies nobody,
and produces no confirmation.

### 4.4 Ending a round leaves its sessions open, and a later round adopts them

`ActivationService.end` transitions only the activation row (`activation_service.py:101-132`).
This was deliberate — `2026-07-13-activities-activation-ux/spec.md:50-51` calls force-closing
"cosmetic once submits are blocked", and `:368-369` records it as FU-2.

Two consequences:

1. Open sessions accumulate. The only sweeps are type deletion (`facade.py:556-557` via
   `session_repo.py:125-144`) and a project's opt-out of a platform type
   (`example_service.py:358`).
2. Because the session key omits the activation, a facilitator running the same activity twice
   in one room (start, end, start) has every participant's second-round submissions land in the
   still-open first-round session, continuing its `attempt_no` sequence. The two rounds are one
   history in `activity_submissions`, and no client can separate them afterwards. The shipped
   course is exactly the shape where this happens: four worksheet types across two units
   (`creative-thinking.json:5-204`), any of which a teacher would plausibly re-run.

### 4.5 Where an activation gets ended

All four paths funnel through `ActivationService.end`, which is what makes a single close-sessions
call there sufficient:

- facilitator end route (`activities.py:688-694`)
- project-owner type delete (`facade.py:545-552` via `_cascade_delete`)
- admin platform-type delete (`facade.py:521-527`, same `_cascade_delete`)
- project opt-out of a platform type (`example_service.py:348-354`)

### 4.6 Frontend surfaces and gates

- The Activity tab exists for a participant only while an activation is live
  (`ChatroomView.vue:523-526`); the facilitator always has it.
- `isCreator` is room creator, admin, or a moderator on a legacy NULL-creator room
  (`useObservations.ts:63-71`).
- Guests are ordinary registered users enrolled against a room
  (`guest_service.py:1-14`), so `session.me.id` is always present and the Start button's
  `:disabled="!session.me?.id"` guard (`ActivityPanel.vue:235`) never fires for them.
- Both locales give the facilitator's start-for-room and the participant's join distinct English
  strings but the same Chinese word: `startForRoom` = 在聊天室開始, `join` = 開始
  (`zh-TW.json:11-12`; `en.json:11-12` = "Start for room" / "Start"). The empty state tells the
  participant to wait for the facilitator (`zh-TW.json:15-16`), and then presents them another
  Start.

### 4.7 The uniqueness guard

`uq_activity_sessions_open ON activity_sessions (activity_type_id, chatroom_id, subject_user_id)
WHERE status = 'open'` (`0049_activities.py:114-118`) is what makes `create_open`'s
`ON CONFLICT DO NOTHING` race-safe (`session_repo.py:77-98`).

## 5. Design

### Options considered

**Option A — Frontend only.** Mount `ActivityHost` as soon as an activation exists, pass
`sessionId: null`, delete both buttons. No migration.

Trade-off: fixes the unintuitive surface and nothing else. The round-merging defect (§4.4) and
the never-closed sessions survive, and there is no completion signal, which Q-3 rules out.

**Option B — Bind the session to the activation, keep `status` as the completion record.** Add
`activation_id`; the participant's "done" closes the session; the facilitator's end also closes
sessions.

Trade-off: one column instead of two, but the facilitator's end-of-round cascade would mark
every participant complete, and an accidental "done" would be terminal (§Q-4, Q-5). The
completion count would be a count of two different things.

**Option C — Bind the session to the activation, with a separate `completed_at`.** Chosen.

### Decision

**Option C.** `activity_sessions` gains two nullable columns:

- `activation_id` — which round this session belongs to. Nullable only because pre-migration
  rows have no round to point at; every row created after 0077 sets it.
- `completed_at` — when the subject declared themselves done, `NULL` while they have not (or
  have undone it, or have submitted since).

`status` keeps its existing meaning — whether the session can still take submissions — and is now
driven by the facilitator, not the participant.

The session key becomes `(activation_id, subject_user_id)` and is a **plain** unique constraint,
not a partial one. This is stronger than what it replaces: a subject has exactly one session per
round whatever its status, so a completed-then-resumed participant keeps one continuous
`attempt_no` sequence, and a second round is structurally a different row rather than depending
on the first having been closed. The old partial-unique is dropped, because it would forbid the
legitimate second-round row. PostgreSQL treats `NULL`s as distinct in a unique constraint, so
legacy rows (all of them closed by the migration's backfill, per Q-2) are unaffected.

What this consciously gives up: `activation_id` cannot be `NOT NULL` without inventing
activations for historical sessions, so the invariant "every session has a round" is enforced by
the writers and stated in the column comment rather than by the schema. Backfilled and legacy
rows are distinguishable exactly by `activation_id IS NULL`.

**Participant flow after this change.** Activation appears (WS or hydrate) → `ActivityHost`
mounts immediately with `sessionId: null` → first submit lazily creates the session bound to the
active activation → an "I'm done" toggle appears once a session exists → facilitator ends → the
panel's surface clears and the server has closed the session. The participant presses nothing to
begin and nothing to be counted as still working.

**Facilitator completion view.** A room-creator-gated read returns
`{completed, in_progress}` for an activation; a `activity.session.completion` event carrying the
same two numbers is emitted post-commit to the activation's starter (Q-6). Non-starter
facilitators poll on the `useObservations` precedent (`useObservations.ts:110-111`). Counts, not
identities (see Non-goals).

## 6. Detailed Changes

### 6.1 Backend — schema

**Migration `0077_activity_session_activation`** (`down_revision = "0076_platform_activity_types"`;
head verified as 0076):

1. `ALTER TABLE activity_sessions ADD COLUMN activation_id uuid NULL REFERENCES
   activity_activations(id) ON DELETE CASCADE`, `ADD COLUMN completed_at timestamptz NULL`.
2. Backfill (Q-2), in one statement: set `activation_id` from the `active` activation of the
   same `chatroom_id` + `activity_type_id` for every `status = 'open'` session that has one.
3. `UPDATE activity_sessions SET status = 'closed', closed_at = now() WHERE status = 'open' AND
   activation_id IS NULL`.
4. `DROP INDEX uq_activity_sessions_open` (`0049_activities.py:114-118`).
5. `CREATE UNIQUE INDEX uq_activity_sessions_activation_subject ON activity_sessions
   (activation_id, subject_user_id)`.
6. `CREATE INDEX ix_activity_sessions_activation ON activity_sessions (activation_id)` for the
   per-round count and close.
7. `downgrade()` reverses in order: drop the two new indexes, recreate the old partial-unique,
   drop the two columns. Recreating the partial-unique can fail if two rounds left two open
   sessions for one `(type, room, subject)` — the downgrade closes all but the newest first, and
   §10 records why that is acceptable.

`tables.py:53-78` gains both columns verbatim (the ORM/migration type-match rule,
`tables.py:1-7`).

### 6.2 Backend — domain

`domain/models.py`: `ActivitySession` gains `activation_id: uuid.UUID | None` and
`completed_at: dt.datetime | None` (both defaulted, matching how `scope` was added to
`ActivityType` at `models.py:92`, so existing construction sites keep compiling). New domain
error `ActivitySessionNotFoundForActivation` is **not** added — the existing `SessionNotFound`
and `ActivityNotActive` cover every new refusal.

### 6.3 Backend — repository (`session_repo.py`)

- `_SESSION_COLS` and `_row_to_session` gain the two columns.
- `get_open(...)` → `get_for_activation(*, activation_id, subject_user_id)`, keyed on the new
  unique constraint and returning the row **whatever its status**, so a closed round's row is
  found rather than silently duplicated.
- `create_open(...)` gains `activation_id`; the `ON CONFLICT DO NOTHING` target is now the new
  constraint (untargeted `on_conflict_do_nothing()` still covers it, `:94`).
- New `set_completed(session_id, *, completed: bool) -> bool` — guarded `UPDATE` that sets or
  clears `completed_at`, returning whether it changed anything (mirrors `close`'s idiom,
  `:110-123`).
- New `close_open_for_activation(activation_id) -> int` — the per-round counterpart of
  `close_open_for_type` (`:125-144`), bounded by one activation.
- New `count_for_activation(activation_id) -> tuple[int, int]` — one grouped query returning
  `(completed, in_progress)`: `completed` = rows with `completed_at IS NOT NULL`,
  `in_progress` = the rest. **This is PostgreSQL-executed aggregate SQL, so it needs a `db`-tier
  test** (backend/CLAUDE.md's rule).
- `close_open_for_type` / `close_open_for_type_in_rooms` stay unchanged: they answer "this type
  is going away", which the per-round close does not, and they are the only sweep that reaches
  legacy `activation_id IS NULL` rows.

### 6.4 Backend — application

- `session_service.open_session`: after `resolve_reachable_type` (`session_service.py:61-66`),
  read the active activation for the room and raise `ActivityNotActive` when there is none or
  its type differs (Q-1); pass `activation_id` down. Ordering stays type-check → activation-check
  → subject-check so a cross-tenant probe still 404s on the type first (`:57-67`).
- `session_service` gains `set_completion(*, chatroom_id, activation_id, subject_user_id,
  caller_user_id, completed, actor_*)`: resolves-or-creates the session for the round, sets or
  clears `completed_at`, and audits `activity.session_completed` /
  `activity.session_completion_cleared` in-transaction only on a real transition (mirrors
  `close_session`'s guarded audit, `:110-126`). Reuses `_ensure_subject_is_caller` (`:25-32`).
- `session_service` gains `count_for_activation` and `close_open_for_activation` pass-throughs.
- `submission_service._resolve_session` (`:358-395`): the no-`session_id` branch keys on the
  activation already held under `FOR UPDATE` (`:102-104`) instead of `(type, room, subject)`; the
  explicit-`session_id` branch additionally requires `session.activation_id ==
  activation.id`. After resolving, clear `completed_at` when set (Q-5) — one guarded `UPDATE`,
  skipped when already `NULL`.
- `activation_service.end` (`:101-132`): inside the `if await self._repo.end(...)` arm, before
  the audit, call `close_open_for_activation(activation_id)` and record the count on the
  existing `activity.activation_ended` audit metadata (`:122-126`). Because all four end paths
  route through here (§4.5), this is the whole of FU-2.

### 6.5 API contract (`gen:api` rerun required)

- `POST /api/chatrooms/{id}/activity-sessions` — unchanged shape; now 409 `ActivityNotActive`
  when no matching activation is live. `ActivitySessionOut` (`activities.py:173-181`) gains
  `activation_id` and `completed_at`.
- **New** `PATCH /api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/completion`,
  body `{completed: bool}`, gated `ensure_can_send`; subject is the caller (admins may pass
  `subject_user_id`, matching `open_activity_session`'s arm at `:736`). Returns
  `ActivitySessionOut`. Post-commit, best-effort, emits `activity.session.completion` to
  `user_channel(activation.started_by_user_id)` with
  `{chatroom_id, activation_id, completed, in_progress}` — the same `contexts.identity.interfaces`
  import `observations.py:32` uses.
- **New** `GET /api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/progress`,
  gated `ensure_room_creator`, returns `{completed: int, in_progress: int}`. The activation must
  belong to the room (404 otherwise).

### 6.6 Frontend (`slices/activities`, plus one line in `conversation`)

`components/ActivityPanel.vue`:

- Delete `activitySession`, `startSession`, `finishSession` and the two buttons
  (`:33`, `:160-188`, `:223-239`). Render `ActivityHost` directly whenever `activeType` resolves,
  with `:session-id="null"`.
- Add a completion toggle rendered under the host: `activities.panel.markDone` /
  `activities.panel.markDoneUndo`, calling the new PATCH. Local `completed` state seeded from the
  PATCH response and reset by the existing activation watcher (`:191-195`).
- Add a facilitator-only progress line (`v-if="isCreator"`): "{completed} done, {inProgress}
  working", seeded by the new GET and updated by the WS event.
- The panel subscribes to `/user/{me.id}` for `activity.session.completion`, filtered to this
  room, using `wsManager` from `@shared/transport` — the `useObservations.ts:141-187` pattern,
  reachable from this slice without importing `conversation`. Poll the GET every 30s when
  `session.me?.id !== activation.startedByUserId` (`useObservations.ts:110-111`).

`api/index.ts`: add `setActivationCompletion`, `getActivationProgress`. `openActivitySession` and
`closeActivitySession` stay exported (Q-1) but lose their call sites.

`locales/{en,zh-TW}.json`: remove `panel.join`, `panel.finish`, `panel.finishFailed`; add
`panel.markDone`, `panel.markDoneUndo`, `panel.markDoneFailed`, `panel.progress`,
`panel.progressFailed`. zh-TW keeps 在聊天室開始 for the facilitator, so no two buttons share a
label.

No change to `ChatroomView.vue`'s tab gating (`:523-526`) — a participant still sees the tab only
while a round is live.

### 6.7 Deploy/config

None.

## 7. NFR Checklist

- [x] **i18n** — five new keys in both bundles, three removed from both; every new string through
  `$t()`. The Chinese label collision in §4.6 is resolved by construction (the participant has no
  start button left).
- [x] **Audit** — `activity.session_completed` / `activity.session_completion_cleared` emitted
  in-transaction on real transitions only; `activity.activation_ended` gains a
  `sessions_closed` count. No new audit on the lazy session open (unchanged from today, where a
  lazy open is also unaudited).
- [x] **Tenant isolation** — both new endpoints go through `resolve_room_access`; the completion
  PATCH additionally resolves the type through `resolve_reachable_type` inside the service
  ([R30.33]) and the activation must belong to the room. The progress GET is
  `ensure_room_creator`, strictly stronger than the send floor.
- [x] **Error handling UX** — the panel keeps its single `errorMessage` alert region
  (`ActivityPanel.vue:203-209`); the toggle and the progress read each map `ApiError` onto their
  own key. A failed progress read must not hide the worksheet.
- [x] **Performance** — `count_for_activation` is one grouped query over
  `ix_activity_sessions_activation`, run once per completion event rather than per viewer;
  `close_open_for_activation` is one bounded `UPDATE` per end. The 30s poll applies only to
  non-starter facilitators.

## 8. Security Considerations

Touches room authZ, WebSocket, and user-input processing.

- **Who may declare completion.** The PATCH is `ensure_can_send` and the subject is forced to the
  caller by the existing `_ensure_subject_is_caller` (`session_service.py:25-32`); the admin arm
  passes `caller_user_id=None` exactly as `open_activity_session` does (`activities.py:736`). A
  room member cannot mark another participant done.
- **Who may read progress.** `ensure_room_creator` — the same gate the observer surface uses
  ([R28.03]). A participant cannot read how many peers have finished.
- **No per-subject leak on the wire.** The completion event carries two integers and the room and
  activation ids, addressed to one user channel (Q-6). Nothing on a room channel changes.
- **The activation gate is unchanged.** [R30.22]'s submit-time
  `get_active_for_update` check (`submission_service.py:102-104`) is untouched, so binding
  sessions to activations narrows *which* session a submission lands in, never *whether* it is
  accepted.
- **Cross-tenant probing.** `open_session`'s new activation check is ordered after the existing
  reachability resolution, so an unreachable type still 404s before the activation state of
  another tenant's room can be inferred.
- **No secrets, no injection** — payload validation is unchanged; the two new columns are server-
  set timestamps and a server-resolved FK, never client-supplied.

## 9. Quality Notes

**Existing debt in the touched files (record, do not silently fix):**

- `ActivityPanel.vue` holds server state in component-local refs and re-derives it on mount
  (`:33`, `:60-73`); the slice has a `queries/` layer and a store the rest of the panel already
  uses. This task deletes the worst instance (`activitySession`) but the new `completed` and
  progress state should not recreate the pattern — put them in `useActivitiesStore` alongside
  `activations` (`stores/activities.ts:16-17`) if they need to survive an unmount. Anything left
  local goes to FU-2.
- `session_service.py` exposes `_ensure_subject_is_caller` as a module-private imported across
  files (`submission_service.py:26`). Not this task's to fix; do not add a second such import.
- `facade.py:529-566` mixes cascade orchestration into the facade rather than a service. The new
  per-round close belongs in `ActivationService.end`, not here.

**Patterns to follow:**

- Migration idiom: enum/index via `op.execute`, reversed in `downgrade()` —
  `0049_activities.py:114-118`, `:189`.
- Guarded idempotent `UPDATE` returning `rowcount` — `session_repo.py:110-123`.
- Race-safe create — `session_repo.py:77-98`.
- In-transaction audit on a real transition only — `session_service.py:110-126`.
- Post-commit best-effort WS, never before commit — `activities.py:830-876`.
- Per-recipient user-channel emit — `observations.py:247`.
- Frontend user-channel subscription with teardown and a poll fallback —
  `useObservations.ts:110-111`, `:141-189`.

**Reuse inventory:**

- `resolve_reachable_type` (`application/reachability.py`) — do not re-type the tenancy check.
- `_ensure_subject_is_caller` (`session_service.py:25-32`).
- `ActivationRepository.get_active_for_update` — already held in `submit`'s transaction
  (`submission_service.py:102`); do not issue a second read.
- `rowcount` (`shared_kernel.db.rowcount`), `audit.emit`/`AuditEvent`, `Publisher`,
  `user_channel` (`contexts.identity.interfaces`).
- Frontend: `ActivityHost`, `useActivityHost`, `ActivityOutcomeBadge`, `useActivitiesStore`,
  `wsManager` (`@shared/transport`), `SButton`, `SEmptyState`, and the existing
  `usePolicyRefusal` composable.

## 10. Risks and Rollback

- **Migration is destructive to stale rows.** Step 3 closes open sessions that no live activation
  claims. Those sessions cannot receive a submission today ([R30.22]) and would otherwise be
  adopted by the next round, which is the defect being fixed — but the write is not reversible by
  the downgrade. Mitigation: the backfill runs first, so anything belonging to a running class is
  preserved; deploy outside class hours if a session is live.
- **Downgrade can conflict.** Recreating `uq_activity_sessions_open` fails if two rounds left two
  open sessions for one `(type, room, subject)`. The downgrade closes all but the most recent per
  key before recreating it. Data loss on downgrade is limited to a `status` flip; no submission
  row is touched.
- **Four callers depend on `end` not being slow.** `close_open_for_activation` adds one `UPDATE`
  inside `ActivationService.end`, which `_cascade_delete` calls in a loop over every activation of
  a type (`facade.py:545-552`). Bounded by rooms-per-type; the `UPDATE` is index-backed.
- **`get_open` rename breaks callers.** Two call sites (`session_service.py:69`, `:82`;
  `submission_service.py:378`, `:390`) plus unit tests. mypy catches all of them.
- **Rollback**: the frontend half is additive-then-subtractive — reverting `ActivityPanel.vue`
  restores the buttons and they still work against the new backend (Q-1 keeps the endpoints). The
  backend half needs the migration downgrade.

## 11. Acceptance Criteria

- [x] AC-1: With an activation live, a participant (including a guest) sees the worksheet with no
  button press; a refresh or a rail-tab switch re-renders it without one either.
  (`ActivityPanel.test.ts` — the form renders and neither retired key appears.)
- [x] AC-2: A participant's first submission creates exactly one session, bound to the active
  activation, with `attempt_no = 1`.
  (`test_activities_services.py::TestSubmitSessionResolution` + the existing attempt-numbering
  assertions in `TestSubmitInProcess`.)
- [ ] AC-3: Facilitator starts type T, participant submits twice, facilitator ends, facilitator
  starts T again in the same room, participant submits again → the third submission is in a
  **different** session with `attempt_no = 1`, and the first session holds exactly two.
  **Left unticked: the test exists but has never executed** — this claim is about rows a real
  PostgreSQL holds, and the `db` tier could not run here (D-7).
  `tests/integration/test_activity_session_activation.py::test_two_rounds_give_one_subject_two_sessions`
  is the mapped test. The unit half (submit resolves through the round) passes.
- [x] AC-4: Ending an activation closes every session bound to it, through all four end paths
  (facilitator end, project type delete, admin platform-type delete, project opt-out), and the
  `activity.activation_ended` audit event records the count.
  (`test_activity_activation_service.py::test_end_closes_the_rounds_sessions_and_records_the_count`
  proves the choke point; the four paths reaching it is a structural fact — `facade._cascade_delete`
  and `example_service.opt_out` both call `ActivationService.end` and nothing else ends an
  activation. The `db`-tier boundedness test is CI-pending, as AC-3.)
- [x] AC-5: A participant can mark themselves done and undo it; neither transition blocks a
  subsequent submission, and a submission after marking done clears the mark.
  (`TestSessionCompletion`, `TestSubmitSessionResolution::test_answering_again_retracts_a_completion_declaration`,
  `ActivityPanel.test.ts::is reversible`.)
- [x] AC-6: The facilitator sees `{completed, in_progress}` for the running activation, seeded by
  the GET on panel mount and updated without a refetch when the activation's starter is the
  viewer; a non-starter facilitator (admin) converges within one poll interval.
  (`ActivityPanel.test.ts` covers the render, the WS update with no refetch, and that a
  participant never asks. The poll branch itself is untested — FU-6.)
- [x] AC-7: A non-creator receives 403 from the progress GET; a room member cannot set another
  subject's completion (404, the `SessionNotFound` collapse of `_ensure_subject_is_caller`); a
  cross-tenant type id still 404s before any activation state is revealed.
  (`test_activity_activation_routes.py::TestProgressRoute` pins the gate and that the send floor
  is *not* it; `TestSessionCompletion` covers the subject and room refusals;
  `test_cross_project_type_rejected` asserts the activation read never happens for a foreign type.)
- [x] AC-8: `POST .../activity-sessions` with no matching active activation returns 409
  `ActivityNotActive`; with one, the returned session carries that `activation_id`.
  (`TestOpenSessionTenantIsolation` — no round, wrong round, and the admin arm.)
- [ ] AC-9: `alembic upgrade head` applies and `downgrade` reverses cleanly; the new unique
  constraint and index exist, the old partial-unique is gone, and `tables.py` matches the
  migration. An open session whose room has a matching active activation is backfilled; one
  without is closed.
  **Left unticked: unverified.** No Docker and no local PostgreSQL (D-7), so 0077 has never been
  applied anywhere. `tests/integration/test_migration_0077_index_swap.py` (schema, scratch DB) and
  `test_activity_session_activation.py::TestMigrationDataSteps` (the migration's own backfill SQL)
  are the mapped tests and are CI's to run.
- [x] AC-10: No two buttons in the Activity panel share a label in either locale; `panel.join`,
  `panel.finish`, `panel.finishFailed` are absent from both bundles and from `src/`.
  (`i18n.panel.test.ts`.)
- [x] AC-11: Gates green — backend `pytest`/`ruff`/`mypy`; frontend `pnpm lint`/`typecheck`/
  `test`/`build`; `gen:api` rerun and `check:openapi-drift` clean.
  Backend unit tier 6875 passed / 6 skipped (with D-8's exclusion), `ruff check` and
  `ruff format --check` clean, `mypy` clean over 940 files. Frontend `pnpm lint`, `typecheck`,
  `test` (183 files / 1155 tests), `build`, `check:bundle-size` and `check:type-coverage` (98.57%)
  all pass. `gen:api` rerun; `check:openapi-drift` itself cannot run on this host (its bash
  wrapper calls `python`, which is not on the MSYS PATH) so drift was verified equivalently by
  re-exporting the spec and comparing file hashes — identical.

## 12. Test Plan

- **Backend unit** (`tests/unit/`): `_resolve_session` keys on the activation and reuses a closed
  round's row (AC-2, AC-3, AC-5); `open_session` raises `ActivityNotActive` with none live
  (AC-8); `set_completion` set/clear/idempotent + audit only on transition (AC-5);
  `ActivationService.end` calls `close_open_for_activation` and records the count (AC-4);
  `_cascade_delete` and `opt_out` inherit it through `end` (AC-4); subject-mismatch collapses to
  404 (AC-7).
- **Backend db tier** (`tests/integration/`, `pytest.mark.db`): `count_for_activation` executes
  and returns the right split (backend/CLAUDE.md requires a real execution for aggregate SQL);
  the unique constraint permits two rounds' rows for one subject and rejects two for one round
  (AC-3, AC-9); migration upgrade/downgrade round-trip including the backfill and the
  stale-close (AC-9).
- **Backend routes**: 403 on the progress GET for a non-creator, 409 on the session open with no
  activation, 404 on an activation from another room (AC-7, AC-8).
- **Frontend component** (`__tests__/ActivityPanel.test.ts`): host mounts with no button press
  (AC-1) — this replaces the two existing assertions on `activities.panel.join` at
  `ActivityPanel.test.ts:142` and `:167`; toggle calls the PATCH and flips its label (AC-5);
  progress line renders for `isCreator` only and updates from a simulated WS event (AC-6).
- **i18n**: a bundle-parity assertion that the three removed keys appear in neither bundle nor
  anywhere under `src/` (AC-10).
- **Manual** (`frontend:verify`, two browser sessions): facilitator starts, participant answers
  with no button press, marks done, facilitator's count moves, facilitator ends, participant's
  surface clears (AC-1, AC-5, AC-6). Recent activities dossiers have repeatedly shipped without
  this step because Docker was unavailable (`BOARD.md:213-219`, `:250-252`); if it is skipped
  again, say so explicitly rather than leaving it implied.

## 13. SRS Delta

Amend `REQUIREMENTS.md:2160` **[R30.01]**, replacing the final clause:

```
- **[R30.01]** The `activities` bounded context stores typed interaction events alongside free-text chat. An `ActivityType` registers a payload JSON Schema and a validator configuration; an `ActivitySubmission` is the authoritative record of one participant submission; an `ActivitySession` groups one subject's submissions within one `ActivityActivation` and carries a server-assigned monotonic attempt number. A subject has at most one session per activation, so re-running the same activity type in the same room produces a separate session with its own attempt sequence.
```

Amend `REQUIREMENTS.md:2181` **[R30.22]**, replacing the final sentence:

```
- **[R30.22]** A submission is accepted only while an active activation for that exact activity type exists in the room; otherwise the platform rejects it. This is enforced server-side and holds regardless of the client, so a facilitator ending an activity stops further submissions and out-of-window data cannot enter the authoritative record. Participants do not open or close their own sessions: a participant's session is created by their first submission, bound to the activation in force, and closed by the platform when the facilitator ends that activation. A participant may separately declare themselves finished and undo it; the declaration is reversible, never blocks a further submission, and is cleared by one. The facilitator may read the number of finished and still-working participants for the running activation; that read is gated by room-creator capability and reports counts, never identities.
```

## 14. Open Questions

- **OQ-1**: `close_open_for_type` and `close_open_for_type_in_rooms` become near-redundant once
  every end path closes its round's sessions — their only remaining reach is legacy
  `activation_id IS NULL` rows. Whether to retire them after a release in which no such row can
  be created is a later call, not this task's.

## 15. Deviation Log

- **D-1 (§6.1 step 6, dropped): no separate `ix_activity_sessions_activation`.**
  The spec listed a single-column index alongside the new unique. A btree on
  `(activation_id, subject_user_id)` is already usable for a `WHERE activation_id = ?` scan, so
  the per-round count and close are served by the unique itself; a second index would be pure
  write cost. Noted in the migration next to the `CREATE UNIQUE INDEX`.

- **D-2 (§6.1, structure): the two data statements are module-level constants.**
  `BACKFILL_ACTIVATION_SQL` and `CLOSE_UNCLAIMED_SQL` are named at module level rather than
  inlined into `upgrade()`, so `TestMigrationDataSteps` executes *the migration's own SQL*
  against a real PostgreSQL without running DDL against the shared `db`-tier database. The
  shape 0076 uses for `assert_no_platform_types`. A hand-copied duplicate in the test would
  have proved nothing about what the migration does.

- **D-3 (addition, and a defect in the approved spec): a completion GET.**
  §6.6 said the participant's toggle state would be "seeded from the PATCH response". That is
  only true until the first reload: the client holds no session id, so a participant who had
  already declared themselves finished would come back to a toggle reading "not done", and
  every rail-tab remount would do the same. `GET .../activity-activations/{id}/completion`
  (gated `ensure_can_read`, subject forced to the caller, creates nothing) closes it, with
  `ActivitiesFacade.get_session_for_round` and `ActivitySessionService.get_for_round` behind
  it. Unlike `set_completion` it does not require the round to still be running — reading back
  what you declared during a round the facilitator just ended is harmless, and refusing it
  would blank the surface at the moment it is being torn down anyway.

- **D-4 (§6.4, shape): `set_completion` returns a result object, not a tuple.**
  `ActivitySessionCompletionResult{session, activation, transitioned}` (`domain/models.py`),
  mirroring `ActivityActivationEndResult`. The route needs the round to address its post-commit
  broadcast at the facilitator who started it; returning it here avoids a second lookup after
  the commit, on a path where a re-read would also be racing the very state it just wrote.

- **D-5 (quality gate, fixed in-build): `ActivationService` takes an `ActivitySessionCloser`.**
  The first cut had it instantiate the concrete `ActivitySessionRepository` with no seam, which
  is a step back from the injection its three other collaborators already use. It now depends on
  a one-method Protocol in `application/ports.py` — deliberately one method, because ending a
  round has no business reading or opening sessions and a wider contract would let it grow that
  way unnoticed. The unit tests inject through the constructor rather than patching a private
  attribute.

- **D-6 (quality gate, fixed in-build): the progress wiring is a composable.**
  §6.6 put the WebSocket subscription, its teardown and the poll fallback inside
  `ActivityPanel.vue`, on top of the rendering and the two other fetches it already owned. They
  moved to `composables/useActivationProgress.ts`; the panel reads one ref. This also replaced a
  second generation counter with a guard on the activation id itself, which is the real
  identity.

- **D-7 (verification gap): no Docker on the implementing host, so three tiers never ran.**
  `integration`, `db` and `wiring` fail at connect; `alembic upgrade head` was never executed,
  so **migration 0077 has never been applied anywhere**; and no behavioural check was performed
  in a browser, so the entire participant-facing change — the worksheet mounting with no button,
  the done toggle, the facilitator's counts — has been reasoned to work and not seen to.
  AC-3 and AC-9 are unticked for this reason and are CI's to close. This is the sixth
  consecutive dossier in this area to record the same gap (`BOARD.md`, the removal notes from
  2026-08-16); it is now the normal state of this host, not an incident.

- **D-8 (verification gap): `pytest -q` was not run to completion.**
  `tests/unit/test_graphrag_builder.py` hangs indefinitely on this host, in isolation as well as
  in the tier — a pre-existing, already-recorded defect (D-7 of
  `2026-08-16-platform-type-delete-optin-lifecycle`) unrelated to this diff. The unit tier ran
  with that one file excluded: 6875 passed, 6 skipped.

- **D-9 (self-inflicted, corrected): a UTF-8 BOM shipped in a test file.**
  `Set-Content -Encoding utf8` on this Windows host writes a BOM and `core.autocrlf` does not
  normalise it, so `test_activity_activation_routes.py` was committed with one and `ruff format`
  stripped it on the next run. Fixed in its own commit. Same trap the openapi.json regeneration
  carries (`BOARD.md`, D-10 of the migration-0076 dossier); the working defence is to write
  files through the editor rather than through PowerShell redirection.

- **D-10 (review fix): a submission now republishes the facilitator's counts.**
  `submit` clears `completed_at` (that is D-4's Q-5 behaviour) but the submit route published
  nothing, and the starter — the only viewer the completion event targets — has no poll. So a
  participant who declared themselves finished and then kept working left the facilitator's panel
  reading "1 done" for the rest of the round, which is precisely the number a teacher decides to
  move on from. The same hole swallowed the *first* submission of every round, which moves
  `in_progress` 0 → 1 and was equally silent. `_dispatch_room_activation_progress` now runs at the
  end of `_dispatch_submission`, unconditionally: the route cannot know whether the counts moved
  without asking, and asking is the read. `_dispatch_activation_progress`'s docstring claimed a
  dropped event "self-heals"; it does not for the starter, and now says so.

- **D-11 (review fix): the done toggle follows the server after a submission.**
  Mirror of D-10 on the client. `completed` was set only by the toggle and the round read, so
  after answering again the button still read "Keep working" while the server considered the
  participant unfinished — and the next click sent `completed: false`, a no-op, so re-declaring
  cost two clicks with the wrong label in between. `ActivityHost` now emits `submitted` from the
  single submit both its paths share (only on a resolved call — a refused submission changed
  nothing), and the panel clears the toggle on it.

- **D-12 (review fix, §6.1 step 4 reversed): 0077 no longer drops
  `uq_activity_sessions_open`.**
  The spec had the migration drop it, which breaks the forward-compatibility rule this repo holds
  migrations to (backend/CLAUDE.md: old code runs on new schema). Pre-0077 `create_open` relies on
  that index for its `ON CONFLICT DO NOTHING`; without it the old code inserts
  `activation_id = NULL`, NULLs are distinct under the new unique, and two concurrent first
  submissions in the window between `alembic upgrade` and the app restart produce two open
  sessions for one subject — the split this migration exists to prevent, caused by it. Keeping the
  index costs nothing because the new design already satisfies it (ending a round closes its
  sessions, so a subject never holds two *open* sessions for one type+room even across rounds).
  Dropping it is the contract half of an expand/contract pair: **FU-7**, and it must not ship in
  the same release. The downgrade got simpler as a result — nothing to restore, nothing to
  de-duplicate — and the schema test now asserts the old index *survives*, so a later tidy-up has
  to argue with a test rather than only with a comment.

- **D-13 (review fix): a route test that proved nothing now proves it.**
  `test_a_member_may_only_declare_for_themselves` claimed to cover "a body naming another
  subject" while passing `subject_user_id=None`, which the `or principal.user_id` fallback
  satisfies by itself — so AC-7's route half was uncovered. It now passes a foreign uuid and
  asserts both values reach the service; the no-subject case became its own test.

## 16. Follow-ups

- **FU-1**: A per-subject completion roster for the facilitator (who has finished, not just how
  many). A different privacy decision from the counts this task ships; see Non-goals.
- **FU-2**: Move `ActivityPanel`'s remaining component-local server state into
  `useActivitiesStore`/`queries` so the panel survives an unmount without a refetch (§9).
  D-6 took the largest piece; `completed`, `types` and `fetchedType` are what is left.
- **FU-3**: A roster-based denominator ("12 of 18 students") once a room-membership read is
  available to the activities context without breaking [R30.09].
- **FU-4**: The get-or-create-with-race-retry block is duplicated between
  `session_service._resolve_for_activation` and `submission_service._resolve_session` (~18 lines
  each, and it was duplicated before this task too). Deduping means one service importing
  another's internals, which is exactly the cross-service private import §9 says not to add a
  second of — so the fix is to give the pair a shared home, not to import across them.
- **FU-5**: Retire `close_open_for_type` / `close_open_for_type_in_rooms` once no session can
  carry a NULL `activation_id`. Every end path now closes its round's sessions, so their only
  remaining reach is pre-0077 rows. This is §14's OQ-1, restated as work.
- **FU-6**: `useActivationProgress`'s poll branch (the non-starter facilitator) has no test —
  the WS branch and the seed do. A fake-timer test would close AC-6's last uncovered path.
- **FU-7**: Drop `uq_activity_sessions_open` in a later migration, once 0077's code is deployed
  (D-12). It is redundant under the round-scoped unique and only still exists so pre-0077 code
  survives the upgrade window. **Must not ship in the same release as 0077** — that is the whole
  point of splitting it out.
