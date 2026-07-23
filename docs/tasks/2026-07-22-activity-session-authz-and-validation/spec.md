---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: [R30.01, R30.06, R30.11, R30.12, R30.18, R30.22]
depends_on: []
---

# Activity sessions: subject authority, watchdog notification, and optional-array assembly

`depends_on: []` is a positive claim, checked against every open row in `docs/tasks/BOARD.md`:

- **No file overlap with any open dossier.** This dossier touches
  `backend/contexts/activities/**`, `backend/app/api/v1/activities.py`,
  `backend/app/workers/tasks/activities.py`,
  `backend/contexts/conversation/interfaces/access.py` (one re-export line, conditionally), and
  `frontend/src/slices/activities/components/schemaFields.ts`. No other open dossier names
  any of these.
- **Nearest textual adjacency, cleared.** `2026-07-22-reconnect-reconciliation`
  edits `useChatroomSocket.ts`, which carries the `activity.created` /
  `activity.validated` cases at `frontend/src/slices/conversation/composables/useChatroomSocket.ts:295-311`.
  A case-insensitive grep for `activit` across that dossier's `spec.md`
  returns **no matches**, and this dossier changes no frontend socket code (see §7), so the
  two diffs cannot collide.
- **Nearest conceptual adjacency, cleared.** `2026-07-22-retention-sweep-fixes`
  is the other "a sweep whose write-back notifies nobody" dossier, but
  it operates on `backend/contexts/conversation/application/retention_service.py` and
  `t.messages` — disjoint files, disjoint tables. §6 records the shared shape without
  creating a dependency.
- **One board row appears to claim F-12; it does not.** The
  `2026-07-22-wakeup-trigger-state-and-bounds` row reads "a2u F-3, F-12, F-14, F-21, F-38". The
  a2u audit has only F-1..F-22 and no F-38, while the a2a audit's F-3, F-12, F-14, F-21 and F-38
  (`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md:136,389,438,594,959`)
  match that row's description — silence triggers, designer soft bounds, `refresh_every_hours`
  — verbatim. The row is mislabeled `a2u` where it means `a2a`. Not a dependency; recorded as
  FU-1 so the board gets corrected rather than re-litigated.

Source: `docs/audits/2026-07-22-agent-to-user-conversation/findings.md` **F-12** (`:360-382`)
and **F-20** (`:529-549`), plus `docs/audits/2026-07-22-conversation-verification-gap/findings.md`
**V-7** (`:291-344`), routed here by the hand-off table at
`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:678`.

**Confidence note the grouping requires.** F-12 and F-20 are `confirmed`; **V-7 is
`plausible`** (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:293-294`).
V-7's mechanism is fully traced and reproduced below, but its *trigger* — an operator-authored
`payload_schema` declaring an optional `enum` array with `minItems` — could not be traced to
any configuration that exists today. Its acceptance criteria stand on the module's own stated
contract, not on an observed production failure.

## 1. Summary

Three defects in the `activities` bounded context.

**F-12 — any room member can close another participant's activity session.**
`ActivitySessionService.close_session` (`backend/contexts/activities/application/session_service.py:64-68`)
validates that the session exists and belongs to the addressed room, and nothing else. The
route gates on `resolve_room_access` + `ensure_can_send`
(`backend/app/api/v1/activities.py:343-344`), i.e. anyone who may post. The victim's
`session_id` is directly queryable: `GET /activity-submissions` accepts a `subject_user_id`
filter (`activities.py:392`, applied at
`backend/contexts/activities/infrastructure/repositories/submission_repo.py:299-302`) and
returns `session_id` on every row (`ActivitySubmissionOut.session_id`, `activities.py:118`,
populated at `:189`).

**F-20 — the stalled-validation watchdog notifies nobody.** `activities_watchdog`
(`backend/app/workers/tasks/activities.py:164-187`) writes the terminal `error` verdict and
emits neither the `activity.validated` room event nor the `workflow_signal("activity", …)`
completion signal that the normal validation path emits ten lines above it (`:155-160`). It
structurally cannot: `sweep_stalled` is a set-based UPDATE returning a bare rowcount
(`submission_repo.py:210-242`), so no swept id is ever in hand.

**V-7 — `SchemaForm` asserts an empty array for an untouched optional multi-select.**
`assemblePayload` documents "Empty optional values are omitted"
(`frontend/src/slices/activities/components/schemaFields.ts:94-95`) and honours it in four of
its six branches; `:117` (`case 'enum-array'`) assigns unconditionally.

**These do not share a root cause. They are grouped by change surface — specifically, by
bounded context.** §5 names three distinct earliest-correctable links: a service-layer
identity check that was never written (F-12), a repository return shape that forecloses
per-row notification (F-20), and one switch branch that breaks its own module's documented
contract (V-7). No fix in this dossier is a precondition for another, and any one could ship
alone.

The grouping is still the right unit of work for three reasons, none of them "they feel
related": all three live under `contexts/activities/` + `slices/activities/` and would be
reverted together; F-12 and F-20 land in the same two test files
(`backend/tests/unit/test_activities_services.py`,
`backend/tests/unit/test_activities_validation_worker.py`); and F-12's fix changes the
`ActivitiesFacade` session signatures (`backend/contexts/activities/interfaces/facade.py:169-173`)
while F-20's changes the same facade's `sweep_stalled` (`:227-228`), so building them
concurrently in separate branches would conflict in one file for no benefit.

## 2. Observed vs Expected

### F-12

- **Observed.** `close_session` takes `session_id` and `chatroom_id` and no caller identity at
  all (`session_service.py:64`). Its guard is `session is None or session.chatroom_id != chatroom_id`
  (`:66`); a repo-wide read of the file finds no reference to `subject_user_id` on any code
  path. `ActivitiesFacade.close_session` (`facade.py:169-170`) is a pass-through with the
  same two parameters, and the route (`activities.py:336-351`) resolves the principal only to
  satisfy `ensure_can_send` — `principal.user_id` is never forwarded. The repository close
  (`backend/contexts/activities/infrastructure/repositories/session_repo.py:109-122`) guards
  on `status='open'` only.
- **Expected.** [R30.01] (`REQUIREMENTS.md:2112`) defines an `ActivitySession` as grouping
  **a subject's** submissions with a server-assigned monotonic attempt number. [R30.22]
  (`:2133`) states the participant lifecycle as "Participants join an active activity, open
  **their own** per-subject session (explicit start), submit, and finish (close)" — the close
  is named as an act on one's own session. The design record agrees: Q-3 of
  `docs/tasks/2026-07-13-activities-activation-ux/spec.md:68` reads "Participant explicitly
  opens (start) and closes (finish) **their** per-subject session".

### F-20

- **Observed.** Two paths write the same terminal transition and only one notifies.
  Completion: `validate_activity_submission` commits, builds the signal at
  `app/workers/tasks/activities.py:155-156`, then calls `_emit_validated` (`:158-159`) and
  `_emit_activity_signal` (`:160`). Watchdog: `activities_watchdog` (`:164-187`) calls
  `sweep_stalled` (`:172`) and `audit.emit("activity.watchdog_swept")` (`:174-180`) and
  stops — neither `_emit_validated` (`:86`) nor `_emit_activity_signal` (`:98`) appears
  anywhere on that path.
- **Expected.** [R30.06] (`REQUIREMENTS.md:2117`) makes the watchdog sweep a first-class way
  a submission reaches a terminal state, not a lesser one. [R30.12] (`:2123`) requires a
  best-effort `activity` signal "at submission **and at validation completion**", carrying
  the final `error_class`. The code states the equivalence itself: the TTL constant's comment
  calls the watchdog "the single safety net for a stalled worker OR a dropped post-commit
  enqueue" (`activities.py:30-32`) — i.e. it exists precisely to substitute for the path that
  does emit.

### V-7

- **Observed.** `schemaFields.ts:117` is `case 'enum-array': payload[f.name] = Array.isArray(v) ? v : []`,
  unconditional. `initialValues` seeds `enum-array → []` (`:76-78`), so an untouched control
  is *guaranteed* to reach assembly in the state that emits the key. The server then rejects:
  `payload_errors` runs full Draft 2020-12 validation
  (`backend/contexts/activities/application/validators/schema.py:27-30`), and
  `submission_service.py:89-91` raises `SubmissionPayloadInvalid`, mapped to 422 at
  `backend/contexts/activities/interfaces/error_mapping.py:59-63`.
- **Expected.** The module's own docstring: "Empty optional values are omitted"
  (`schemaFields.ts:94-95`). The string branch states the reasoning in full at `:120-122` —
  omit an empty optional string "so a `minLength`/`pattern`/`format` constraint on an
  optional field is not tripped by a blank submission". `minItems` is to an optional array
  what `minLength` is to an optional string. [R30.18] (`REQUIREMENTS.md:2129`) requires the
  generic form to degrade unsupported constructs rather than drop data; emitting a value the
  participant never entered is the mirror-image failure.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What error should a subject mismatch on `close_session` produce — a new 403, or the existing 404? | Fold it into the existing `SessionNotFound` branch (404). | `contexts/activities/domain/errors.py:10-75` defines no forbidden-shaped error, so a 403 means a new class, a new `_MAP` row (`error_mapping.py:13-64`), and a new RFC 7807 slug in the public contract — scope for no gain. The existing guard already collapses "does not exist" and "wrong room" into 404 (`session_service.py:66`); "not your subject" is the same class of answer. 404 also declines to confirm the session exists to a non-subject, which is strictly the more conservative posture even though §6 records that the identifier is separately discoverable. |
| Q-2 | Fix only `close_session` (F-12 as filed), or also the arbitrary-`subject_user_id` accepted by `open_session` and `submit`? | Fix all three. | They are the same root cause (§5), and fixing only close leaves the symptom fully reachable: an attacker who can submit as subject `A` corrupts `A`'s attempt history without closing anything. The gap is already owned as FU-3 of `docs/tasks/2026-07-13-activities-activation-ux/spec.md:370-372`, which prescribes exactly this remedy — "enforce `subject == caller` unless a facilitator capability explicitly allows proxying". That dossier is `implemented`; its follow-up has no other home, and a `check-security` pass over the corrected code would re-file it on day one. Folding it in is the honest scope. |
| Q-3 | Should the room facilitator (creator) be able to close a participant's session? | No. Subject only, plus the platform-admin bypass every other activities route already honours. | [R30.22] (`REQUIREMENTS.md:2133`) is explicit that "ending the room activation does **not** force-close open participant sessions", and `activities-activation-ux/spec.md:49-51` records force-closing as a named non-goal with FU-2 (`:368-369`) holding the deferral. Granting the facilitator a close would implement, sideways, the exact capability the design declined. The admin arm is kept because `activities.py:312,325,344,368` all pass `is_admin=principal.is_admin`; dropping it here alone would be a silent inconsistency. |
| Q-4 | `close_activity_session` currently emits no audit event at all (`activities.py:336-351`). Add one? | Yes — `activity.session_closed`, in the same transaction, mirroring `submission_service.py:178-195`. | This is the only scope addition beyond the three defects, and it is justified by F-12 itself: §7's data-repair position is "none", and the reason it must be "none" is that no record exists of *who* closed any session. Shipping an authorization fix whose effect cannot be observed in production, on the surface whose absence of a trail makes the damage unrepairable, is the wrong trade. [R30.11] (`REQUIREMENTS.md:2122`) names registration, submission and validation only, so this is additive to the SRS rather than mandated by it — see §11. Decline it and F-12's fix is still correct, merely unmonitorable. |
| Q-5 | Should `sweep_stalled` also emit a per-row `activity.validated` audit event, matching `record_validation_error` (`submission_service.py:247-255`)? | No. Keep the single aggregate `activity.watchdog_swept` row (`activities.py:174-180`). | The sweep is bounded at 500 rows per call (`submission_repo.py:211`) and runs every minute (`backend/app/workers/main.py:332`); per-row audit would be up to 500 audit inserts per tick for a state the aggregate already records, with no consumer named anywhere. [R30.11]'s "validation emits audit events" is satisfied by the existing aggregate. This is a deliberate asymmetry, recorded so it is not read as an oversight. |
| Q-6 | F-20 leaves the badge stale for a participant whose socket was disconnected when the sweep ran. Add a frontend refetch? | No — out of scope, deliberately. See §7. | That is the missed-frame class the hand-off table already routes elsewhere: `docs/audits/2026-07-22-agent-to-user-conversation/findings.md:693-697` names F-11, V-2 and the config audit's F-13 as one cause, with the generic remedy (replay or cursor semantics on the pub/sub layer) recorded as FU-1 of `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/`. Building an activities-only refetch here would be a fourth private workaround for a problem with an owner. Recorded as FU-2. |
| Q-7 | `frontend/src/slices/activities/__tests__/schemaForm.test.ts:82` asserts `expect(payload.tags).toEqual([])` — the exact behaviour V-7 calls a defect. Is that a deliberate pin? | Treat it as an unexamined assertion and change it. | The test's own title is `'omits empty optional values'` (`:79`) — it asserts the opposite of what it is named. Its sibling assertion one line up (`:81`, `expect('color' in payload).toBe(false)`) is the correct shape for the same case. And `docs/tasks/2026-07-13-activities-plugin-sdk` prescribes no array-presence behaviour. A deliberate pin would have a comment; this one contradicts its own describe-string. |

## 4. Reproduction

### F-12 — closing another participant's session

Preconditions: project `P` with activity type `T` registered (`POST /api/projects/{P}/activity-types`,
`activities.py:218`); chatroom `R` under `P` whose creator has started an activation for `T`
(`activities.py:259-279`, required because `submit` rejects without one —
`submission_service.py:85-87`); participants `A` and `B`, both ordinary members satisfying
`ensure_can_send`.

1. As `A`, submit once. `_resolve_session` (`submission_service.py:322-333`) lazily opens
   session `S_A` for `(T, R, A)`. `attempt_no` is 1 (`submission_repo.py:76-88`).
2. As `B`, call `GET /api/chatrooms/{R}/activity-submissions?subject_user_id={A}`
   (`activities.py:388-412`). `ensure_can_read` passes for any member. The response carries
   `A`'s submission with `session_id = S_A` (`activities.py:118`, set at `:189`); the
   `subject_user_id` filter is applied at `submission_repo.py:299-302`, so the read is
   targeted, not a haystack.
3. As `B`, call `PATCH /api/chatrooms/{R}/activity-sessions/{S_A}/close`.
   **Observed: 200.** `ensure_can_send` passes for `B` (`activities.py:344`);
   `close_session` finds `S_A` and confirms `S_A.chatroom_id == R` (`session_service.py:66`);
   `session_repo.close` flips it (`session_repo.py:109-122`).
4. As `A`, submit again. `_resolve_session` finds no open session
   (`session_repo.get_open` filters `status='open'`, `session_repo.py:69`) and opens `S_A2`
   (`submission_service.py:327-333`). `next_attempt_no` is scoped to `session_id` alone
   (`submission_repo.py:76-88`), so `A` **restarts at attempt 1**.

Deterministic; no timing or concurrency component. Aftermath, each cited: `A`'s attempt
history is split across two sessions with duplicate `attempt_no` values, violating [R30.01]'s
monotonic-per-subject guarantee; every per-session aggregate is now computed over a partial
window (`submission_repo.py:318-319` filters `session_id`); and `rolling.same_error_count` in
the reactive-rules signal is scoped to `session_id` too
(`submission_repo.py:128-147`, consumed at `submission_service.py:291-300`), so an impasse
rule watching `A` silently resets.

**A guest can do this.** `ensure_can_send` (`backend/contexts/conversation/application/access.py:166-175`)
evaluates room flags, and a guest-link enrollee satisfies them — `is_room_creator` needs an
explicit guest exclusion at `access.py:153-154` precisely because `ensure_can_read`/`ensure_can_send`
do not exclude guests.

### F-12b — submitting as another subject (Q-2 scope)

As `B`, `POST /api/chatrooms/{R}/activity-submissions` with `subject_user_id = A`.
`activities.py:374` resolves `body.subject_user_id or principal.user_id`, so the submission is
recorded against `A`'s session with `producer_user_id = B` (`:373`). No comparison between the
two exists anywhere on the path (`submission_service.py:62-75` receives them as independent
parameters). The same holds for `POST /activity-sessions` (`activities.py:330`).

### F-20 — a swept submission notified to nobody

Preconditions: type `T` registered with `validator_kind = webhook`
(`ValidatorKind.WEBHOOK`, dispatched at `app/workers/tasks/activities.py:67-79`); an active
activation; a participant with the room open.

1. Submit. `validation_status` is `pending` (`submission_service.py:144-150`); the route
   emits `activity.created` and enqueues `validate_activity_submission`
   (`activities.py:421-435`). The badge renders the pending clock
   (`frontend/src/slices/activities/components/ActivityOutcomeBadge.vue:25-26`), fed via
   `useChatroomSocket.ts:295-305` → `activitiesStore.applyCreated`
   (`frontend/src/slices/activities/stores/activities.ts:38-51`).
2. Make the validator hang past 900 s (`_PENDING_TTL_SECONDS`, `activities.py:32`) — an
   unreachable webhook host, or a lost enqueue (`activities.py:433-435` swallows the failure).
3. On the next minute tick (`app/workers/main.py:332`), `activities_watchdog` runs.
   `sweep_stalled` sets `validation_status='error'`, `error_class='validation_timeout'`
   (`submission_service.py:258-262` → `submission_repo.py:210-242`).

**Observed:** the database is correct and nothing else moves.
- No `activity.validated` frame is published, so `useChatroomSocket.ts:306-311` never fires,
  `applyValidated` (`stores/activities.ts:55-68`) never runs, and the badge stays on the clock
  icon. Nothing else can correct it: a grep for `useQuery|refetchInterval|invalidateQueries`
  across `frontend/src/slices/activities/` returns **zero matches**, and `activityKeys`
  (`frontend/src/slices/activities/queries/index.ts:6-11`) has no consumer — the only hits are
  its definition and its re-export at `index.ts:34`. The store is plain in-memory reactive
  state (`stores/activities.ts:15`).
- No `workflow_signal("activity", …)` is enqueued, so `workflow_signals.py:192-203` never
  evaluates `_activity_pred`. That predicate matches on `validation_status` (`:195,202`), so
  an [R30.13] `activity_event` trigger authored to fire on the error verdict never fires.

### V-7 — an untouched optional multi-select blocking a submission

Preconditions: an activity type whose `payload_schema` declares an optional `enum` array with
a non-empty constraint, e.g. `{"type":"object","required":["answer"],"properties":{"answer":{"type":"string"},"tags":{"type":"array","items":{"enum":["x","y"]},"minItems":1}}}`.
Registration accepts it — `validate_schema_wellformed` only calls
`Draft202012Validator.check_schema` (`schema.py:18-24`).

1. As a participant, open the Activity rail. `getActivityPlugin` misses (the registry ships
   empty — `frontend/src/slices/activities/plugins/registry.ts`), so `ActivityHost.vue:83-88`
   renders `SchemaForm`.
2. Fill `answer`. Tick no tag. Submit.
3. `SchemaForm.vue:72` calls `assemblePayload`; `:117` emits `tags: []`; `:80` emits it.
   Client Zod does not catch it — `zodForField` applies `.min(1)` only when the field is
   `required` (`schemaFields.ts:165`) and `jsonSchemaToZod` never reads `minItems`.
4. The POST 422s. `ActivityHost.vue:90-96` renders `errorMessage`, which
   `useActivityHost.ts:63-64` sets to `err.message` — the raw jsonschema string joined at
   `submission_service.py:91`, e.g. `[] is too short`, naming no field.

**Reachability caveat, restated from the source audit** (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:320-326`):
no such `payload_schema` ships. There is no seeded activity type under `backend/smap/`, no
frontend surface registers one, and the column defaults to `'{}'::jsonb`
(`backend/contexts/activities/infrastructure/tables.py:33`), which accepts anything. Step 0 of
this reproduction is an operator action, and that is why V-7 is `plausible`.

## 5. Root Cause Analysis

One root cause per finding — the earliest link whose correction prevents the symptom.

### F-12

1. **Trigger.** `B` issues `PATCH .../activity-sessions/{S_A}/close`.
2. `resolve_room_access` + `ensure_can_send` (`activities.py:343-344`) pass. **Correct
   behavior, not the cause** — `B` genuinely is a room member who may act in this room; this
   gate answers "may you touch this room", and it answers it right.
3. The route calls `facade.close_session(session_id=…, chatroom_id=…)` (`activities.py:346`).
   `principal.user_id` is in scope (`:340`) and is not forwarded. Consequential, but a
   symptom of 4: the route cannot forward an identity the service has no parameter for.
4. **Root cause.** `ActivitySessionService.close_session` (`session_service.py:64-68`) treats
   room containment as the whole of the resource's authorization. `activity_sessions` is a
   **per-subject** resource — the partial-unique is on `(activity_type_id, chatroom_id,
   subject_user_id)` (`session_repo.py:57-61`) — and the service verifies two of those three
   coordinates. This is the earliest correctable link: fix it and steps 1-3 proceed exactly as
   they do today while the close is refused. Every link downstream (`facade.py:169-170`,
   `session_repo.py:109-122`) merely executes the decision this line already made.
5. **Symptom.** `S_A` closes; `A`'s next submit lazily opens `S_A2`
   (`submission_service.py:327-333`); `next_attempt_no` restarts at 1
   (`submission_repo.py:76-88`).

**The same cause, two routes over.** `open_activity_session` (`activities.py:330`) and
`submit_activity` (`:374`) resolve `body.subject_user_id or principal.user_id` and never
compare the two. This is not a second defect that happens to look similar — it is the
identical proposition ("membership in the room is sufficient authority over a per-subject
resource in it") expressed at the route layer instead of the service layer. Q-2 folds it in on
that basis.

**Aggravating factors, not causes.**
- *The identifier is queryable, and targetably so.* `list_activity_submissions` exposes
  `session_id` (`activities.py:118,189`) and accepts a `subject_user_id` filter (`:392` →
  `submission_repo.py:299-302`), so `B` need not guess. This converts an attack requiring a
  known UUID into a two-request attack. It does not cause the defect: closing `S_A` would be
  equally permitted if `B` learned the id any other way. (Note the DTO carries **no**
  `subject_user_id` and **no** `payload` — `activities.py:116-127` — so the read itself
  discloses metadata, not answers. §6 clears it on that basis.)
- *Nothing records the close.* `close_activity_session` (`activities.py:336-351`) emits no
  audit event, unlike `submit` (`submission_service.py:178-195`) and both activation
  transitions (`activities.py:269-276,292-298`). This makes the damage unattributable and is
  the operative reason §7's data-repair position must be "none". It aggravates; the close
  would be equally wrong if fully audited.
- *Nothing reopens a session.* `ActivitySessionRepository` has `create_open` and `close`
  (`session_repo.py:76-97,109-122`) and no reopen, so the state change is one-way. Bounds the
  repair options; does not cause the defect.

**Provenance: original, not drift.** `session_service.py` has carried this guard since the
context was introduced, and the design record it was written against says the opposite —
activation-ux Q-3 (`docs/tasks/2026-07-13-activities-activation-ux/spec.md:68`) says the
participant closes "their" session, and [R30.22] (`REQUIREMENTS.md:2133`) says "their own".
The gap was *known*: `activities-activation-ux/spec.md:240-243` flags the submit arm as a
"pre-existing subject-spoofing gap … flagged, not fixed here" and records FU-3 (`:370-372`).
The close arm was never flagged, and F-12 is its discovery.

### F-20

1. **Trigger.** An mcp/webhook validation stays `pending` past 900 s (`activities.py:32`).
2. The cron fires `activities_watchdog` (`app/workers/main.py:332`).
3. `facade.sweep_stalled` → `SubmissionService.sweep_stalled` (`submission_service.py:258-262`)
   → the repository.
4. **Root cause.** `ActivitySubmissionRepository.sweep_stalled` (`submission_repo.py:210-242`)
   performs the terminal transition as a set-based `UPDATE ... WHERE id IN (batch)` and
   returns `rowcount(result) or 0` (`:242`). The rows that transitioned are never
   materialised. This is the earliest correctable link because it is the *only* one at which
   correction is possible: no change at the worker can emit a per-room event when the worker
   holds a count and not an id. It is also the link that must change for the workflow-signal
   half, which no frontend change could address at all.
5. `activities_watchdog` (`activities.py:171-187`) therefore calls only `audit.emit` and
   returns a string. The omission of `_emit_validated` / `_emit_activity_signal` here is the
   **proximate** omission and is often mistaken for the cause; it is not correctable in place.
6. **Symptom A.** No `activity.validated` on the room channel → `useChatroomSocket.ts:306-311`
   never fires → the badge holds `ActivityOutcomeBadge.vue:25-26`'s pending clock.
   **Symptom B.** No `workflow_signal("activity", …)` → `workflow_signals.py:192-203` never
   runs → an [R30.13] `activity_event` trigger keyed on the error verdict never fires
   ([R30.12] deviation).

**The equivalence is asserted by the code itself.** `activities.py:30-32` calls the watchdog
"the single safety net for a stalled worker OR a dropped post-commit enqueue". A safety net
for a dropped enqueue that does not itself enqueue is the internal inconsistency, stated in
the module's own comment.

**Aggravating factors, not causes.**
- *The activities slice has no query layer.* No `useQuery`, `refetchInterval` or
  `invalidateQueries` exists anywhere under `frontend/src/slices/activities/`, and
  `activityKeys.submissions` (`queries/index.ts:9-10`) is defined and consumed by nobody. With
  a refetch, Symptom A would self-correct on the next poll. It would not touch Symptom B. So
  this widens the blast radius by exactly one of two symptoms and causes neither.
- *The watchdog's own idempotency guard is correct and blameless.* `sweep_stalled`'s
  `pending`-only predicate (`submission_repo.py:222,232`) mirrors `record_validation`
  (`:175`) and `record_error` (`:199`), so the sweep and a late-arriving worker cannot
  double-write. Cited so the fix is not tempted to weaken it (§7).
- *[R30.12] makes the signal best-effort.* The completion emit is wrapped and swallowed
  (`activities.py:104-111`). That licenses a *failed* emit, not an *absent* one — the
  distinction the fix rests on.

**One half of the original claim was refuted upstream and stays refuted.** A parked
`wait_for_event` node does not hang forever: its default `timeout_seconds` is 600
(`backend/contexts/workflow/application/executors/wait_for_event.py:45`), shorter than the
900 s TTL, so it times out before the watchdog fires. That bounds the workflow-side blast
radius; it does not make the missing signal correct, because a *trigger* (as opposed to a
wait) has no timeout to fall back on.

### V-7

1. **Trigger.** A participant submits with an optional `enum-array` untouched.
2. `initialValues` seeded it to `[]` (`schemaFields.ts:76-78`). **Correct, and not the cause:**
   as an *input model* for a checkbox group, "nothing ticked" genuinely is the empty array.
   The audit's own §4 makes the parallel point for booleans
   (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:430-440`).
3. **Root cause.** `schemaFields.ts:117` assigns `payload[f.name]` unconditionally, converting
   an input-model default into a *submitted assertion*. It is the earliest correctable link
   and the only one: `initialValues` is right, the server is right ([R30.04],
   `submission_service.py:89-91`), and every sibling branch in the same switch already does
   the right thing.
4. **Symptom.** `tags: []` reaches the server, `minItems: 1` fails, 422.

**Aggravating factors, not causes.**
- `zodForField` applies `.min(1)` only when the field is `required` (`schemaFields.ts:165`)
  and `jsonSchemaToZod` never reads `minItems`, so client validation cannot pre-empt the
  round-trip. Worsens the UX; a correct `:117` makes the constraint unreachable anyway.
- `ActivityHost.vue:90-96` renders `err.message` verbatim, which is the joined jsonschema
  message (`submission_service.py:91`) naming no field. Makes the failure unattributable.
  Independent of the cause and shared by every 422; not fixed here (FU-3).

**Provenance: an unfinished generalisation, not a regression.** The author demonstrably
reasoned about this exact hazard class twice — the string branch's comment
(`schemaFields.ts:120-122`) and `SchemaForm.vue:113-116`, which renders numbers as text inputs
specifically so a cleared numeric field is not "silently submit[ted] 0". Arrays were left
doing the thing both guards exist to prevent.

## 6. Blast Radius and Sibling Suspects

### Blast radius

- **F-12** — every room, every tenant, retroactively and going forward. Any member who can
  send (including a guest-link enrollee, `access.py:153-154`) can close any participant's
  session in that room, with the target discoverable in one request (§4). Damage is to the
  research record: split attempt histories with duplicate `attempt_no`, partial per-session
  aggregates, and a reset `rolling.same_error_count`. Not recoverable (see §7) and not
  currently detectable (Q-4).
- **F-20** — every submission whose async validation exceeds 900 s. Bounded by how often async
  validators stall, which is a deployment property. The database is never wrong; what is lost
  is one room event and one workflow signal per swept submission.
- **V-7** — zero configurations in the current build (§4). Unbounded for operator-authored
  schemas. Failure mode is a hard-blocked submission with a non-attributable error, not data
  loss.

### Security Considerations

F-12 is an authorization defect, so this subsection is mandatory.

**What must not weaken.** The outer gate stays exactly as it is: `resolve_room_access` +
`ensure_can_send` (`activities.py:343-344`, `:325`, `:368`) is the tenant and room boundary and
the new check is strictly *inside* it, never a replacement. Tenant isolation on the type must
survive untouched — `session_service.py:40-42` and `submission_service.py:76-80` both reject a
type whose `project_id` differs from the room's, and that check must keep running *before* any
new subject comparison so a cross-tenant probe still gets 404 rather than a subject-mismatch
answer that confirms the type exists. `sweep_stalled`'s `pending`-only predicate
(`submission_repo.py:222,232`) must survive F-20's rewrite: adding a `RETURNING` clause must
not become an excuse to restructure the WHERE.

**What an over-broad fix would expose.** Gating close on `ensure_room_creator`
(`access.py:161-163`) instead of subject identity would *grant* the facilitator a capability
[R30.22] (`REQUIREMENTS.md:2133`) explicitly withholds — "ending the room activation does not
force-close open participant sessions" — and which `activities-activation-ux/spec.md:49-51`
records as a named non-goal. It would create the force-close power through the back door while
still locking the participant out of their own session. Separately, adding a facilitator arm to
`submit`'s subject override would grant proxy-submission authority over another person's
research record, which nothing in §30 authorises.

**What an over-narrow fix would break.** Two failure modes, both real:
1. *Fixing only `close_session`.* The F-12 symptom — a corrupted per-subject attempt history —
   is fully reachable through `submit` with a foreign `subject_user_id` (`activities.py:374`)
   without closing anything. A close-only fix would let this dossier claim the symptom is
   closed while leaving it open. This is why Q-2 folds FU-3 in.
2. *Dropping the admin bypass.* Every adjacent activities route passes
   `is_admin=principal.is_admin` (`activities.py:312,325,344,368`). Enforcing `subject ==
   caller` with no admin arm would break the platform-support path uniquely on these three
   routes, inconsistently with the rest of the context.

**Is a `check-security` referral warranted? Yes — and the justification is not "it is an
authz bug".** The reasoning either way:

*Against.* Everything this dossier fixes is closed by the fix and pinned by T-1..T-4 (§8). The
sibling sweep below is exhaustive **within `contexts/activities/`** and every site there has a
verdict with evidence. On its own terms, this dossier does not need a second opinion.

*For, and decisive.* The pattern F-12 instantiates — prove room membership once, then trust
the resource identifier the caller supplied — is not an activities-context property. It is a
property of how the room-access chain is used, and V-8
(`docs/audits/2026-07-22-conversation-verification-gap/findings.md:346-384`) is the same shape
in `contexts/conversation/` (TUS authorization proved at create, never re-proved at PATCH or
finalize). Both audits reached the same conclusion independently: the a2u hand-off routes V-8
to `check-security` "alongside F-12 — both are 'gate proved once, never re-proved'"
(`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:682`), and F-12's own entry
(`:379-380`) says it is "worth routing to `check-security` for the authorization view". An
area-wide sweep for that pattern is exactly what this per-finding dossier cannot perform and
what `check-security` is for.

**Scoping the referral, so it is not a gate.** It runs **in parallel with, and preferably after,
this fix** — not as a precondition. This dossier is not blocked on it: the defect is confirmed,
the fix is bounded, and the referral's question is "where else does this shape occur", which
does not change what must happen here. Running it after the fix also means it audits the
corrected shape and can judge whether the chosen remedy generalises. `depends_on` stays `[]`
accordingly; the referral is recorded as AC-13, not as a dependency.

F-20 and V-7 are not security findings. F-20 loses notifications about a state the database
records correctly; nothing is disclosed and no gate is bypassed. V-7 blocks a submission the
server correctly rejects, which is a usability failure on the *safe* side of the boundary.

### Sibling suspects

Three sweeps, one per root cause. Every row carries its evidence.

**Sweep 1 — "a per-subject or per-actor resource authorized by room membership alone."**
Every route in `backend/app/api/v1/activities.py` was checked.

| Site | Verdict | Evidence |
|---|---|---|
| `close_activity_session` → `ActivitySessionService.close_session` | **Confirmed — F-12** | §5 |
| `open_activity_session` (`activities.py:317-333`) | **Confirmed — same root cause, folded in (Q-2)** | `:330` resolves `body.subject_user_id or principal.user_id`; `open_session` (`session_service.py:26-62`) takes no caller identity, so any member may open a session naming any subject. |
| `submit_activity` (`activities.py:359-385`) | **Confirmed — same root cause, folded in (Q-2)** | `:373-374` passes `producer_user_id=principal.user_id` and `subject_user_id=body.subject_user_id or principal.user_id` as independent values; `SubmissionService.submit` (`submission_service.py:62-75`) never compares them. Already recorded as FU-3 of `docs/tasks/2026-07-13-activities-activation-ux/spec.md:370-372`. |
| `SubmissionService._resolve_session` with an explicit `session_id` (`submission_service.py:310-320`) | **Cleared** | It compares all four coordinates including `session.subject_user_id != subject_user_id` (`:316`) and `status != 'open'` (`:317`) before accepting the session. This is the correct shape, in the same context, one file away from the defect — which is what makes F-12 an omission rather than an unconsidered case. |
| `list_activity_submissions` (`activities.py:388-412`) | **Cleared as a read, but named as the discovery vector** | Room-wide by design: [R30.10] (`REQUIREMENTS.md:2121`) specifies a generic aggregation read model "per subject/session/room" for dashboards and observers, and the endpoint gates on `ensure_can_read` (`:398`). The DTO excludes both `payload` and `subject_user_id` (`activities.py:116-127`), so it discloses submission metadata, not answers or attribution. It is not a defect; §5 records it as the aggravating factor that makes F-12 targetable, and §13 FU-4 records the residual question of whether room-wide submission metadata should be creator-scoped. |
| `start_activity_activation` / `end_activity_activation` (`activities.py:259-302`) | **Cleared** | Both call `ensure_room_creator` (`:268`, `:291`), the stronger gate ([R30.21], `REQUIREMENTS.md:2132`). Not per-subject resources. |
| `get_active_activity_activation` (`activities.py:305-314`) | **Cleared** | Read-only and room-scoped by design — [R30.21] requires the endpoint precisely so "late-joining or reconnecting participants hydrate the same state". `ensure_can_read` is the correct floor. |
| `register_activity_type` / `list_activity_types` (`activities.py:218-251`) | **Cleared** | Project-scoped, gated by `assert_project_owner` (`:226`) and `assert_project_membership` (`:249`) per [R30.02]. No subject dimension exists. |

**Sweep 2 — "a terminal write-back that skips the notification the normal path performs."**

| Site | Verdict | Evidence |
|---|---|---|
| `activities_watchdog` (`app/workers/tasks/activities.py:164-187`) | **Confirmed — F-20** | §5 |
| `validate_activity_submission` completion (`activities.py:114-161`) | **Cleared** | Emits both: `_emit_validated` (`:158-159`) and `_emit_activity_signal` (`:160`), gated on `result_status in ("validated","error")` so a redelivered no-op does not re-emit (`:155`, pinned by `backend/tests/unit/test_activities_validation_worker.py:251-265`). |
| `submit_activity` post-commit dispatch (`activities.py:415-445`) | **Cleared** | Emits `activity.created` (`:421-428`), enqueues validation when pending (`:431-435`), and enqueues the submit-time `workflow_signal` (`:442-445`), each swallowing its own failure per [R30.12]. |
| `workflow_watchdog` → `RunEngine.force_fail` | **Cleared — and it is the positive precedent** | `backend/app/workers/tasks/workflow_watchdog.py:75` calls `force_fail` per run inside a loop (`:51-80`), and `force_fail` (`backend/contexts/workflow/application/run_engine.py:402-431`) emits both the audit event (`:418-426`) **and** the same `workflow.run_finished` pub/sub frame the normal completion path emits (`:427-430`). The platform's other watchdog already implements the contract F-20 breaks, and it achieves it by iterating rows rather than bulk-updating — which is the structural argument for §7's `RETURNING` clause. |
| Retention purge (`backend/contexts/conversation/application/retention_service.py:91-93`) | **Confirmed same shape — owned elsewhere, not fixed here** | It hard-deletes and publishes nothing (the module imports no `Publisher`), while `frontend/src/slices/conversation/utils/mergeMessages.ts:10-11` documents that out-of-window deletions "arrive via the `message.deleted` WS event". Recorded as FU-2 of `docs/audits/2026-07-22-conversation-verification-gap/findings.md:491-495` and owned by `docs/tasks/2026-07-22-retention-sweep-fixes/` via V-5. Disjoint files; re-filing it here would duplicate an owned item. |
| `record_validation` / `record_validation_error` (`submission_service.py:213-256`) | **Cleared** | Both emit the in-transaction `activity.validated` audit event on transition (`:230-238`, `:247-255`) and return `changed` so the caller can decide whether to emit externally — which the worker does (`activities.py:142-148,155`). The service layer's contract is intact; only the sweep's caller cannot honour it. |

**Sweep 3 — "an `assemblePayload` branch that emits a value the participant never entered."**
All six branches of the switch at `schemaFields.ts:102-139`.

| Branch | Verdict | Evidence |
|---|---|---|
| `enum-array` (`:116-118`) | **Confirmed — V-7** | §5 |
| `string` (`:119-126`) | **Cleared** | `if (f.required \|\| s !== '')` — omits an empty optional string, with the reasoning stated at `:120-122`. This is the shape V-7's fix copies. |
| `number` (`:108-112`) | **Cleared** | Guarded on `v !== null && v !== '' && v !== undefined && !Number.isNaN(...)`, so a cleared numeric field is omitted. Reinforced at the render layer by `SchemaForm.vue:113-116`, which deliberately uses a text input so "cleared" survives as `''`. |
| `enum` (`:113-115`) | **Cleared** | `if (v !== null && v !== undefined && v !== '')`; `initialValues` seeds `null` (`:71-74`), so an untouched select is omitted. |
| `json` (`:127-137`) | **Cleared** | Emits only when `text` is non-empty after trim (`:129`); an unparseable value produces a field error rather than a silent drop, per [R30.18]. |
| `boolean` (`:105-107`) | **Cleared as by-design** | `payload[f.name] = v === true` always emits, which is correct: an `SCheckbox` in this form has no unset state, so absence and `false` are indistinguishable to the participant. Refuted at `docs/audits/2026-07-22-conversation-verification-gap/findings.md:430-440`, including the observation that an `enum` on a boolean routes to the `SSelect` path instead (`schemaFields.ts:28` checks `enum` before `type`). Not re-litigated. |
| `initialValues` (`:65-86`) | **Cleared** | Its `enum-array → []` seed (`:76-78`) is correct as an input model and is not the defect; see §5 step 2. |

## 7. Fix Design

Backend for F-12 and F-20; one line of frontend for V-7 plus its test. **No migration, no
schema change, no new table, no new column** — see the data-repair position below.

### F-12 — prove subject identity in the service that owns the resource

**1. `close_session` gains the caller's subject.** `session_service.py:64-68` takes an
additional `subject_user_id: uuid.UUID | None`, where `None` means "no subject constraint"
(the admin arm), and extends the existing guard at `:66` to include
`session.subject_user_id != subject_user_id` when the constraint is present. Same
`SessionNotFound` (Q-1). Placing it in the existing boolean keeps the room check and the
subject check in one expression, which is the whole point: they are two coordinates of one
resource identity.

**2. `open_session` and `submit` refuse a foreign subject** (Q-2). Both already receive
`subject_user_id`; each gains a `caller_user_id` (or the same `None`-means-admin convention)
and rejects a mismatch before any repository call. For `submit`, the check must sit **after**
the type/project isolation check at `submission_service.py:76-80` so a cross-tenant probe
still 404s on the type, per §6.

**3. Facade and routes pass it through.** `facade.py:154-173` forwards the new parameters;
`activities.py:326-331,346,369-380` supply `principal.user_id`, or `None` when
`principal.is_admin`.

**4. Re-export `is_room_creator`** — required only if /build chooses to distinguish the admin
arm at the route rather than via `principal.is_admin`. Note the constraint:
`backend/contexts/conversation/interfaces/access.py:9-29` re-exports `ensure_room_creator` but
**not** the boolean `is_room_creator`, which exists at
`backend/contexts/conversation/application/access.py:139-158`. Per Q-3 no facilitator arm is
added, so this should not be needed; it is recorded so /build does not reach past the
interfaces facade if it finds itself wanting the predicate.

**5. Audit the close** (Q-4). `activity.session_closed` emitted in-transaction from the route
or service, mirroring `submission_service.py:178-195`, carrying `session_id`, `chatroom_id`
and `subject_user_id`.

**Why this corrects rather than masks.** The correction sits on the same line §5 names as the
root cause. With the fix, every step upstream is unchanged — `B` is still a room member, still
passes `ensure_can_send`, still holds a valid `session_id` read from a legitimate endpoint —
and the outcome differs. Nothing downstream compensates for anything upstream. Three masking
variants were considered and rejected:
- *Removing `session_id` from `ActivitySubmissionOut`.* Fixes discoverability, not authority.
  The close would remain permitted for anyone who obtains the id another way, and it would
  break the `session_id` filter the same endpoint exposes (`activities.py:391`).
- *Restricting the list endpoint to the room creator.* Contradicts [R30.10]
  (`REQUIREMENTS.md:2121`) and still leaves the write open.
- *Blocking the close while an activation is live.* Contradicts [R30.22]
  (`REQUIREMENTS.md:2133`), which makes finishing part of the participant's normal lifecycle.

**Data repair: none, and the reason is that repair is impossible and would be harmful.**
- *Impossible to identify the affected rows.* `activity_sessions` records no actor for the
  close — `session_repo.close` (`session_repo.py:109-122`) sets `status` and `closed_at` and
  nothing else, and the table (`backend/contexts/activities/infrastructure/tables.py`) has no
  actor column. The audit trail cannot substitute: `close_activity_session`
  (`activities.py:336-351`) emits **no audit event at all** today, which is the operative
  reason Q-4 adds one going forward. A closed session closed by its own subject and one closed
  by an attacker are byte-identical.
- *Harmful even if they could be identified.* The repair would have to either reopen the
  session — for which no repository method exists (`session_repo.py` has `create_open` and
  `close`, no reopen) and which would violate the `uq_activity_sessions_open` partial-unique
  if the subject has since opened a successor — or renumber `attempt_no` across the split
  sessions. The latter rewrites a committed research record whose attempt number [R30.01]
  (`REQUIREMENTS.md:2112`) makes server-assigned and authoritative, and whose values are
  already referenced by the SYSTEM echo messages in the transcript
  (`submission_service.py:168-177`, `metadata.attempt_no`) and by every emitted `activity`
  signal (`:366`). A migration would desynchronise the record from its own transcript.
- *Nothing degrades further after deploy.* Every already-split history stays exactly as it is,
  correct-as-a-record even though it is wrong-as-a-history, and no new split can be created.

**No Alembic revision is part of this dossier. If /build finds itself writing one for F-12,
the fix has drifted into the rejected renumbering and must stop.**

### F-20 — make the sweep able to notify, then notify

**1. `sweep_stalled` returns what it swept.** `submission_repo.py:210-242` adds
`.returning(_SUB.c.id, _SUB.c.chatroom_id)` and returns the rows instead of `rowcount`. The
bounded `LIMIT 500` batch (`:211,226`) and the `pending`-only predicate (`:222,232`) are
unchanged — §6 flags the predicate as a must-not-weaken.

**2. `SubmissionService.sweep_stalled` (`submission_service.py:258-262`) propagates the rows.**

**3. `activities_watchdog` (`activities.py:164-187`) emits per row**, in the ordering the
completion path already documents at `:152-156`: commit first, then build each signal via
`facade.build_activity_signal` in the same session, then emit outside the session. The
ordering is load-bearing, not cosmetic — `_same_error_count` (`submission_service.py:291-300`)
must count the just-written `validation_timeout` verdict, and post-commit is where the
existing code says that holds. Per row: `_emit_validated(chatroom_id, sid, "error")` (`:86-95`)
and `_emit_activity_signal(payload)` (`:98-111`). Both already swallow their own failures, so
one bad row cannot fail the sweep.

**4. The aggregate `activity.watchdog_swept` audit stays as-is** (`:174-180`) and no per-row
audit is added (Q-5).

**5. No frontend change.** Once the frame is published, the existing chain carries it:
`useChatroomSocket.ts:306-311` → `applyValidated` (`stores/activities.ts:55-68`) → the badge
(`ActivityOutcomeBadge.vue:28-30` renders the `error` variant). The frontend is already correct
and already wired; it was starved of an event.

**Why this corrects rather than masks.** The masking fix is obvious and available: add a
`useQuery` with a `refetchInterval` over `listActivitySubmissions`
(`frontend/src/slices/activities/api/index.ts:85-93`, with the key already defined at
`queries/index.ts:9-10`). It is rejected on two independent grounds. First, it repairs one of
two symptoms — polling can refresh a badge, but nothing on the client can enqueue the
server-side `workflow_signal` an [R30.13] trigger needs, so an impasse rule would stay blind
regardless. Second, it puts the correction three layers away from the cause and leaves the
server asserting, via `activities.py:30-32`, an equivalence it does not implement. Adding
`RETURNING` is the minimal change that makes the true statement true. The precedent is in the
codebase: `workflow_watchdog` already loops per row and emits the normal path's frame
(`workflow_watchdog.py:51-80` → `run_engine.py:427-430`).

**Data repair: none, and retro-emitting would cause harm.**
- *The database is already correct.* `sweep_stalled` wrote the right terminal state
  (`validation_status='error'`, `error_class='validation_timeout'`, `validated_at=swept_at`,
  `submission_repo.py:236-240`). No row holds a wrong value; there is nothing to repair *to*.
- *The stale badge is not persisted.* `stores/activities.ts:15` is in-memory reactive state,
  cleared by `resetRoom` / `clearAll` (`:100-110`) and on session clear (`:113`). Every
  affected client self-heals on reload.
- *Replaying the missed signals would be actively wrong.* `rolling.same_error_count` is
  computed over `_ROLLING_WINDOW_SECONDS = 60` (`submission_service.py:51`); a signal emitted
  now for a submission swept days ago would carry a window that has long expired, and firing
  an [R30.13] `activity_event` trigger for a concluded session could start agent turns on the
  user's own provider key. [R30.12] (`REQUIREMENTS.md:2123`) makes emission explicitly
  best-effort, so a signal that was never sent is within contract; a signal sent late against
  a stale window is not.

### V-7 — omit an untouched optional array

`schemaFields.ts:117` takes the shape of the string branch at `:119-126`: coerce to an array,
then emit only when the field is `required` or the array is non-empty. Keeping a **required**
empty array is deliberate and mirrors `:122-124`'s stated reasoning — a required field is kept
so `zodForField`'s `.min(1)` (`:165`) flags it in the client, rather than disappearing into
`validatePayload`'s required-presence branch (`:194-195`) with a less specific message. Update
the docstring at `:94-95` only if /build finds it now inaccurate; it should not be — the fix
makes the code match the sentence already written there.

**Why this corrects rather than masks.** The alternatives are all further from the cause and
each breaks something. Adding `minItems` support to `jsonSchemaToZod` would surface the error
client-side, which is a better error message for a submission that should never have been
constructed — it masks by improving the diagnostics of a wrong payload. Stripping empty arrays
server-side before `payload_errors` would make the backend silently reinterpret a submitted
value, contradicting [R30.03]'s server-authority posture and [R30.04]'s "validated … before
persistence" rule. Seeding `enum-array` to `undefined` in `initialValues` would break the
`SCheckbox` group's binding for a control whose empty state genuinely is `[]`.

**Data repair: none, and nothing exists to repair.** The defect blocks a submission; it never
writes one. No row is affected because a 422 is returned before
`ActivitySubmissionRepository.insert` (`submission_repo.py:90-126`) is reached
(`submission_service.py:89-91` raises ahead of `:152`). The verification audit further
established that no shipping configuration can trigger it (§4), so the affected-row count in
the current build is provably zero.

## 8. Regression Test Plan

Failing tests first. Every test states why it fails today. Both tiers used already exist —
`backend/tests/unit/` (service, repository and route tests, with route tests demonstrated at
`backend/tests/unit/test_activity_activation_routes.py:20-58` using monkeypatched facades) and
`frontend/src/slices/activities/__tests__/`. **No new tier is proposed** and no integration
test is needed: every assertion below is reachable at the unit tier with the existing harnesses.

**T-1 (fails today) — `backend/tests/unit/test_activities_services.py`**, new case
`"closing another subject's session is refused"`. Open a session for subject `A` in room `R`,
then call `close_session` with `subject_user_id=B`; assert `SessionNotFound` and that
`ActivitySessionRepository.close` is never awaited. **Fails today** because
`session_service.py:66` compares only `session.chatroom_id`; the call succeeds and the row
closes. This is the dossier's primary failing test.

**T-2 (fails today) — same file**, `"opening a session for another subject is refused"`.
Call `open_session` with `subject_user_id=B` as caller `A`. **Fails today** at a stronger
level than T-1: `open_session` (`session_service.py:26-33`) has no caller parameter at all, so
the test cannot even be written against the current signature — it fails to compile under
mypy and fails at runtime on an unexpected keyword. That is the correct signal; the missing
parameter *is* the defect.

**T-3 (fails today) — same file**, `"submitting on behalf of another subject is refused"`.
Call `SubmissionService.submit` with `producer_user_id=B, subject_user_id=A`. **Fails today**
because `submit` (`submission_service.py:62-75`) accepts both as independent parameters and
never compares them; the submission is inserted against `A`'s session
(`:152-166`). Assert the rejection happens **after** the type/project isolation check
(`:76-80`) by also asserting that a cross-tenant type still raises `ActivityTypeNotFound`
rather than the subject error — this pins the §6 ordering constraint.

**T-4 (passes today, guard against over-correction) — same file**,
`"a subject closes their own session, and a double close stays a no-op"`. Assert the subject's
own close succeeds and a second close returns without error (`session_repo.py:109-122`'s
`status='open'` guard makes it 0 rows). Also assert the platform-admin arm still closes another
subject's session (Q-3). Passes today for the first two assertions and fails on the third only
in the sense that the admin arm does not yet exist — /build should write it against the new
signature. This is the test that fails loudly if someone later implements Q-3's rejected
facilitator arm or drops the admin bypass.

**T-5 (fails today) — `backend/tests/unit/test_activities_validation_worker.py`,
class `TestWatchdog`**, new case
`"the watchdog emits activity.validated and the workflow signal for each swept row"`. Stub
`facade.sweep_stalled` to return two `(submission_id, chatroom_id)` rows and
`facade.build_activity_signal` to return a payload; patch `_emit_validated` and
`shared_kernel.queue.enqueue` as the file already does (`:78-83`, `:260`). Assert two
`_emit_validated` awaits with `status="error"` and two `workflow_signal` enqueues, and that
both happen after `db.commit`. **Fails today** on the first assertion:
`activities.py:171-187` never references `_emit_validated`, and `sweep_stalled` returns an
`int` (`submission_repo.py:242`), so no id exists to emit for. The existing sweep test
(`:269-283`) asserts only `af.sweep_stalled.assert_awaited_once()` and the `"swept=4"` return
string, so it must be updated for the new return type — a mechanical change, flagged so /build
does not mistake it for a behavioural decision.

**T-6 (fails today) — `backend/tests/unit/test_activity_repos.py`**, new case
`"sweep_stalled returns the identity of each swept row"`. Assert the return value carries the
id and `chatroom_id` of every transitioned row. **Fails today** because `submission_repo.py:242`
returns `rowcount(result) or 0`.

**T-7 (passes today, guard) — same file**, `"the sweep transitions only pending rows"`.
Seed one `pending` row older than the cutoff, one `validated` and one already-`error`; assert
only the first transitions and the other two are byte-unchanged. Passes today via the
predicate at `submission_repo.py:222,232`; included because the `RETURNING` rewrite touches
that exact statement and §6 names the predicate as a must-not-weaken.

**T-8 (fails today) — `frontend/src/slices/activities/__tests__/schemaForm.test.ts`**, the
existing case `'omits empty optional values'` (`:79-83`). Change the second assertion from
`expect(payload.tags).toEqual([])` (`:82`) to `expect('tags' in payload).toBe(false)`, matching
its sibling at `:81`. **Fails today** by construction — the current assertion asserts the
defect, and the case's own title asserts the fix. Per Q-7 this is an unexamined assertion, not
a deliberate pin; /build must not preserve it.

**T-9 (passes today, guard) — same file**, new case
`"keeps a required enum-array so the client min(1) check still flags it"`. With `tags` in the
schema's `required` list and no selection, assert `'tags' in payload` **and** that
`validatePayload` returns `fieldInvalid` for it. Passes today for the presence half; the
value is that it pins §7's deliberate `f.required ||` clause, so the fix is not simplified
into an unconditional omission that would silently downgrade a required empty array from
zod's `.min(1)` (`schemaFields.ts:165`) to the generic required-presence branch (`:194-195`).

**T-10 (passes today, guard) — same file**, new case
`"emits a touched optional enum-array"`. Assert a selection of `['a']` survives assembly.
Passes today; guards against the fix over-reaching into "never emit optional arrays".

## 9. Risks and Rollback

- **F-12 breaks any client that relied on proxy submission.** By design, and the risk is
  assessed as near-zero: `activities-activation-ux/spec.md:52-54` records that "v1 frontend
  always submits `subject=self`", and the frontend confirms it —
  `useActivityHost.ts:48-53` forwards `subject_user_id` from
  `toValue(options.subjectUserId) ?? null`, and `ActivityHost.vue:34` defaults that prop to
  `null` (`:22-25`), so the shipped client sends `null` and the server resolves it to the
  caller (`activities.py:330,374`). No shipped surface passes a foreign subject. A future
  proctor/facilitator proxy feature must go through an explicit capability, per FU-3's own
  wording.
- **F-12 changes three facade signatures.** `facade.py:154-173` and the two submission
  entry points. Internal surface only — the facade is the context's boundary
  (`facade.py:1-7`) and nothing outside `app/api/v1/activities.py` and the worker calls
  these. `pnpm run gen:api` is unaffected: the HTTP request and response models
  (`activities.py:80-83,109-113,99-107`) do not change shape.
- **F-20 emits into rooms during the sweep tick.** Up to 500 `activity.validated` frames plus
  500 enqueues per minute in the pathological case. Bounded by the existing batch limit
  (`submission_repo.py:211`), which is unchanged, and each emit already swallows its own
  failure (`activities.py:94-95`, `:108-111`), so a Redis hiccup degrades to the current
  behaviour rather than failing the sweep. If the volume proves objectionable in practice the
  correct lever is the batch limit, not the emit.
- **F-20's emit ordering is a real constraint, not a style note.** The signal must be built
  post-commit so `rolling.same_error_count` counts the just-written verdict — the reasoning is
  already recorded at `activities.py:152-154` for the completion path. Building it pre-commit
  would ship an off-by-one aggregate into every impasse rule.
- **V-7 changes an existing test assertion.** Called out explicitly (Q-7, T-8) so the diff is
  not read as a test being weakened to fit an implementation. The assertion contradicted its
  own case title.
- **Rollback.** Three independent reverts, no shared state. No migration, no schema change, no
  backfill, no persisted artifact written by any part of the fix — a direct corollary of §7's
  three data-repair positions. Reverting F-12 restores the current (defective) authorization;
  reverting F-20 restores the silent sweep, with the database in both cases identical either
  way; reverting V-7 restores the emitted `[]`. Nothing a rollback must reconcile.

## 10. Acceptance Criteria

- [ ] AC-1: T-1, T-2, T-3, T-5, T-6 and T-8 from §8 fail before the change and pass after.
- [ ] AC-2: closing an activity session succeeds only for that session's `subject_user_id`, or
  for a platform admin; every other caller — including a room creator and a guest — receives
  `SessionNotFound` (404) and the row is not modified.
- [ ] AC-3: `open_activity_session` and `submit_activity` reject a `subject_user_id` that is
  neither the caller nor an admin call, and the rejection is ordered **after** the
  type/project isolation check so a cross-tenant type still yields `ActivityTypeNotFound`.
- [ ] AC-4: `close_activity_session` emits an `activity.session_closed` audit event in the same
  transaction as the close, carrying `session_id`, `chatroom_id` and `subject_user_id` (Q-4).
- [ ] AC-5: `ActivitySubmissionRepository.sweep_stalled` returns the id and `chatroom_id` of
  every row it transitioned, and still transitions **only** rows whose `validation_status` is
  `pending` and whose `created_at` predates the cutoff (T-7).
- [ ] AC-6: `activities_watchdog` emits one `activity.validated` with `status="error"` on the
  room channel and enqueues one `workflow_signal("activity", …)` per swept submission, both
  after commit, and a failure in either never fails the sweep.
- [ ] AC-7: the aggregate `activity.watchdog_swept` audit event is retained unchanged and no
  per-row `activity.validated` audit is added (Q-5).
- [ ] AC-8: an untouched **optional** `enum-array` is omitted from the assembled payload; a
  **required** one is still emitted so the client `.min(1)` check flags it (T-9); a touched one
  is emitted with its selections (T-10).
- [ ] AC-9: **no Alembic revision, no backfill, no data-mutating script and no new table or
  column** is added by this change.
- [ ] AC-10: no frontend change outside `frontend/src/slices/activities/components/schemaFields.ts`
  and its tests — in particular `useChatroomSocket.ts`, the activities store and the badge are
  untouched (Q-6, §7).
- [ ] AC-11: backend gate green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`.
- [ ] AC-12: frontend gate green — `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`.
- [ ] AC-13: a `check-security` pass is run against the **corrected** code for the "gate proved
  once, never re-proved" pattern across the room-access chain, scoped as §6 describes and
  paired with V-8 per `docs/audits/2026-07-22-agent-to-user-conversation/findings.md:682`. This
  is a deliverable of the task, not a precondition of it.

## 11. SRS Delta

**Requirements: none.** §30 is correct as written and is what convicts the code in all three
cases. [R30.01] (`REQUIREMENTS.md:2112`) and [R30.22] (`:2133`) already scope a session to
its subject; [R30.06] (`:2117`) and [R30.12] (`:2123`) already make the watchdog a validation
completion that must emit; [R30.18] (`:2129`) already governs the generic form's fidelity.
Nothing needs amending.

**One judgement call, recorded rather than silently taken.** Q-4 adds an
`activity.session_closed` audit event. [R30.11] (`REQUIREMENTS.md:2122`) names "type
registration, submission, and validation" and does **not** name session lifecycle events, so
this is additive to the SRS rather than mandated by it. It is deliberately **not** drafted as
an SRS amendment: the event is an operational necessity for verifying an authorization fix
(§7), not a new product behaviour, and [R30.11] is not made false by an implementation that
audits more than it lists. If the user prefers the SRS to enumerate it, the amendment is one
clause on [R30.11] and can be applied at approval.

**One documentation correction.** `docs/tasks/2026-07-13-activities-activation-ux/spec.md:370-372`
(FU-3) is discharged by this dossier per Q-2. That dossier is `status: implemented` and per
`docs/tasks/README.md` is not renamed or rewritten; the discharge is recorded here and
in FU-1's board correction rather than by editing a closed dossier.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `docs/tasks/BOARD.md`'s `wakeup-trigger-state-and-bounds` row is labelled
  "a2u F-3, F-12, F-14, F-21, F-38". Those are the **a2a** audit's findings
  (`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md:136,389,438,594,959`); the
  a2u audit has no F-38 and its F-3 and F-12 are owned by the attachment-lifecycle dossier and
  this one respectively. The row should read `a2a`. Correct it when the board is next touched;
  a reader currently sees two dossiers claiming F-12.
- **FU-2** — F-20's fix delivers the `activity.validated` frame to connected clients only. A
  participant disconnected at the moment the sweep runs still sees a stale badge, because
  nothing in `frontend/src/slices/activities/` re-fetches (no `useQuery`, `refetchInterval` or
  `invalidateQueries`; `activityKeys.submissions` at `queries/index.ts:9-10` has no consumer).
  This is the missed-frame class routed elsewhere:
  `docs/audits/2026-07-22-agent-to-user-conversation/findings.md:693-697` groups F-11, V-2 and
  the config audit's F-13 under one cause, with the generic remedy recorded as FU-1 of
  `docs/tasks/2026-07-22-prompt-assistant-delivery-recovery/`. Deliberately not solved here
  (Q-6); the activities slice should adopt whatever that work lands.
- **FU-3** — Every activities 422 surfaces the raw joined jsonschema message.
  `submission_service.py:91` joins up to five `payload_errors` strings (`schema.py:27-30`),
  which reach the participant verbatim through `useActivityHost.ts:63-64` and
  `ActivityHost.vue:90-96`. Messages like `[] is too short` name no field and cannot be
  attributed to a control. Independent of V-7 — it is what makes *any* server-side schema
  rejection unhelpful. Worth an error-shape pass (per-field `payload_errors` keyed by JSON
  pointer, rendered against the matching `SFormField`) rather than a widening of this dossier.
- **FU-4** — `list_activity_submissions` returns every room member's submission metadata to
  any room reader including a guest (`activities.py:388-412`, gated on `ensure_can_read`).
  §6 clears it as [R30.10]-intended and notes the DTO excludes `payload` and
  `subject_user_id` (`activities.py:116-127`). Still worth a design question for a research
  context: should submission metadata be creator-scoped, or is room-wide visibility the
  intended classroom semantic? A design call, not a defect — [R30.10] currently answers "room-
  wide" and this dossier does not reopen it.
- **FU-5** — Activity sessions carry no `activation_id`, so attempts from two activation
  windows are unseparable after the fact. Already recorded as FU-2 of the source audit
  (`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:713-715`) and as a non-goal
  at `docs/tasks/2026-07-13-activities-activation-ux/spec.md:49-51`. Noted here only because
  F-12's damage — a split attempt history — is the same shape, and anyone reading §5 will
  wonder whether the two are the same problem. They are not: FU-5 is a deliberate absence of
  grouping, F-12 is an unauthorised state change.
- **FU-6** — `SCheckbox` supports `indeterminate`
  (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:436-440`) and `SchemaForm`
  never uses it. If the research record ever needs "unanswered" distinguished from "false" or
  "empty selection", the shared control is ready and only the form is not. Relevant to V-7's
  neighbourhood; explicitly out of scope, since changing it would alter what a submitted
  payload means.
</content>
