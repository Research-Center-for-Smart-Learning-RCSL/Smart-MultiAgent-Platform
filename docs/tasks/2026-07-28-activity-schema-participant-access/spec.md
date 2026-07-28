---
type: bugfix
status: implemented
created: 2026-07-28
requirements: [R30.02, R30.09, R30.17, R30.18, R30.21, R30.22]
depends_on: []
---

# Activity payload schema: participant access and validator-config confidentiality

## 1. Summary

A room participant cannot render an activity unless they hold a project role, and the only
endpoint that serves the rendering contract also returns the type's `validator_config`
verbatim. Because the shipped first-party validator `exact_match` stores the correct answer
in `validator_config.expected` (`backend/app/plugins/activity_validators.py:37`), the two
halves are mutually exclusive in practice: a guest participant cannot submit at all, and a
participant made a project member to fix that is handed every answer key in the project.
Both symptoms share one root cause — a single response model serving both the owner
authoring surface and the participant rendering surface — and both are fixed by splitting
that model and adding a room-scoped read path.

Surfaced by `docs/assessments/ai-teacher-phase1-spec-review.md` §3 (BUG-1 / BUG-2) while
checking the AI Teacher Phase 1 pedagogical specification against the platform.

## 2. Observed vs Expected

**Observed A — validator_config (answer keys) is readable by any project-scope role.**
`GET /api/projects/{project_id}/activity-types` gates on `assert_project_membership`
(`backend/app/api/v1/activities.py:364`), which passes for a caller holding *any* role at
project scope (`backend/app/api/v1/deps.py:105-110`). The response model `ActivityTypeOut`
declares `validator_config: dict[str, Any]` (`activities.py:92`) and `_type_out` copies it
straight through with no redaction (`activities.py:180`). For an `in_process`/`exact_match`
type, that dict holds `{validator_id, field, expected, case_sensitive}` where `expected`
is the correct answer (`backend/app/plugins/activity_validators.py:35-44`).

**Observed B — a participant without a project role cannot render or submit an activity.**
The Activity rail tab sources types only from that same project-scoped endpoint
(`frontend/src/slices/activities/components/ActivityPanel.vue:52`), resolves the active
type by matching ids against that list (`ActivityPanel.vue:29-31`), and wraps the entire
participant surface — the Join button and `ActivityHost` — in `v-if="activeType"`
(`ActivityPanel.vue:139-164`). A chatroom guest holds no project role
(`backend/contexts/conversation/application/access.py:97-106` resolves `is_guest`
independently of `roles`), so the endpoint 403s, `types` stays empty, `activeType` is
`null`, and nothing renders. The room-scoped activation read carries only
`activity_type_id` and no schema (`activities.py:115-122`, `:200-209`), so there is no
alternative source.

**Expected.**
- `[R30.18]` states that types without a custom plugin "are rendered by a generic
  JSON-schema form derived from the type's payload schema" — for a participant, which
  requires the participant to be able to obtain that schema.
- `[R30.21]`/`[R30.22]` model activation and submission as room-scoped: the facilitator is
  the room creator and a submission is accepted while the room has an active activation.
  Every write path in the participant flow gates on the room-access chain
  (`activities.py:457`, `:478`, `:509`), so the read path that makes those writes possible
  must not require a strictly stronger, project-scoped credential.
- `docs/tasks/2026-07-13-activities-activation-ux/spec.md` §2 states the intent verbatim:
  "any room sender sees the active activity in the Activity rail tab, explicitly starts
  their own per-subject session, submits through the existing `ActivityHost`, and
  finishes."
- Confidentiality of `validator_config` has **no** current intent source; `[R30.02]` and
  `[R30.09]` describe the ownership and isolation model but say nothing about the payload
  of `validator_config`. Confirmed with the user as Q-2 below and written into the SRS in
  §11 rather than left implicit.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does a participant obtain `payload_schema`? | **Both**: a new room-scoped `GET /api/chatrooms/{chatroom_id}/activity-types/{type_id}` returning a public projection, **and** the same projection embedded in the activation read plus the `activity.activation.started` broadcast | The embedded copy removes a round trip and hydrates late joiners and reconnects for free (`useChatroomSocket.ts:196-206` already re-reads the activation on reconnect); the standalone read is the recovery path when the broadcast was missed, when the store was reset, and the only path that works if a future flow needs a type that is not the currently active one. One shared projection helper keeps the two in step. |
| Q-2 | How is `validator_config` treated on the project-scoped surface? | **Owner-only.** The list omits `validator_config` for non-owner callers; registration and edit are already owner-gated and keep it | An answer key is the whole value of a task-based lesson, and the same field is where a webhook validator's credential would land if FU-1 of `2026-07-13-activities-platform-core` is ever built. Redacting for non-owners keeps the authoring UI working unchanged (it is owner-only) while removing the exposure. Rejected "leave as is": it makes any project member a leak and leaves no defence at all. |
| Q-3 | Are chatroom guests supported activity participants? | **Yes** — the room-access chain is the authority | Matches the room-scoped model `[R30.21]`/`[R30.22]` and the activation-UX intent; and it is the only way a class of students can join by link without each needing a project membership row. Rejected "activities require project membership": it would make the leak in Q-2 systemic by design, since every student would then hold a project role. |
| Q-4 | Does this depend on any open dossier? | **No** — `depends_on: []` | Checked every non-implemented row in `docs/tasks/BOARD.md` against this task's files. The only activities-adjacent row, `2026-07-22-activity-session-authz-and-validation`, is listed under "In progress" on the board but its own frontmatter reads `status: implemented`; per README.md the frontmatter wins, so it is not a blocker. No other open dossier touches `app/api/v1/activities.py`, `slices/activities`, or `useChatroomSocket.ts`'s activation cases. The stale board row is corrected in Step 7. |

## 4. Reproduction

Preconditions: a project with an owner O; a workspace and a chatroom R created by O;
`allow_guest_links` enabled on R; an `ActivityType` T registered in the project with
`validator_kind=in_process`, `validator_config={"validator_id":"exact_match","field":"answer","expected":"日本"}`;
T activated in R by O.

**A — answer-key exposure.** Add user M to the project with any non-owner role. As M:
`GET /api/projects/{project_id}/activity-types` returns T with
`validator_config.expected == "日本"`. M has never opened the room.

**B — guest cannot participate.** Enroll user G in R through the guest link (G holds no
project role). As G, open R and select the Activity tab: the request to
`GET /api/projects/{project_id}/activity-types` returns 403, the panel shows only the
fallback active-activity label (`ActivityPanel.vue:136-138`), and neither the Join button
nor `ActivityHost` renders. G can send chat messages in R, and
`POST /api/chatrooms/{R}/activity-sessions` would succeed if called directly
(`activities.py:457` gates on `ensure_can_send`), so the block is purely the missing read
path, not an authorization decision about G.

Both are deterministic; no timing or concurrency involved.

## 5. Root Cause Analysis

1. `ActivityTypeOut` is a single response model used for three different audiences —
   register, update, and list (`activities.py:85-97`, produced by `_type_out` at `:172-185`
   and returned at `:246`, `:305`, `:358`). It carries the full authoring record, including
   `validator_config`.
2. Because the only projection of an `ActivityType` is that authoring record, the only
   endpoint that can serve it is a project-scoped one, and it is gated at the weakest level
   that the authoring UI needs for its list (`assert_project_membership`, `:364`).
3. The participant rendering path needs exactly two fields from that record — `key` (plugin
   lookup, `ActivityHost.vue:44`) and `payload_schema` (form derivation,
   `ActivityHost.vue:45-47`) — plus `id` and `name` for display. With no narrower
   projection available, `ActivityPanel.vue:52` reaches for the authoring endpoint.
4. Symptom A follows from (1)+(2): everyone who can reach the list sees the answer key.
   Symptom B follows from (2)+(3): everyone who cannot reach the list loses the whole
   participant surface.

**Root cause**: step (1) — one response model conflating the owner authoring record with
the participant rendering contract. Correcting it removes both symptoms; correcting only
the gate in (2) would trade one symptom for the other, which is exactly the state the
system is in today.

Aggravating factor, not the cause: `exact_match` stores the answer inside
`validator_config` rather than in a separately-classified field
(`app/plugins/activity_validators.py:35-44`). That design is reasonable — the config is a
validator-private blob — and is what makes confidentiality the correct fix rather than
relocating the answer.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**
- Every `ActivityType` in a project, not just the active one: the list returns all
  non-deleted types (`facade.list_types`, `activities.py:365`).
- The exposure set is every caller holding any project-scope role, resolved through
  `TenancyRoleResolver` (`deps.py:107-110`). Chatroom guests hold none, which is precisely
  why symptom B exists.
- `webhook` types expose their validator URL and `mcp` types expose `agent_id` /
  `binding_id` / `tool_name` (`docs/activities-type-authoring-gap.md` §"Validator kinds").
  These are topology, not secrets, today. If FU-1 of
  `docs/tasks/2026-07-13-activities-platform-core/spec.md` (sealed webhook validator
  credentials) is built, `validator_config` becomes the natural home for a secret and this
  leak becomes a credential leak. Fixing it now is what makes that follow-up safe.
- No persisted bad data: nothing was written incorrectly, so there is no data-repair step.
  Answer keys that were already disclosed to a member cannot be un-disclosed; if a lesson
  has already run with students as project members, its `expected` values should be treated
  as burned and the type edited before reuse.

**Sibling suspects** (other places the same "authoring record served to a read audience"
pattern could exist):
- `POST` register (`activities.py:246`) and `PATCH` update (`:305`) also return
  `ActivityTypeOut` — **cleared**: both are `assert_project_owner` (`:254`, `:314`), so the
  audience is exactly the owner who supplied the config.
- `GET /api/activity-validators` (`activities.py:374-383`) — **cleared**: returns
  `{id, title}` only and its docstring states the intent explicitly (`:378-380`).
- `ActivitySubmissionOut` (`activities.py:142-...`) — **cleared for this defect**: it
  carries `sub_scores`/`error_class` about a submission, never the type's config. Note it
  is gated at `ensure_can_read` (`:540`), i.e. any room reader can list a room's
  submissions; that is the room-scoped model working as designed, not this bug.
- `ActivityActivationOut` (`activities.py:115-122`) — **confirmed adjacent, in scope**: it
  is the room-scoped read that *should* have carried the rendering contract and does not.
  Fixed here per Q-1.
- The generated frontend client wraps the same shapes
  (`frontend/src/shared/api-client/services/ActivitiesService.ts`) — regenerated, not
  hand-edited.

## 7. Fix Design

**Backend.**

1. New response model `ActivityTypePublicOut` in `app/api/v1/activities.py`:
   `{id, key, name, payload_schema}`. No `validator_config`, no `validator_kind`, no
   `retention_days`, no visibility flags. One helper `_type_public_out(t)` beside the
   existing `_type_out` so the two projections cannot drift.
2. New route `GET /api/chatrooms/{chatroom_id}/activity-types/{type_id}` on
   `chatroom_router`, gated by `resolve_room_access` + `ensure_can_read` (the pattern at
   `activities.py:443-444`), returning `ActivityTypePublicOut`. Tenant isolation: after
   `facade.get_type(type_id)` (`contexts/activities/interfaces/facade.py:141-142`), reject
   with 404 unless `activity_type.project_id == access.project_id` — the same
   never-leak-another-tenant's-type rule the submission service already applies
   (`contexts/activities/application/submission_service.py:81-84`).
3. `ActivityActivationOut` gains `activity_type: ActivityTypePublicOut | None`.
   `_activation_out` becomes async or takes the resolved type as a parameter; the three
   call sites (`activities.py:410`, `:432`, `:446`) resolve the type through the facade.
   `None` only when the type row is missing or cross-project, which the endpoint treats the
   same way as today's null activation.
4. `_dispatch_activation_started` (`activities.py:590-601`) adds the same projection under
   an `activity_type` key in the emitted payload. The payload stays small: a JSON Schema
   for a form of a few fields, not an arbitrary blob. Cap is not introduced here; see
   FU-2.
5. `list_activity_types` (`activities.py:358-366`) becomes owner-aware: resolve ownership
   once, and return `ActivityTypeOut` with `validator_config` populated only for an owner
   (or admin), otherwise `{}`. Implementation note: keep the field present and empty rather
   than making it optional, so the generated client's type does not become nullable across
   the whole authoring form. The membership gate itself is unchanged — a non-owner member
   still legitimately lists types (that is how a facilitator picks one to activate).

**Frontend.**

6. `slices/activities/types`: add `ActivityTypePublic` (the four public fields);
   `ActivationView` gains `activityType: ActivityTypePublic | null`.
7. `slices/activities/api/index.ts`: add `getRoomActivityType(chatroomId, typeId)`.
8. `stores/activities.ts` `setActivation` (`:74-83`) maps the new field through both of its
   input shapes.
9. `useChatroomSocket.ts` case `'activity.activation.started'` (`:438-450`) reads
   `ev.activity_type` into the store.
10. `ActivityPanel.vue`: the participant path stops depending on `listActivityTypes`.
    `activeType` comes from `activation.activityType`, falling back to
    `getRoomActivityType` when the store has an activation without one (missed broadcast,
    store reset). `listActivityTypes` stays, used **only** for the facilitator's start
    dropdown (`:32`, `:49-56`), and its failure must no longer block the participant path —
    today a 403 there sets `errorMessage` for everyone (`:54`).
11. `ActivityHost.vue` prop `activityType` narrows from `ActivityType` to
    `ActivityTypePublic`. It already reads only `id`, `key`, and `payload_schema`
    (`ActivityHost.vue:32,44,46`), so this is a type narrowing with no behavioural change.
12. `pnpm run gen:api` after the backend change; `check:openapi-drift` must pass.

**Why this is not a symptom patch.** The two symptoms are the two faces of one missing
distinction. After the split, the participant surface has its own projection reachable
through the room-access chain, and the authoring record is reachable only by the role that
authored it. Neither audience is served the other's model, so neither symptom can recur
through a new endpoint that reuses the wrong model.

**Data repair.** None required (nothing was persisted incorrectly). Operational note for
the release notes: `expected` values disclosed before the fix should be rotated by editing
the affected types.

## 8. Regression Test Plan

Written first, failing against current code.

Backend (`backend/tests/unit/test_activities_authz.py`, extending the existing room/project
gate matrix):
- `test_room_scoped_type_read_allows_guest` — a principal with `is_guest=True` and no
  project role reads `GET /api/chatrooms/{id}/activity-types/{type_id}` successfully.
  Fails today: the route does not exist (404).
- `test_room_scoped_type_read_omits_validator_config` — the response has no
  `validator_config` key. Fails today: no route.
- `test_room_scoped_type_read_rejects_cross_project_type` — a type from another project
  returns 404, not the row.
- `test_list_types_redacts_validator_config_for_non_owner` — a non-owner project member's
  list entries carry `validator_config == {}`, while an owner's carry the real config.
  Fails today: both carry the real config.

Backend (`backend/tests/unit/test_activities_services.py` or a new
`test_activities_activation_projection.py`):
- `test_active_activation_embeds_public_type` — the activation read includes
  `activity_type` with `key`/`payload_schema` and without `validator_config`. Fails today:
  the field does not exist.
- `test_activation_started_broadcast_carries_public_type` — the published
  `activity.activation.started` payload includes the projection. Fails today.

Frontend (`frontend/src/slices/activities/__tests__/`):
- `ActivityPanel.test.ts::renders participant surface when listActivityTypes rejects` —
  with an activation carrying `activityType` and `listActivityTypes` mocked to throw a 403,
  the Join button renders. Fails today: `activeType` is null so the surface is absent.
- `ActivityPanel.test.ts::falls back to getRoomActivityType when activation lacks the type`
  — store holds an activation with `activityType: null`; the panel fetches and renders.
- `activities.store.test.ts::setActivation carries activityType through both input shapes`.

## 9. Risks and Rollback

- **`_activation_out` gains a DB read.** Three call sites now resolve the type. All three
  already hold a session and are single-row primary-key reads; the started/ended dispatch
  paths already run post-commit best-effort. Risk is latency-negligible, but the ended
  dispatch must not acquire new failure modes — it does not embed the projection.
- **WS payload growth.** A pathologically large `payload_schema` would inflate every
  activation broadcast. Bounded in practice by the authoring form, not enforced; recorded
  as FU-2 rather than silently assumed.
- **Redaction breaking the authoring form — checked, covered.** The edit form pre-fills
  every field and resubmits it (`ActivityTypeUpdateIn` docstring, `activities.py:71-74`),
  and the authoring view sources its rows from exactly the list endpoint being redacted
  (`frontend/src/slices/activities/views/ActivityTypesView.vue:49`). The owner arm
  returning the real config is therefore load-bearing, not a nicety: a redacted owner would
  silently blank a type's `validator_config` on save. A non-owner member who reaches the
  view sees an empty config but cannot persist it — `PATCH` is `assert_project_owner`
  (`activities.py:314`) and returns 403. The build must not "simplify" the owner arm away.
- **Rollback**: the change is additive except for the list redaction. Reverting the
  redaction restores the old (leaky) behaviour without breaking clients; reverting the
  projection requires reverting the frontend together, since the panel would then have no
  schema source.

## 10. Acceptance Criteria

- [x] AC-1: Every regression test in §8 fails against current code and passes after the fix.
- [x] AC-2: A chatroom guest with no project role can open the Activity tab of a room with
  an active activity, see the Join button, open a session, and submit — the full
  participant flow, end to end. **Verified at test level, not live** — see D-3.
- [x] AC-3: No room-scoped response and no WebSocket payload contains `validator_config`
  under any activity type projection. Asserted structurally, not by string match.
- [x] AC-4: `GET /api/projects/{id}/activity-types` returns `validator_config` populated
  for a project owner and admin, and empty for every other caller; the membership gate is
  otherwise unchanged.
- [x] AC-5: The room-scoped type read is tenant-isolated: a type belonging to another
  project returns 404, never the row, for a caller who can read the room.
- [x] AC-6: With the WebSocket broadcast dropped (simulating a missed event), a participant
  who loads the room still renders the activity, via the activation read or the standalone
  room-scoped read. **Verified at test level, not live** — see D-3.
- [x] AC-7: The facilitator flow is unchanged — start dropdown lists the project's types,
  start and end still require room-creator capability.
- [x] AC-8: `pnpm run gen:api` regenerated; `pnpm run check:openapi-drift`, `pnpm lint`,
  `pnpm run typecheck`, `ruff check .`, and `mypy .` show no new findings. **`check:openapi-drift`
  itself could not execute** — see D-2.

## 11. SRS Delta

Analysis showed the SRS is silent on both points, which is why the defect was possible.
Append to chapter §30 after `[R30.24]`. **Applied to `REQUIREMENTS.md` on 2026-07-28 at
approval** (`REQUIREMENTS.md:2176-2177`); `/build` must not re-apply it.

```
- **[R30.25]** An `ActivityType`'s `validator_config` is confidential to Project Owners. It may hold answer keys and, once sealed validator credentials exist, secrets. Project-scoped read surfaces omit it for non-owner callers, and it is never exposed on any room-scoped surface or realtime payload.
- **[R30.26]** A room participant obtains the rendering contract of an activity type — identity, key, display name, and payload schema, and nothing else — through the room-access chain rather than through project membership. The active-activation read and the activation-started broadcast carry that same projection, and a room-scoped read of a single type serves the cases where the broadcast was missed. A guest who satisfies the room's access tier is a full activity participant.
```

## 12. Deviation Log

- **D-1 (environment: wiring/integration tests not runnable).** The Docker daemon is not
  reachable in this build environment (`docker ps` fails to connect), so `tests/wiring/*`
  (needs live Postgres/Redis/Vault) could not run. Gate 1 was satisfied with
  `pytest tests/unit -q` instead of the bare `pytest -q` the DoD lists — 6090 passed, 6
  pre-existing skips, none in the touched files. `pytest -q` was run once, after the fix:
  66 failures, all in `tests/wiring/*`, all `socket.gaierror`/connection-refused (DNS/TCP
  failures reaching Postgres/Redis/Vault) and none naming an activities file — consistent
  with an unreachable Docker daemon rather than a regression, but not confirmed against a
  pre-change baseline since that run was not repeated before the fix.
- **D-2 (environment: `check:openapi-drift` script not runnable).** The script invokes
  `bash` (resolves to `C:\WINDOWS\system32\bash.exe`, WSL), whose `python` is not on PATH,
  so the script itself fails before it can compare anything. Verified the equivalent by
  hand instead: regenerated `backend/openapi.json` via `python scripts/export_openapi.py`
  and the frontend client via `pnpm run gen:api`, then diffed both — the only changes are
  the intended contract (new route, `ActivityTypePublicOut`, `ActivityActivationOut.activity_type`)
  plus two pre-existing line-ending-only no-op diffs on `SessionOut.ts`/`AuthService.ts`
  unrelated to this task.
- **D-3 (environment: no live behavioral verification).** Step 5.4 and AC-2/AC-6 call for
  driving the guest flow live; the Docker dev stack is unreachable here so the app could
  not be launched. Confirmed by test instead: `test_room_scoped_type_read_allows_guest`
  (backend, a principal with `is_guest=True` and no project role reads the new endpoint),
  `ActivityPanel.test.ts`'s three participant-surface tests (Join renders without
  `listActivityTypes`, falls back to the room-scoped read, surfaces a fallback failure),
  and the pre-existing (unchanged, still-passing) session/submission tests confirming
  `open_activity_session`/`submit_activity` already gate on `ensure_can_send`, not project
  membership. Confirmed with the user (2026-07-28) as the accepted closure given the
  environment gap; re-verify live once a dev stack is reachable.
- **D-4 (self-audit fix beyond the literal Fix Design).** Item 10's `ActivityPanel.vue`
  fallback fetch (`ensureActiveTypeLoaded`) originally swallowed a failed room-scoped read
  with no user feedback and no protection against two overlapping fetches resolving out of
  order. Found in this build's own quality audit and self-audit; fixed by surfacing
  `errorMessage` on failure and adding a generation counter mirroring
  `useChatroomSocket.ts`'s existing `resyncActivation` pattern. Not a deviation from the
  design's intent, just a robustness gap the spec didn't anticipate.
- **D-5 (post-close-out `/code-review` pass).** The user ran a full-branch `/code-review`
  after this dossier was already `implemented`. It surfaced 6 findings; 4 were in this
  task's own diff and were fixed in a follow-up commit:
  - `_resolve_activation_type` propagated a transient `facade.get_type` failure uncaught,
    turning an already-committed activation start/end into a 500 for the facilitator (worse
    for end: the `activation.ended` broadcast had already fired before the failing read).
    Now degrades to `activity_type=None` and logs, matching the file's established
    post-commit best-effort pattern — the client's fallback room-scoped read recovers it.
  - `_dispatch_activation_started` built its payload (including `_type_public_out(...)`)
    outside the `try` that wraps the publish call, so a projection failure would propagate
    past what the function's own post-commit best-effort contract promises. Payload
    construction moved inside the `try`.
  - `ActivityPanel.vue`'s `ensureActiveTypeLoaded` (added by this task) never cleared
    `errorMessage` on a successful fetch, so one transient failure left a stale error banner
    on screen indefinitely even after the feature recovered. Now clears it on success.
  - The route-local `_is_project_owner` helper (added by this task) duplicated
    `assert_project_owner`'s admin-bypass + owner-check logic instead of reusing it.
    Extracted a shared non-raising `is_project_owner_or_admin` into `deps.py`;
    `assert_project_owner` now calls it too.
  All four fixed with regression tests, verified against the full backend/frontend gate
  suite (6092 backend unit tests, 890+ frontend tests, ruff/mypy/eslint/vue-tsc all clean).
  The other 2 findings (`backend/app/workers/tasks/prompt_assistant.py`) are in code this
  task never touched — pre-existing from prior session work, out of scope here, and not
  fixed; surfaced to the user as a separate matter.
  Also noted: the review's Efficiency finder subagent hung (0-byte output for over an hour);
  the user directed abandoning that one angle and proceeding with the other 7.

## 13. Follow-ups

- **FU-1 (server-side task ordering).** A submission is checked only against the type's
  schema and the room's active activation (`submission_service.py:93-99`); there is no
  prerequisite/unlock check, so a client can submit the last task of a multi-step lesson
  first. Out of scope here (a missing capability, not a deviation), but it is the next
  thing a task-based lesson will need. Recorded in
  `docs/assessments/ai-teacher-phase1-spec-review.md` §5.
- **FU-2 (payload_schema size bound on the realtime path).** Embedding the schema in the
  `activity.activation.started` payload makes the broadcast size a function of authored
  content. No cap is added here; add one if authored schemas grow.
- **FU-3 (submissions do not record the activity type version).** `ActivityType.version`
  bumps on behavioural edits (`type_service.py:131`) but `ActivitySubmission` stores no
  version (`contexts/activities/domain/models.py:103-124`), so a scored submission cannot
  be tied to the schema/validator revision that scored it. A research-data integrity gap,
  noticed while reading the same files; not a blocker for this fix.
- **FU-4 (`docs/tasks/BOARD.md` staleness).** The board lists
  `2026-07-22-activity-session-authz-and-validation` under "In progress" while its
  frontmatter is `implemented`. Corrected as part of this dossier's board update; flagged
  because other rows may have drifted the same way.
- **FU-5 (`_make_type` test-builder duplication).** `test_activity_type_edit.py` and
  `test_activities_services.py` already each carried their own near-identical `ActivityType`
  builder before this task; this task's two new test files
  (`test_activities_authz.py`, `test_activities_activation_projection.py`) added two more
  copies rather than extracting a shared factory, worsening pre-existing duplication.
  Found by this build's `check-quality` gate. Not fixed here — extracting a shared fixture
  is a test-infrastructure change with its own blast radius across 4 files, out of scope
  for a bugfix task.
