---
type: bugfix
status: approved
created: 2026-10-05
requirements: [R6.12, R13.06, R13.06a, R13.06b, R15.24]
depends_on: [2026-10-05-guest-frontend-session-lifecycle]
---

# Guest session backend hardening: audit shape, join cap race, dead rooms, orchestration 403s, refresh rotation

## 1. Summary

Five backend defects in the anonymous guest feature, each small, none visible in a normal lesson,
all reachable in a classroom: guest audit rows split across two incompatible shapes, so an
investigation of one guest finds half the trail (F-14); a class joining at once can exceed the
per-room guest cap (F-15); a link to a room whose workspace or project was deleted still enrolls a
guest, and a socket in such a room is never closed (F-16); orchestration reads answer a guest 403
where a 404 or a filtered list is required (F-17); and two tabs refreshing the same guest session at
once can both succeed (F-21). This dossier fixes findings F-14, F-15, F-16, F-17 and F-21 of
`docs/audits/2026-10-05-guest-anonymous-session/findings.md`, plus a same-path duplicate-insert race
and a members-affecting socket fail-open found during analysis (§6).

## 2. Observed vs Expected

Citations are against `main` at `c5114a13`; the audit's `guest_session_service.py` and
`guest_session_repo.py` line numbers have drifted by up to 15 lines with no behavioral change.

- **F-14.** `message.sent` (`backend/contexts/conversation/application/message_service.py:178-194`)
  sets `actor_user_id` to the guest id but writes no `guest` key; the session events
  (`backend/contexts/conversation/application/guest_session_service.py:128-143,174-189,226-239`) set
  `actor_user_id=None` with `guest: true` and `guest_session_id` in metadata; `attachment.uploaded`
  (`attachment_service.py:236-253`) and `activity.submitted` (`submission_service.py:335-349`) carry the
  guest id with no `guest` key; the refreshed row has no `actor_ip` or `request_id` because the refresh
  route takes no request context (`backend/app/api/v1/guests.py:139-154`); a rename writes no row.
  **Expected**: guest dossier AC-9 (`docs/tasks/2026-09-04-guest-anonymous-session/spec.md:627-628`,
  §6 at `:365-369`): every guest action writes `actor_user_id = guest_session_id` and metadata
  `{"guest": true, "chatroom_id": ...}`.
- **F-15.** `count_active_for_update`
  (`backend/contexts/conversation/infrastructure/repositories/guest_session_repo.py:110-125`) puts
  `FOR UPDATE` on existing rows only, so with no rows nothing is locked and concurrent inserts are never
  blocked; the service counts (`guest_session_service.py:156-158`) then inserts (`:161-166`). The
  `browser_id` resume lookup (`:112`) runs before any lock and `(chatroom_id, browser_id)` is not unique
  (`backend/alembic/versions/0085_guest_sessions.py:70-76`), so two tabs can insert twice, after which
  `.one_or_none()` (`guest_session_repo.py:86`) raises on every resume. **Expected**: [R13.06a] caps
  active guests per room; guest dossier AC-4.
- **F-16.** The guest service checks only `chatrooms.deleted_at` (create `:98-100`, refresh
  `:206-208`; `chatroom_repo.py:120-130`); workspace and project soft deletes do not cascade to rooms
  (`workspace_service.py:135-157`, `project_service.py:204-242`). The socket's pre-accept handler
  catches only `ChatroomNotFound` and `ForbiddenInRoom` (`backend/app/api/ws/chatroom.py:77-79`): a
  deleted project closes as 4403, which the client shows as "guest access disabled", and a deleted
  workspace raises uncaught. Mid-socket, the watchdog's `authorize` (`chatroom.py:263-277`) has the
  same gap and the watchdog swallows the error and retries (`backend/shared_kernel/realtime/connection.py:388-394`),
  so a socket stays open indefinitely after its workspace is deleted, for members as well as guests.
  Pre-accept closes also reach the browser as 1006, losing the code (`connection.py:220-224`).
  **Expected**: [R6.12] (a deleted room's links stop working); guest dossier §6 Phase 2 invalid-link
  state.
- **F-17.** `_room_readable` (`backend/contexts/conversation/application/access.py:346-350`) catches
  only `ChatroomNotFound` and `WorkspaceNotFound`; the guest branch raises `ForbiddenInRoom` for any
  other room (`:145-146`), mapped to 403. Single-record orchestration reads
  (`backend/app/api/v1/orchestration.py:90-123`, used at `:282,345,374,441`) and listings
  (`filter_readable_by_room` at `:312,408,450`) therefore answer a guest 403 or fail the whole list.
  **Expected**: [R15.24] (`REQUIREMENTS.md:911`): "Listings omit records the caller may not read
  rather than refusing the whole request, and disclose nothing about what was omitted."
- **F-21.** Refresh reads by hash (`guest_session_repo.py:89-95`, service `:212-215`) then updates by
  id with no compare-and-swap (`guest_session_repo.py:139-144`, service `:218`); two concurrent refreshes
  with the same cookie can both succeed. **Expected**: [R13.06b] single-use rotation.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which audit rows get the AC-9 shape? | Every row a guest causes: `message.sent`, session created, resumed, refreshed, renamed (new row), `attachment.uploaded`, `activity.submitted`. No admin-UI filter in this dossier. | Requester's choice. One investigation query (`actor_user_id = :gsid`) then finds the whole trail. Rows already written stay in the old shapes: `audit_logs` is append-only (`backend/alembic/versions/0004_audit.py:1-3,73-89`), recorded as D-n in the guest dossier's log at build time. |
| Q-2 | How is the shape applied? | A field on `AuditEvent` naming the actor as a guest (and its room), folded into metadata by `emit()`; set from the acting principal, never from the subject. | One place owns the shape. An admin acting on a guest's resource stays the actor with no guest tag (`backend/tests/integration/test_guest_identity_writes_db.py:249`). |
| Q-3 | What does a room whose workspace or project was deleted look like? | Joining or refreshing: the invalid-link 404 (`ChatroomNotFound`). A connected client: the socket closes with 4404 and the room shows "this room no longer exists" (new state, members included). | Requester's choice. 4404 is the existing not-found close convention (`backend/app/api/ws/workflow_runs.py:39`, `canvas.py:98`). |
| Q-4 | Accept-then-close for socket refusals? | Yes for 4403 and 4404, where the subprotocol is known (exemplars `connection.py:225-226`, `canvas.py:124-125`); 4401 stays pre-accept. | Gives the browser the code the session-lifecycle dossier's banners key on. 4401 has no parsed auth to echo a subprotocol from (`backend/shared_kernel/auth/ws_auth.py:93-103`) and would change behavior for every user. |
| Q-5 | How is the join cap made atomic? | `advisory_xact_lock(f"guest-join:{chatroom_id}")` before the `browser_id` lookup, then a plain count; `count_active_for_update` is deleted. `find_by_browser_id` tolerates existing duplicates by taking the most recently seen row. | Same pattern as the orchestration sub-agent cap (`backend/contexts/orchestration/infrastructure/repositories.py:608-618`, helper `backend/shared_kernel/db/advisory_lock.py:25-30`). A row lock on `chatrooms` would contend with `FOR SHARE` reads and room edits (`chatroom_repo.py:200-221`). A unique index would need a migration and a dedupe of existing rows; the lock prevents new duplicates. |
| Q-6 | Refresh rotation? | One statement: `UPDATE guest_sessions SET refresh_token_hash=:new, last_seen_at=now() WHERE refresh_token_hash=:old AND chatroom_id=:cid RETURNING id, display_name`; zero rows is `GuestTokenInvalid`. The links-off and liveness checks follow it in the same transaction, so a later refusal rolls the rotation back. | Under READ COMMITTED the loser re-checks the predicate after the winner commits and matches nothing. Keeps the session-lifecycle dossier's ordering (cookie before links, its §7.5 amendment). The losing tab shows the expired banner, accepted there (§9). |
| Q-7 | Orchestration reads for a guest of another room? | `_room_readable` returns False for a guest whose token names another room (equivalently, catches `ForbiddenInRoom`). | Single reads then answer the existing 404, listings omit the row ([R15.24]). |
| Q-8 | Does this depend on another dossier? | `depends_on: [2026-10-05-guest-frontend-session-lifecycle]`. | That dossier changes the same `guest_session_service.py` error paths (create, refresh, ticket) and adds the client close-code handling this dossier's 4404 extends; its Q-8 asks for this ordering. |

## 4. Reproduction

1. **F-15**: with the cap at 1 and no guests, send two session-create requests for the same room
   concurrently: two sessions exist.
2. **F-16**: delete the project of a room with guest links on; open the old link and enter a name:
   the session is created, then every request fails. With a member connected to a room whose
   workspace is deleted, the socket stays open.
3. **F-17**: as a guest of room A, `GET` an approval of room B: 403 instead of 404.
4. **F-21**: send two refresh requests with the same cookie concurrently: both return 200.
5. **F-14**: as a guest, join, post and upload; query `audit_logs` by `actor_user_id = <guest session
   id>`: the session rows are missing, and the message and attachment rows lack `guest: true`.

## 5. Root Cause Analysis

1. Each guest audit call site chose its own actor and metadata when the guest feature landed; the
   session events kept `actor_user_id=None` from a period when the actor FK forbade non-users
   (dropped in migration 0096), and nothing owns the shape. **F-14.**
2. The cap's lock was written against existing rows, which locks nothing in the case that matters
   (an empty or nearly empty room). **F-15** and the duplicate `browser_id` insert.
3. "Room is live" is checked as "chatroom row not deleted" on the guest paths and the socket, while
   liveness actually spans room, workspace and project; the socket handlers also catch a fixed list of
   exceptions rather than "not readable". **F-16.**
4. `_room_readable` predates the guest branch's `ForbiddenInRoom`. **F-17.**
5. Rotation is a read followed by an unconditional write. **F-21.**

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** F-15 and F-21 bite exactly when a class joins or wakes laptops together; F-16 after
  a course ends and links are reused; F-17 on orchestration surfaces only; F-14 on every audit
  investigation of a guest.
- **Sibling suspects.**
  - Duplicate `(chatroom_id, browser_id)` inserts on concurrent first joins: **confirmed**, same lock
    (Q-5).
  - Mid-socket fail-open after a workspace delete, members included: **confirmed**, in scope (Q-3).
  - User refresh rotation: the user path is also not atomic (`backend/shared_kernel/auth/tokens.py:105,127-139`;
    the DB mirror ignores its rowcount, `backend/contexts/identity/infrastructure/repositories.py:372-387`):
    **confirmed**, out of scope (FU-1).
  - `GuestTokenInvalid` (404) reused for principal mismatches (`guests.py:190-191,240-241,247-248`;
    guest dossier FU-16): **confirmed**, out of scope (FU-2).

## 7. Fix Design

### 7.1 Audit shape (F-14)

- `backend/shared_kernel/audit.py`: `AuditEvent` gains an optional actor-is-guest marker carrying the
  guest's room id; `emit()` merges `{"guest": true, "chatroom_id": ...}` into metadata when set.
- Call sites set `actor_user_id = guest_session_id` and the marker from the acting principal:
  `message_service.py:178-194` (`sender_type` is in scope, `:151`), `attachment_service.py:236-253`,
  `submission_service.py:335-349`, and the session events in `guest_session_service.py`, which keep
  `guest_session_id` in metadata for existing queries.
- New `guest.session.renamed` row from `update_display_name` when the name changed.
- The refresh route takes `current_context` and passes `remote_ip` and `request_id`.

### 7.2 Join cap and duplicates (F-15)

`create_or_resume` takes `advisory_xact_lock(db, f"guest-join:{chatroom_id}")` before
`find_by_browser_id`; counts with `count_active`; `count_active_for_update` is removed.
`find_by_browser_id` orders by `last_seen_at DESC` and takes the first row.

### 7.3 Live room (F-16)

- One conversation-application helper, `ensure_room_live(chatroom_id)`, checks room, workspace
  (`WorkspaceRepository`) and project (`TenancyFacade.get_project`, as `access.py:156` does) and raises
  `ChatroomNotFound` for any miss. Used by guest create, refresh and the ticket route (the last
  added by the session-lifecycle dossier).
- `backend/app/api/ws/chatroom.py`: the pre-accept handler catches `WorkspaceNotFound` alongside
  `ChatroomNotFound`, and for a known subprotocol accepts then closes with 4404 (not found) or 4403
  (forbidden). The watchdog's `authorize` result becomes a small outcome (allowed, forbidden, gone)
  so the context-free `connection_loop` (`connection.py:207-208`) closes with 4403 or 4404 instead of
  retrying forever on an exception.
- Frontend (`ChatroomView.vue` close handler as reworked by the session-lifecycle dossier): 4404 shows
  a new "this room no longer exists" state for every viewer and stops reconnecting; new i18n key in
  both locales.

### 7.4 Orchestration reads (F-17)

`_room_readable` returns False when `principal.is_guest and principal.chatroom_id != chatroom_id`.

### 7.5 Rotation (F-21)

`GuestSessionRepository.rotate_refresh(old_hash, new_hash, chatroom_id)` executes the single
`UPDATE ... RETURNING` of Q-6; `refresh` calls it first, then `ensure_room_live` and the links-off
check, all in the route's transaction.

### 7.6 Quality notes

- **Existing debt (record, do not imitate).** The guest dossier's planned integration tests
  (`tests/integration/audit/test_guest_audit.py`, `tests/integration/conversation/test_guest_session_service.py`,
  its `spec.md:672-680`) were never written; coverage is mocked unit tests. Audit metadata shapes are
  chosen per call site. `connection_loop`'s `authorize` returns a bare bool.
- **Patterns to follow.** Advisory locks via `shared_kernel/db/advisory_lock.py`; rowcount or
  `RETURNING` compare-and-swap as in `backend/contexts/conversation/infrastructure/repositories/workspace_repo.py:81-98`;
  accept-then-close as in `canvas.py:124-125`; db-tier race tests as in
  `backend/tests/integration/test_instruct_terminal_state_race.py:151-186`.
- **Reuse inventory.** `advisory_xact_lock`, `count_active`, `WorkspaceRepository.get`,
  `TenancyFacade.get_project`, `ChatroomNotFound`/`WorkspaceNotFound` mappings
  (`backend/contexts/conversation/interfaces/error_mapping.py:14-23`), `audit.emit`, the guest fixtures in
  `test_guest_identity_writes_db.py` (audit teardown via `SET ROLE smap_audit_retention`, `:42,124-127`).

### 7.7 Security considerations

- Rotation becomes strictly single-use; a replayed refresh cookie cannot mint a second token.
- `ensure_room_live` maps every miss to the same invalid-link 404, so a deleted workspace or project is
  indistinguishable from a bad link ([R13.32]).
- Closing sockets in rooms whose workspace was deleted removes a fail-open that kept members and guests
  receiving room events after deletion.
- Audit rows gain no new personal data: the guest session id and room id are already present in some
  rows.

## 8. Regression Test Plan

Written first, failing against current code:

1. db tier (`pytest -m db`):
   - F-15: two concurrent `create_or_resume` calls under a cap of 1 in an empty room produce one
     session and one `GuestCapReached`; two concurrent first joins with the same `browser_id` produce
     one session.
   - F-21: two concurrent refreshes with the same cookie produce one success and one
     `GuestTokenInvalid`.
   - F-14: after a guest joins, posts, uploads, renames and refreshes, `actor_user_id = :gsid AND
     metadata @> '{"guest": true}'` returns all six rows; an admin acting on a guest's attachment is
     the actor with no guest tag.
2. Unit: guest create, refresh and ticket for a room whose workspace or project is deleted raise
   `ChatroomNotFound`; the chatroom socket handler closes 4404 (deleted workspace or project) and 4403
   (forbidden) after accept; the watchdog closes 4404 when `authorize` reports gone; `_room_readable`
   is False for a guest of another room, and the orchestration list omits that row.
3. Frontend: a 4404 close shows the room-gone state and stops reconnecting.

## 9. Risks and Rollback

- **Advisory lock contention.** One lock per room, held for the join transaction only; a class
  joining at once serialises its inserts by milliseconds.
- **Socket close behavior change for members.** A member in a room whose workspace was deleted is now
  disconnected with "room no longer exists" instead of staying on a dead room; intended.
- **Mixed audit shapes.** Rows written before this ships keep their old shapes (Q-1).
- No migration. Rollback: revert the commits.

## 10. Acceptance Criteria

- [ ] AC-1: every audit row caused by a guest has `actor_user_id = guest_session_id` and metadata with
  `guest: true` and `chatroom_id`; rows caused by others acting on guest resources carry no guest tag.
- [ ] AC-2: concurrent joins never exceed the per-room cap, and concurrent first joins with one
  `browser_id` create one session (db tier).
- [ ] AC-3: a guest link, refresh or ticket for a room whose workspace or project is deleted answers
  the invalid-link 404; a connected client (guest or member) is closed with 4404 and sees "this room no
  longer exists".
- [ ] AC-4: socket refusals for a known subprotocol reach the browser with 4403 or 4404, not 1006.
- [ ] AC-5: a guest's orchestration reads for another room answer 404 (single record) or omit the row
  (listings).
- [ ] AC-6: two concurrent refreshes with one cookie yield exactly one success (db tier).
- [ ] AC-7: backend and frontend lint, typecheck, tests (db tier included) and build pass in CI.

## 11. SRS Delta

None. [R13.06a], [R13.06b], [R6.12] and [R15.24] already state the intended behavior; the audit shape
is the guest dossier's AC-9.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** The user refresh rotation is not atomic either: `rotate_session` reads outside `MULTI`
  without `WATCH` (`backend/shared_kernel/auth/tokens.py:105,127-139`) and the DB mirror ignores its
  rowcount (`backend/contexts/identity/infrastructure/repositories.py:372-387`).
- **FU-2.** Guest dossier FU-16: principal mismatches on guest routes answer `GuestTokenInvalid` 404
  (`guests.py:190-191,240-241,247-248`), which blocks re-authentication flows; give them their own
  401-class problem.
- **FU-3.** An admin audit filter for guest activity (Q-1 left it out).
- **FU-4.** Existing duplicate `(chatroom_id, browser_id)` rows, if any, are tolerated but not removed;
  a dedupe plus a unique index would need a migration.
