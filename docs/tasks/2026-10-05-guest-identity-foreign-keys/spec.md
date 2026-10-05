---
type: bugfix
status: approved
created: 2026-10-05
requirements: [R5.04, R30.01, R30.26, R30.39, R13.06]
depends_on: []
---

# Guest writes fail on foreign keys to `users`

## 1. Summary

An anonymous guest's `Principal.user_id` is a `guest_sessions.id`
(`backend/app/api/middleware/auth.py:103-109`), and only the `audit_logs.actor_user_id` foreign
key was removed when anonymous guests shipped
(`backend/alembic/versions/0096_drop_audit_logs_actor_fk.py:23-26`). Every other path that writes
the principal id into a column referencing `users.id` fails with an unhandled
`IntegrityError`, which surfaces as HTTP 500. For a classroom that admits students by guest link
this means a guest cannot submit an activity, mark a round finished, or attach a file, which are
the core student actions. Three registered-only endpoints also accept guest principals and fail
the same way or misreport the failure. A related agent-side defect stages an older member's
attachments into a turn that a guest's message should drive. This dossier fixes audit findings
F-1, F-2, F-23 and F-25 of `docs/audits/2026-10-05-guest-anonymous-session/findings.md`, plus the
two sibling endpoints that analysis confirmed share the cause.

## 2. Observed vs Expected

- **Observed**
  - Activity submit, completion toggle and open-session by a guest insert the guest id into
    `activity_sessions.subject_user_id` (`backend/contexts/activities/infrastructure/tables.py:77-82`,
    FK at `:80`) and `activity_submissions.producer_user_id` (`:165-170`, FK at `:168`); both
    reference `users.id` with `ON DELETE CASCADE` (`backend/alembic/versions/0049_activities.py:97-101,141-145`).
    The routes pass `principal.user_id` at `backend/app/api/v1/activities.py:1002-1015`,
    `:1076-1084` and `:1128-1142`; `session_repo.create_open`
    (`backend/contexts/activities/infrastructure/repositories/session_repo.py:143-169`) uses a
    targetless `ON CONFLICT DO NOTHING`, which does not cover a foreign-key violation. Result: 500.
  - Guest attachment upload (single-shot `backend/app/api/v1/attachments.py:69-94`; resumable
    `backend/app/api/v1/tus.py:196-210`, finalized at `tus_service.py:381-392`) writes the object to
    MinIO first (`attachment_service.py:146-151,192-208`) and then inserts
    `message_attachments.uploaded_by_user_id`, which references `users.id`
    (`backend/contexts/conversation/infrastructure/tables.py:298-303`; `0018_attachments_chatroom.py:40-48`).
    Result: 500 and an orphaned object until the `chat-uploads` 3-day lifecycle removes it.
  - The guest frontend never reaches that endpoint: `useChatroomAttachments.ts:42-48` returns early
    with `uploadNotReady` when no `projectId` resolves, and an anonymous guest cannot resolve one
    (the room read returns 403, audit F-3). The backend ignores the client's project for
    `chat_attachment` and derives it from the room (`tus.py:207-210`).
  - "I am finished" is disabled for guests: `ActivityPanel.vue:485` gates on `session.me?.id`,
    which is null for an anonymous guest.
  - Legacy enroll (`backend/app/api/v1/guests.py:43-59`) accepts any principal and inserts
    `chatroom_guests.user_id` (FK `users.id`, `0016_chatrooms.py:79-81`): 500 for a guest token.
  - `POST /api/keys` (`backend/app/api/v1/keys.py:189-215`) accepts any principal; `KeyService.upload`
    runs the outbound provider probe and Vault encryption before the `api_keys.owner_user_id`
    insert fails (FK `0005_api_keys.py:53-54`): 500 after side effects.
  - `POST /api/auth/google/link/start` (`backend/app/api/v1/auth.py:700-712`) accepts a guest; the
    callback's insert into `auth_identities.user_id` fails on the FK and is caught as
    `IntegrityError` (`backend/contexts/identity/application/auth_service.py:604-606`), so the guest
    is told the Google account is already linked elsewhere.
  - `latest_user_attachments` (`backend/contexts/conversation/interfaces/facade.py:666-679`) selects
    the newest message with `sender_type is SenderType.USER` (`:675`), while the transcript maps
    guest senders to role `user` (`backend/contexts/agents/application/runtime/transcript.py:73-77,135`)
    and the engine splices fallback attachments onto the newest `user`-role row
    (`turn_engine.py:2833-2834`). On a turn with no trigger message (silence wake, release,
    coalesced re-enqueue: `backend/app/workers/tasks/orchestration.py:293-302`,
    `backend/app/api/v1/observations.py:281`, `turn_engine.py:600-605`) after a guest posted, an
    older member's file is staged as if it belonged to the guest's message.
- **Expected**
  - [R30.26]: "A guest who satisfies the room's access tier is a full activity participant."
    Guest submit, completion and open-session succeed and are recorded as the guest's.
  - [R5.04] and the permission matrix: a guest may send in the room, which includes attachments.
  - The guest dossier (`docs/tasks/2026-09-04-guest-anonymous-session/spec.md`) designed guest
    identity to live outside `users` ("No FK to users -- this is the entire point", `:228`), left
    activity participation as OQ-2 "needs verification" (`:779-782`), and limited legacy enroll to
    registered users (AC-11, `:631-632`; D-6, `:817-823`).
  - Registered-only endpoints reject a guest principal with a clear 4xx before any side effect.
  - The fallback attachment resolver agrees with the transcript about which message is the newest
    human message.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How do the activity tables hold a guest subject and producer? | Drop the FKs on `activity_sessions.subject_user_id` and `activity_submissions.producer_user_id`, and add stored discriminators `subject_kind` and `producer_kind`. | Chosen over (a) dropping the FKs only, which leaves no SQL-level way to tell a guest from a user once the guest session is purged after 30 days, and (b) parallel guest columns as 0081 did for groups, which needs a third branch at about 20 sites (repos, services, codes, dashboard, export, agent context) and collapses every guest into one subject wherever a branch is missed. A discriminator plus an FK-less id is the house pattern for polymorphic human identity (`messages.sender_type` + `sender_id`, `0085_guest_sessions.py:37`; `conversation/infrastructure/tables.py:235-239`). |
| Q-2 | How does `message_attachments.uploaded_by_user_id` record a guest uploader? | Drop its FK and store the guest id in the same column. | NULL already means "agent artifact" (`attachment_repo.py:170-192,203-215`), so a guest cannot be stored as NULL. Keeping one column leaves the bind predicate `uploaded_by_user_id == sender` (`attachment_repo.py:144-154`, called from `message_service.py:166-177`) correct for guests with no change. Chosen over the canvas dual-column pattern (`created_by_guest_id`), which needs an `is_guest` branch in the bind and a second unbound index. Uploader kind is recoverable from the bound message's `sender_type`. |
| Q-3 | Which registered-only endpoints get a guest guard? | A new shared dependency `require_registered_principal` in `shared_kernel/auth/dependencies.py`, applied to legacy enroll, `POST /api/keys`, and `POST /api/auth/google/link/start`. All other routes stay with `check-security` (audit FU-1). | These three are the confirmed guest-reachable writes into FKs to `users` besides the activity and attachment columns (inventory of all 50 such columns, §6). One dependency is the single place the guest dossier's §10 asked for and never built. |
| Q-4 | Is the frontend in scope? | Yes: chat attachments no longer require a `projectId`, and the completion toggle is enabled for a guest session. | Without them the backend fix delivers nothing to a guest: the upload is stopped client-side and the toggle is disabled. |
| Q-5 | What happens to a hard-deleted user's activity rows once the FKs no longer cascade? | Preserve today's behavior exactly: `hard_delete_user` explicitly deletes sessions whose subject is that user (their submissions cascade by `session_id`) and submissions that user produced, in the same transaction. | Today `users.delete()` (`backend/contexts/identity/application/admin_service.py:508`) cascades through both FKs. Dropping the FKs without this would silently stop erasure. Changing what erasure removes (for example keeping group submissions a deleted proposer produced) is a policy question outside a bugfix. Decided as the conventional default; flagged at approval. |
| Q-6 | What does `require_registered_principal` return for a guest? | 403 with a dedicated problem type `auth/registered-account-required`. | Not 401: `axios.ts:193-211` treats an authenticated 401 as refresh-eligible and, with guest context set, would silently refresh the guest token and replay. Not the existing `GuestTokenInvalid` 404, which the landing page renders as "invalid link". A distinct type lets the client say "sign in with your account" later without another backend change. |
| Q-7 | Does this dossier depend on another? | `depends_on: []`. | The only non-closed dossiers on `BOARD.md` are `2026-07-19-large-artifacts-silently-dropped` (stale status; its fix has landed, and this task does not touch the agent-artifact path) and `2026-07-07-graphrag-two-axis-redesign` (no overlap). The sibling dossiers planned from the same audit (`guest-room-read-and-identity`, `research-export-data-shape`) consume `subject_kind`, so they should list this slug in their own `depends_on`, not the reverse. |

## 4. Reproduction

Preconditions: a project with a workspace and a room whose `allow_guest_links` is on; an activity
type opted in for the project; the room creator starts an activation of that type.

1. Open the room's guest link in a private window, enter a display name, and enter the room.
2. Open the Activity tab and submit any valid payload. Observe HTTP 500 on
   `POST /api/chatrooms/{id}/activity-submissions`; the backend log shows a
   `ForeignKeyViolation` on `activity_sessions`.
3. Observe that "I am finished" is disabled.
4. Pick a file with the composer's attach button. Observe the `uploadNotReady` toast and no request.
   With the request sent manually (`POST /api/chatrooms/{id}/attachments` with the guest bearer),
   observe 500 and a `ForeignKeyViolation` on `message_attachments`.
5. With the same guest bearer, `POST /api/keys` with any provider payload: observe 500, and in the
   egress log an outbound probe to the provider.

Deterministic. Steps 2, 4 and 5 are exactly what the db-tier tests in §8 encode.

## 5. Root Cause Analysis

1. The guest dossier chose to keep guest identity outside `users` and represent it by a
   `guest_sessions.id` in `Principal.user_id` (`auth.py:103-109`;
   `backend/shared_kernel/auth/permissions.py:104-110`).
2. That choice requires every column receiving `Principal.user_id` on a guest-reachable path to
   accept a non-`users` id. Migration 0096 adapted exactly one such column, the one the guest
   dossier's tests happened to hit (`0096_drop_audit_logs_actor_fk.py:3-9`).
3. No inventory of guest-reachable writes was made, and the guard the dossier proposed for
   registered-only routes (`spec.md:595-597`) was never implemented, so guest principals reach
   routes that assume a user (`keys.py:200-203` states that assumption in its docstring).
4. The tests that claim guest coverage mock every repository (`backend/tests/unit/test_activities_services.py:2142-2239`,
   whose docstring at `:2143-2150` concedes it cannot see FK violations), and the planned db-tier
   and e2e guest tests were never written.

**Root cause:** link 2. The schema still encodes "every human actor is a `users` row" on the
columns guests write, contradicting the identity model the guest feature adopted. Links 3 and 4
are aggravating factors: the missing guard widened the reachable set, and the mocked tests hid it.

The frontend gates (link to Observed bullets 3 and 4) are independent defects with a shared
assumption: "the viewer is `session.me`", which is null for a guest.

F-25 has a separate, simpler cause: the fallback resolver predates the `guest` sender type and
filters on `USER` only (`facade.py:675`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Every anonymous guest in every room: activity participation and attachments
  are impossible. No bad data was persisted, because every failing insert rolled back; the only
  residue is MinIO objects from failed uploads, which the bucket lifecycle already expires
  (`backend/smap/bootstrap/minio_init.py:143-146`). The key-upload path also performed outbound
  provider probes and Vault Transit operations for anonymous callers.
- **Sibling suspects.** All 50 columns with a live FK to `users.id` at migration head 0097 were
  checked for guest reachability:
  - **Confirmed, in scope:** `activity_sessions.subject_user_id`,
    `activity_submissions.producer_user_id`, `message_attachments.uploaded_by_user_id`,
    `chatroom_guests.user_id` (legacy enroll), `api_keys.owner_user_id`,
    `auth_identities.user_id` (Google link; misreported rather than 500).
  - **Cleared, with the guard that stops a guest:**
    - `activity_activations.started_by_user_id`: `ensure_room_creator` (`activities.py:909`).
    - `activity_group_proposals.proposer_user_id`, `activity_group_proposal_votes.user_id`:
      `NotAGroupMember` and `may_vote` before any insert (`group_proposal_service.py:244-250,630-634`).
    - `message_edits.edited_by_user_id`: self-edit requires `sender_type is SenderType.USER`
      (`message_service.py:279`), so a guest edit is refused before the insert (see FU-2).
    - Canvas objects, comments, snapshots, templates: guest identity is written to
      `created_by_guest_id` or NULL (`backend/app/api/v1/canvas.py:231-236`).
    - Tenancy, invites, knowledge, agents, skills, prompt studio, notifications, chatroom settings,
      observations, workflow runs: role, email-verified, ownership or creator checks deny a guest,
      whose role set is always empty.
  - **Adjacent, not an FK:** `GET`/`PATCH /api/auth/me` return an explicit 500 "User profile not
    found" for a guest (`auth.py:518-542`); recorded as FU-1.

## 7. Fix Design

### 7.1 Migration `0098_guest_identity_columns`

Revision number taken from `alembic heads` at build time, not from this dossier.

- `activity_sessions`: add `subject_kind text NOT NULL DEFAULT 'user'`; backfill
  `subject_kind = 'member_group'` where `subject_member_group_id IS NOT NULL`; add
  `CHECK (subject_kind IN ('user','guest','member_group'))` and
  `CHECK ((subject_kind = 'member_group') = (subject_member_group_id IS NOT NULL))`. The existing
  exactly-one-subject CHECK (`0081_group_activity_submissions.py:71,90-94`) stays.
- `activity_submissions`: add `producer_kind text NOT NULL DEFAULT 'user'` with
  `CHECK (producer_kind IN ('user','guest'))`.
- Drop the FKs on `activity_sessions.subject_user_id`, `activity_submissions.producer_user_id` and
  `message_attachments.uploaded_by_user_id` with `DROP CONSTRAINT IF EXISTS`, as 0096 did. The
  constraint names are expected to follow `shared_kernel/db/__init__.py:23`
  (`fk_<table>_<column>_users`); the db-tier schema test in §8 asserts the constraints are actually
  gone, so a wrong name fails the build instead of passing silently.
- Text plus CHECK, not a PG ENUM, following the activities context's newer convention
  (`tables.py:33,279-281`) and avoiding the ORM/ENUM type-match hazard.
- `downgrade()` drops the CHECKs and columns and recreates the three FKs; it fails if guest rows
  exist, which is correct and documented in the migration docstring.
- `tables.py`: remove the three `sa.ForeignKey(...)` declarations and add the two columns and CHECKs
  verbatim, so the table definitions match the migration.

### 7.2 Activities write path

- Domain: `SubjectKind` (`backend/contexts/activities/domain/models.py:48-57`) gains `GUEST`;
  `ActivitySession.subject_kind` becomes a stored field instead of being derived (`:216-248`);
  `ActivitySubmission` gains `producer_kind`.
- Routes (`activities.py:1002-1015,1076-1084,1128-1142`): the kind is `guest` when the subject or
  producer id is the caller's own id and `principal.is_guest`; otherwise `user`. A guest naming a
  different subject is already refused by `_ensure_subject_is_caller` (`session_service.py:43-50`).
- Services and repos thread the kind to `session_repo.create_open` and `submission_repo.insert`
  (`submission_repo.py:171,189`), and map it back in `_row_to_session` / `_row_to_submission`.
  Read queries keep keying on `subject_user_id`; uniqueness indexes are unchanged.
- `ActivitySessionOut` (`activities.py:218-237`) already exposes `subject_kind`; it gains the
  `guest` value. The workflow signal (`submission_service.py:549-592`) emits the stored kind.
- Subject codes are unchanged in this task: a guest keeps the `u:` code (`subject_code.py:30-31`),
  which is also what drafts already assign guests (`draft_tools.py:201`). Whether guests get a
  distinct prefix is for the research-export dossier, which can now branch on the stored kind.

### 7.3 Erasure

`AdminService.hard_delete_user` (`admin_service.py:470-526`) calls a new
`ActivitiesFacade.purge_user_rows(user_id)` before `users.delete()` in the same transaction. It
deletes `activity_sessions` where `subject_kind = 'user' AND subject_user_id = :id` (their
submissions follow by the `session_id` cascade, `0049_activities.py:124-128`) and
`activity_submissions` where `producer_kind = 'user' AND producer_user_id = :id`. This reproduces
today's cascade exactly (Q-5). Cross-context call through a facade has a precedent in the same
file (`admin_service.py` imports `NotificationFacade`).

### 7.4 Attachments

No service change: the guest id is written to `uploaded_by_user_id` and the existing bind predicate
matches it. Agent artifacts keep `NULL`.

### 7.5 Registered-only guard

`require_registered_principal` in `backend/shared_kernel/auth/dependencies.py`, next to
`current_principal` (`:49-52`): returns the principal, or raises a 403 problem of type
`auth/registered-account-required` when `principal.is_guest`. Applied to `enroll_guest`
(`guests.py:43-59`), `upload_key` (`keys.py:189-215`) and `google_link_start` (`auth.py:700-712`).
The `keys.py:200-203` docstring is corrected.

### 7.6 Fallback attachments

`latest_user_attachments` selects the newest message whose `sender_type` is `USER` or `GUEST`.

### 7.7 Frontend

- `frontend/src/shared/transport/tus.ts:22,57`: `projectId` becomes optional and is omitted from
  the metadata when absent; `useChatroomAttachments.ts:41-83` stops requiring it for
  `chat_attachment` (the backend derives the project from the room, `tus.py:207-210`).
- `ActivityPanel.vue:485`: the completion toggle is enabled when `session.me` is set or a guest
  session is active (`isGuestSession` from `frontend/src/shared/transport/axios.ts:81-84`).
- Regenerate the OpenAPI types (`pnpm run gen:api`) for the `subject_kind` value and the new
  problem type.

### 7.8 Existing debt in the touched files (record, do not imitate, do not silently fix)

- `TestGuestSubmission` (`test_activities_services.py:2142-2239`) uses the guest id as
  `started_by_user_id`, which no real path produces; it is corrected as part of §8, not extended.
- `keys.py:200-203` claims a matrix decision runs; it does not. Fixed only as far as the docstring.
- `backend/CLAUDE.md` says migrations run 0000-0079; head is 0097. Out of scope (FU-4).

### 7.9 Patterns to follow

- Migration shape and `IF EXISTS` drops: `0096_drop_audit_logs_actor_fk.py`; CHECK naming and
  backfill-then-constrain: `0081_group_activity_submissions.py`.
- Discriminator plus FK-less id: `messages.sender_type`/`sender_id`.
- Route dependency style: `current_principal`, `require`, `require_membership` in
  `shared_kernel/auth/dependencies.py`.
- SoC: routes call facades; the identity application layer reaches activities only through
  `ActivitiesFacade`.

### 7.10 Reuse inventory

- `_raise_forbidden` and the problem+json helpers in `shared_kernel/auth/dependencies.py:173-184`.
- `isGuestSession` (`axios.ts:81-84`) for the frontend guest check.
- db-tier fixtures `sessionmaker`, `project`, `project_id` (`backend/tests/integration/conftest.py:40-153`)
  and the route-driving pattern with `httpx.ASGITransport` and dependency overrides in
  `backend/tests/integration/test_message_search_determinism.py:211-254`.
- Activities db-tier exemplars: `test_group_activity_constraints_db.py`,
  `test_activity_session_activation.py`.

### 7.11 Security considerations

- The registered-only guard removes an anonymous path to outbound provider probes and Vault
  Transit operations (`key_service.py:114-123`).
- Dropping three FKs removes referential validation that an admin-supplied
  `body.subject_user_id` names a real user (`activities.py:1009,1082,1135`). A bad id previously
  produced a 500; it now produces an orphan subject. Acceptable: the caller is an admin acting on
  their own room, and the value never crosses tenants.
- No change to room access: every guest write still passes `resolve_room_access` and
  `ensure_can_send`.

## 8. Regression Test Plan

Written first, failing against current code:

1. `backend/tests/integration/test_guest_identity_writes_db.py` (`pytestmark = pytest.mark.db`).
   Fixtures insert a real `guest_sessions` row (`chatroom_id`, `display_name`, unique
   `refresh_token_hash`; `0085_guest_sessions.py:39-68`) in a room with `allow_guest_links`.
   - Guest submit through the route with a guest `Principal` override creates a session with
     `subject_kind='guest'` and a submission with `producer_kind='guest'`. Fails today with
     `ForeignKeyViolation`.
   - Guest completion toggle and open-session succeed. Fail today the same way.
   - Guest attachment create followed by `MessageService.send(..., attachment_ids=...,
     sender_type=GUEST)` binds the attachment. Fails today at the insert.
   - `bind_agent_artifacts` still binds only `NULL`-uploader rows.
   - Schema: no FK from the three columns to `users`; the kind CHECKs reject
     `subject_kind='member_group'` with a NULL group id and an unknown kind; pre-existing group rows
     were backfilled to `member_group`.
   - `hard_delete_user` removes a user's own sessions and produced submissions and leaves a guest's
     rows untouched.
   - Teardown removes guest-actor audit rows under `SET ROLE smap_audit_retention`, as the
     `project` fixture does for owner rows.
2. `backend/tests/unit/test_require_registered_principal.py`: the dependency returns the principal
   for a user and raises the 403 problem for a guest; route tests for enroll, key upload and Google
   link start assert 403, no `chatroom_guests`/`api_keys` write, and no probe call (probe mocked).
3. Unit test for `latest_user_attachments`: newest human message is a guest's with an attachment,
   older member message has another; the guest's is returned. Fails today.
4. Frontend: `useChatroomAttachments` uploads with an undefined `projectId` (fails today on the early
   return); `ActivityPanel` enables the completion toggle when a guest session is active.

The db-tier tests run in CI's `backend-db` job (`.github/workflows/ci.yml:138-194`); local Windows
runs are not authoritative.

## 9. Risks and Rollback

- **Erasure regression.** If §7.3 is incomplete, hard-deleting a user leaves their activity rows.
  Pinned by the db-tier erasure test.
- **Workflow signal contract.** `subject_kind` can now be `guest`. No existing signal carries a
  guest (guest submissions never succeeded), so this is new behavior, not a change; documented in
  `docs/workflow.schema.md` alongside the existing values.
- **Downgrade.** Recreating the FKs fails once guest rows exist. Rollback after guest data is
  written means deleting guest activity rows first; the migration docstring says so.
- **Orphan subject ids.** A purged guest session leaves a dangling id in activity rows, which is
  intended: research data outlives the 30-day guest cleanup and follows `retain_until`, as a
  deleted member group's id already does (`0081_group_activity_submissions.py:19-23`).
- **Rollback path.** Revert the code commits and downgrade 0098 before any guest row is written;
  after that, keep the schema and revert only the code.

## 10. Acceptance Criteria

- [ ] AC-1: the db-tier guest-submit test fails before the fix and passes after; the session and
  submission carry `subject_kind='guest'` and `producer_kind='guest'`.
- [ ] AC-2: guest completion toggle and open-session succeed against a real database.
- [ ] AC-3: a guest uploads an attachment and binds it to its own message against a real database;
  agent artifact binding is unchanged.
- [ ] AC-4: the schema test shows no FK from `activity_sessions.subject_user_id`,
  `activity_submissions.producer_user_id` or `message_attachments.uploaded_by_user_id` to `users`;
  the kind CHECKs hold; existing group sessions read `member_group`.
- [ ] AC-5: hard-deleting a user removes exactly the activity rows the old cascade removed and no
  guest rows.
- [ ] AC-6: legacy enroll, key upload and Google link start return 403
  `auth/registered-account-required` for a guest, with no row written and no provider probe;
  registered-user behavior is unchanged.
- [ ] AC-7: `latest_user_attachments` returns a guest message's attachments when that message is the
  newest human message.
- [ ] AC-8: the chat attachment upload proceeds without a `projectId`; the completion toggle is
  enabled for a guest session.
- [ ] AC-9: OpenAPI types regenerated; backend and frontend lint, typecheck, tests and build pass in
  CI, including `backend-db`.
- [ ] AC-10: on a running stack, a guest entering by link submits an activity, toggles completion
  and attaches a file, each without error. Left unticked if no stack is available, with the reason.

## 11. SRS Delta

Amend [R30.39] (first sentence), to state what [R30.26] already requires:

> - **[R30.39]** An `ActivitySession`'s subject is either one person (a registered user or an
>   anonymous guest session, [R5.04]) or one project Member Group ([R13.28]), never both and never
>   neither. A group session is created only by an accepted group proposal ([R30.41]); the
>   per-subject rules of [R30.01] apply unchanged with the group as the subject, so a group has at
>   most one session per activation and its own monotonic attempt sequence. A member's own
>   individual session for the same activation is separate and unaffected.

Regenerate `docs/traceability.csv` with `python scripts/traceability.py` after applying.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** `GET` and `PATCH /api/auth/me` return an explicit 500 "User profile not found" for a
  guest (`auth.py:518-542`); they should use `require_registered_principal` or return a guest
  profile. Not an FK write, so outside this dossier.
- **FU-2.** Guests cannot edit their own messages: self-edit requires `SenderType.USER`
  (`message_service.py:279`) while the route's `is_author` accepts `guest` (`messages.py:481`), and
  `message_edits.edited_by_user_id` references `users.id` with `RESTRICT` (`tables.py:257-262`).
  Needs an intent decision ([R13.21] says "Users").
- **FU-3.** The remaining `current_principal`-only routes should be swept for guest reachability as
  part of the `check-security` work in audit FU-1, now that `require_registered_principal` exists.
- **FU-4.** `backend/CLAUDE.md` states migrations run 0000-0079; head is 0097.
