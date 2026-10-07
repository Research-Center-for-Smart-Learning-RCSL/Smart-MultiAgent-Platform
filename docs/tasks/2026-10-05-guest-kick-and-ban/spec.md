---
type: feature
status: implemented
created: 2026-10-05
requirements: [R5.04, R6.12, R6.13, R13.06, R13.07, R13.33, R17.01]
depends_on: [2026-10-05-guest-frontend-session-lifecycle, 2026-10-05-guest-session-backend-hardening, 2026-10-05-guest-sender-marking]
---

# Remove, ban and rotate: moderating a single anonymous guest

## 1. Summary

A teacher whose public-link room is disrupted by one anonymous guest today has two options: turn
guest links off, which ejects every guest and readmits the disruptor when turned back on, or delete
the room. This feature lets those who manage a room's guest link remove one guest (end that session
now), ban one (also refuse that session and its browser from re-entering until unbanned), see and
lift bans in the room settings, and rotate the guest link so a link that has spread stops admitting
newcomers without ejecting the guests already in the room. It fixes finding F-24 of
`docs/audits/2026-10-05-guest-anonymous-session/findings.md`, which recorded the gap as a
requirements decision; the decisions are in §3 and the requirement changes in §13.

## 2. Goals and Non-goals

**Goals**
- A moderator can remove an anonymous guest from a room: every later request, socket and refresh of
  that session is refused, open sockets close, and the guest sees "you were removed from this room".
- A moderator can ban an anonymous guest: as removal, plus the session and the browser identity it
  joined with cannot re-enter the room until unbanned. Bans outlive guest-session retention.
- Room settings list the room's bans (name, time) with an unban action.
- A moderator can rotate the guest link; the old link stops admitting new guests; guests already in
  the room are unaffected.
- Every removal, ban, unban and rotation is audit-logged.

**Non-goals**
- Registered guests (`chatroom_guests`, signed-in users enrolled through a link): not removable here
  (Q-6, FU-1).
- IP-based bans (Q-1): school networks share one address per classroom.
- Showing the guest a reason, or giving moderators a reason field.
- Deleting or relabelling the removed guest's messages (Q-4); the existing moderator delete remains.
- Any change to platform-level Admin bans ([R6.13]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What can a moderator do to one guest, and what does a ban key on? | Remove and ban. A ban keys on the guest session and on the browser identity (`browser_id`) that session joined with. | Requester's choice. `browser_id` is what resume matches on (`backend/contexts/conversation/application/guest_session_service.py:111-112`), so a ban survives reloads, closing the tab and returning through the link. It is client-chosen and stored in localStorage (`frontend/src/slices/conversation/views/GuestLandingView.vue:50,62`), so a private window or another device escapes it; the spec and the UI say so (Q-2 is the remedy). IP was rejected: school NAT would ban the whole class, and the IP-ban middleware does not cover sockets (`backend/app/api/middleware/ip_ban.py:14-43`). |
| Q-2 | Add guest-link rotation? | Yes; amends [R6.12] and [R13.07], which forbid revocation. | Requester's choice. The only robust remedy for a link that has spread; already-joined guests keep their sessions because refresh uses the session cookie, not the link token (`backend/app/api/v1/guests.py:134-167`). Permission-matrix row 18 is already titled "Create / revoke Guest invite link" (`REQUIREMENTS.md:197`). |
| Q-3 | Who may act? | Those who may manage the guest link, matrix row 18 (`Capability.GUEST_LINK_MANAGE`, `backend/shared_kernel/auth/permissions.py:232-235`): Project Owner, Org Owner, Admin. | Requester's choice; the same people who can turn guest links off. |
| Q-4 | What does the removed guest see, and what happens to their messages? | A banner "You were removed from this room", no reason, no Rejoin. Messages stay, with their labels. | Requester's choice. The guest session row is kept (marked revoked) so its name keeps labelling past messages ([R13.33]); deleting it would turn them into generic "Guest" labels. |
| Q-5 | How long does a ban last? | Until a moderator lifts it; listed in room settings. | Requester's choice. Owners cannot read the audit log ([R17.02]), so the settings list is their only view of bans. |
| Q-6 | Include registered guests? | No; FU-1. | Requester's choice. They are accountable accounts an Admin can already ban platform-wide ([R6.13]), and removing them needs a different mechanism (`chatroom_guests` rows, user-id keys). |
| Q-7 | Does this depend on other dossiers? | `depends_on`: the session-lifecycle, backend-hardening and sender-marking dossiers. | It extends the lifecycle dossier's `guestSessionEnd` with a third reason; it adds a `removed` outcome to the hardening dossier's socket `authorize` result and runs the ban check inside its join lock; it uses the sender-marking dossier's roster `kind` to put the action on guest rows of the participant list. |

## 4. Current State

- **No room-level removal exists for anyone.** `guests.py` has enroll, session, refresh, rename and
  ticket routes only (`:43-251`); `ChatroomGuestRepository` has no remove
  (`backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:987-1070`); there is no
  ban table or removal event. Platform bans are Admin-only (`REQUIREMENTS.md:201,265`;
  `backend/contexts/identity/application/admin_service.py:364-402`) and their client signal is a
  per-user channel (`frontend/src/shared/composables/useBanKickGuard.ts:35-38`) a guest never joins.
- **A guest's token cannot be revoked.** Guest JWTs carry a jti, but it is discarded at mint
  (`guest_session_service.py:122,168,220`) and `guest_sessions` has no column for it
  (`backend/alembic/versions/0085_guest_sessions.py:39-68`). The middleware's guest branch reads no
  database row (`backend/app/api/middleware/auth.py:103-109`), and `_resolve_guest_access` does not
  read `guest_sessions` (`backend/contexts/conversation/application/access.py:134-165`), so deleting the
  row leaves a 4-hour token working.
- **One choke point covers nearly every path.** `resolve_room_access` (guest branch
  `_resolve_guest_access`) is called by messages, attachments, uploads, activities, canvas, search,
  exports, observations, chatroom reads, rename, both room sockets at handshake and in the watchdog
  (`backend/app/api/ws/chatroom.py:71-79,263-277`; `backend/app/api/ws/canvas.py:103-110,267-275`), and
  orchestration reads (`access.py:328-350`). Not covered: guest refresh
  (`guest_session_service.py:200-245`), create and resume (`:111-152`), and the two ticket routes
  (`guests.py:234-251`; `backend/app/api/v1/auth.py:589-615`), which the handshake check catches later.
- **Retention.** Idle guest sessions are purged after 30 days (`backend/app/workers/tasks/guest_cleanup.py:17-27`),
  so state stored only on the session row would vanish.
- **Link.** `chatrooms.guest_token` is unique and set at creation
  (`backend/contexts/conversation/infrastructure/tables.py:49`, `chatroom_repo.py:71,100`); the settings
  view only reads it (`backend/app/api/v1/chatrooms.py:1236-1256`;
  `frontend/src/slices/conversation/views/ChatroomSettingsView.vue:342,674-683`).

## 5. Design

### Options considered

**Option A, revoke flag on the session plus a ban table**: `guest_sessions.revoked_at` checked in
`_resolve_guest_access`, refresh and resume; a `chatroom_guest_bans` table keyed by room, session and
a hash of the browser id, independent of session retention. Uses the existing choke point and the
socket watchdog's re-check; costs one primary-key read per guest request.

**Option B, jti denylist**: record each guest token's jti and denylist it on removal. Immediate for
the current token, but needs jti tracking across rotations, does not stop refresh or resume by itself,
and keeps no durable ban.

**Option C, Redis revocation key**: `guest_revoked:{session_id}` checked in the middleware. Cheap and
immediate, but non-durable and outside the conversation context's ownership; a ban still needs a
table.

### Decision

Option A. The access choke point already reaches every room surface and both sockets' watchdogs, so
one check enforces removal everywhere a guest can act, and a durable ban table is needed regardless.
The per-request read is a primary-key lookup on a small table. A removal also emits an ids-only room
event so the removed client reacts at once instead of at the next watchdog tick. Given up: a removed
guest's in-flight request that has already passed the check completes.

## 6. Detailed Changes

- **Backend** (conversation context)
  - Migration `0099`: `guest_sessions.revoked_at timestamptz NULL`; new table `chatroom_guest_bans`
    (`id`, `chatroom_id` FK `chatrooms` ON DELETE CASCADE, `guest_session_id uuid` without FK so it
    outlives the purge, `browser_id_hash text NULL`, `display_name text` snapshot for the list,
    `created_by uuid` FK `users` ON DELETE SET NULL, `created_at`), unique on
    `(chatroom_id, guest_session_id)`, index on `(chatroom_id, browser_id_hash)`. Reversible
    (drops). `guest_sessions` gains `browser_id` hashing only in the ban row; the session table keeps
    its current `browser_id` column.
  - Domain error `GuestRemoved`, mapped to 403 `conversation/guest-removed`.
  - `_resolve_guest_access`: load the session by id (the principal's `user_id`); missing or revoked
    raises `GuestRemoved`.
  - `GuestSessionService`: `remove(chatroom_id, guest_session_id, *, ban, actor)` sets `revoked_at` and,
    with `ban`, inserts the ban row; `create_or_resume` (inside the hardening dossier's join lock)
    refuses a banned `browser_id` with `GuestRemoved` after the link-token check, and treats a revoked
    but unbanned session found by `browser_id` as absent, creating a fresh session; `refresh` refuses a
    revoked session after the cookie match (session-lifecycle ordering); `unban(ban_id)` deletes the row.
  - The [R13.06a] active-guest count (`count_active`) excludes revoked sessions, so repeatedly removing
    a guest who rejoins cannot fill the room's cap with dead rows for 24 hours.
  - The hardening dossier's shared mapping from access exceptions to the socket `authorize` outcome
    gains `removed` (from `GuestRemoved`), closed with 4408 after accept, on **both** the chat socket
    and the canvas socket (`backend/app/api/ws/canvas.py:109,277`), at the handshake and in each
    watchdog. Without it the canvas socket would treat `GuestRemoved` as an unexpected error and keep a
    removed guest's CRDT session open.
  - Routes (all gated by row 18 through `outcome_for(Capability.GUEST_LINK_MANAGE, role)` on the room's
    resolved roles, admins included): `POST /api/chatrooms/{id}/guests/{guest_session_id}/remove`
    (body `{ban: bool}`), `GET /api/chatrooms/{id}/guest-bans`, `DELETE /api/chatrooms/{id}/guest-bans/{ban_id}`,
    `POST /api/chatrooms/{id}/guest-link/rotate` (new `guest_token`, returns the new link). Removal of
    a session of another room is 404.
  - After commit: `chatroom.guest_removed {chatroom_id, guest_session_id}` and
    `chatroom.members_changed` on the room channel (ids only); rotation emits `chatroom.updated`.
  - Audit (hardening §7.1 shape; actor is the moderator): `guest.session.removed`, `guest.banned`,
    `guest.unbanned`, `chatroom.guest_link.rotated`.
  - Migration required: yes (§10).
- **API contract**: four new routes, one problem type, one room event. `gen:api` rerun: yes.
- **Frontend** (`slices/conversation`)
  - Transport `guestSessionEnd` (session-lifecycle §7.1) gains `'removed'`, set by the
    `conversation/guest-removed` problem type, a 4408 close, or a `chatroom.guest_removed` event naming
    this tab's session. The boot restore (`resumeGuestSession`, session-lifecycle §7.1 as amended)
    records `'removed'` for that problem type like its other end reasons, so a removed or banned guest
    who reloads lands on the room's removed banner rather than `/login`.
  - `ChatroomView.vue`: removed banner (no Rejoin), composer disabled, socket closed deliberately.
  - Moderator actions ("Remove guest", "Ban guest", each through `useConfirmDialog`) on guest message
    bubbles (`ChatroomMessageBubble.vue` hover actions, `:191-221`) and on guest rows of
    `ChatroomPresence.vue`, shown when `ChatroomOut.is_moderator` is true. On presence rows they appear
    only for roster `kind: "guest_session"` (sender-marking dossier), never for `room_guest`, which is a
    registered guest this dossier does not remove (Q-6); on message bubbles only for
    `sender_type === 'guest'`.
  - `ChatroomSettingsView.vue`: "Rotate link" next to the guest link (with a confirm explaining that
    the old link stops working and joined guests stay); a "Banned guests" list with unban.
  - `GuestLandingView.vue`: `conversation/guest-removed` shows a "You cannot join this room" state with
    no retry.
  - i18n in both `en.json` and `zh-TW.json`: action labels, confirms, removed banner, banned list, link
    rotation, landing state.
- **Deploy/config**: none.

## 7. NFR Checklist

- [x] i18n: every new string through `$t()` in both locales.
- [x] Audit log: four new actions (§6), moderator as actor, guest session or room as resource.
- [x] Tenant isolation: every route resolves the room through `resolve_room_access` and gates on row 18;
  ban and session ids are checked against the path's room.
- [x] Error handling UX: confirm dialogs, success toasts, the removed banner, the landing state, an
  empty state for the ban list.
- [x] Performance: one primary-key read per guest request; the ban list is per room and small.

## 8. Security Considerations

- **AuthZ**: row 18 only; a moderator of room A cannot remove or unban in room B (404).
- **Enforcement coverage**: the access choke point plus explicit checks in refresh, create/resume, and
  the socket outcome; ticket routes are caught at the handshake. Tested per surface (§12).
- **Ban evasion**: documented limit (Q-1); rotation is the remedy for a spread link.
- **Probing**: `guest-removed` is returned only to the removed session's own token or cookie, or after
  the link-token check on join; an outsider learns nothing new.
- **Privacy**: the ban row stores a hash of the browser id, not the id, and a display-name snapshot.
- **WebSocket**: the removal event carries ids only; only the named session's client acts on it.

## 9. Quality Notes

- **Existing debt**: the guest branch of the auth middleware trusts the token without a database read;
  kept, with the session check placed in the room access layer where the context owns it. Matrix row 18
  says "revoke" while [R6.12] forbids revocation; reconciled by §13.
- **Patterns to follow**: row-18 gating as in `export_sender_scope` (`access.py:444-473`, reading cells
  through `outcome_for`); post-commit room events as in `guests.py` `_emit_members_changed`; confirm
  dialogs via `useConfirmDialog`; settings sections in `ChatroomSettingsView.vue`.
- **Reuse inventory**: `resolve_room_access`, `outcome_for`, `Publisher`/`room_channel`, `audit.emit`,
  `_new_guest_token` (`chatroom_repo.py:71`), `useConfirmDialog`, `SBadge`, `convKeys`, the
  session-lifecycle `guestSessionEnd` plumbing, the hardening join lock and socket outcome.

## 10. Risks and Rollback

- **Per-request read for guests**: one PK lookup; measured in the db tier.
- **False sense of security**: a determined student can return from another browser; the confirm
  dialog and settings copy say so and point at rotation.
- **Migration**: additive (nullable column, new table); downgrade drops both. Rolling back the code
  while the column exists is safe (old code ignores it); banned guests become able to join again.

## 11. Acceptance Criteria

- [x] AC-1: after a moderator removes a guest, every HTTP request, socket and refresh of that session is
  refused with `conversation/guest-removed`, open sockets close within one watchdog interval (4408), and
  the guest's client shows the removed banner immediately on the room event.
- [x] AC-2: a removed but unbanned guest can rejoin through the link as a new session.
- [x] AC-3: a banned guest cannot rejoin from the same browser until unbanned: a reload or new tab lands
  on the room's removed banner, and opening the link again shows the landing page's cannot-join state.
- [x] AC-3a: removed sessions do not count toward the per-room guest cap.
- [x] AC-4: bans survive the 30-day guest-session purge and appear in room settings; unban restores
  joining.
- [x] AC-5: rotating the guest link makes the old link refuse new entries while joined guests keep
  working; the settings view shows the new link.
- [x] AC-6: only Project Owners, Org Owners and Admins of the room can remove, ban, unban, list bans or
  rotate; others get 403, and ids from another room 404.
- [x] AC-7: the removed guest's past messages remain with their labels.
- [x] AC-8: each action writes its audit row with the moderator as actor.
- [x] AC-9: migration applies and downgrades cleanly; backend and frontend lint, typecheck, tests (db
  tier included), OpenAPI drift, build and e2e pass in CI.

## 12. Test Plan

- Backend unit: route gating per role (AC-6); service remove/ban/unban; `_resolve_guest_access` refusal;
  create/resume with a banned and a revoked-unbanned `browser_id`; refresh refusal after the cookie
  match; socket `authorize` `removed` outcome closes 4408.
- db tier: the per-surface refusal (send, list, canvas, attachment upload) for a revoked session; ban
  survives deleting the session row (purge); rotation changes the token and the old one fails join;
  migration upgrade and downgrade.
- Frontend component: moderator actions shown only to moderators and only on guests; removed banner on
  the problem type, the close code and the event; settings ban list and unban; rotate confirm.
- e2e (`frontend/e2e/26-guest-session-lifecycle.spec.ts` extended): owner removes a connected guest, the
  guest sees the banner; owner bans, the guest's reload lands on the removed banner and reopening the
  link lands on the cannot-join state.
- Unit: the canvas socket closes 4408 for a removed guest at the handshake and from its watchdog; a
  removed guest's revoked row is not counted by `count_active`; presence actions are absent on
  `room_guest` rows.

## 13. SRS Delta

Replace [R6.12] with:

> - **[R6.12]** Guest links **cannot be expired, password-protected, or use-capped** (explicit
>   stakeholder decision, Q43). A room's guest link can be rotated by those who manage it (matrix row
>   18): the old link stops admitting new guests, while guests already in the room keep their sessions.
>   Guest access is otherwise revoked by turning guest links off, deleting the Chat Room, removing or
>   banning the specific guest ([R13.07a]), or banning the specific user ([R6.13]).

Replace [R13.07] with:

> - **[R13.07]** No expiry, no use cap, no password (explicit stakeholder decision). A guest link can be
>   rotated, which stops the old link admitting new guests ([R6.12]); to revoke a specific guest's
>   access, remove or ban that guest ([R13.07a]), or delete the chat room.

Add after [R13.07]:

> - **[R13.07a]** Those who may manage a room's guest link (matrix row 18) may remove an anonymous guest,
>   which ends that guest session at once: every request, socket and refresh of it is refused, the guest
>   is told they were removed, and their messages remain, labelled as before. They may instead ban the
>   guest, which additionally refuses that session and the browser identity it joined with from
>   re-entering the room until a moderator lifts the ban. A ban is a deterrent, not an identity
>   guarantee: an anonymous guest who changes browser or clears its storage is a new guest, and rotating
>   the link ([R6.12]) is the remedy for a link that has spread. Bans persist past guest-session
>   retention and are listed in the room's settings with an unban action. Every removal, ban, unban and
>   link rotation is audit-logged.

## 14. Open Questions

None.

Amended 2026-10-05 after review (before any build): cap counting excludes revoked sessions, the
removed outcome covers the canvas socket, reload of a removed guest lands on the room banner, and
presence actions target `guest_session` rows only.

## 15. Deviation Log

Verified on PR #238, CI green at `3d9483a6`: the db tier ran 267 tests against the base branch's 259
(the 8 new ones, migration round trip included, with the same 4 unrelated skips), e2e 126 passed
against 123, and every other gate passed. After the code-review fixes (D-10..D-16), CI green again at
`bdea5316`: db tier 268 (the D-10 regression test), backend unit 11300, frontend 1988, e2e 126. Locally: backend unit suite (11289 passed), ruff, mypy,
frontend lint, typecheck, build and tests (one unrelated flake, FU-12). Not observed on a running
stack outside CI's e2e.

- **D-1.** `GuestRemoved` subclasses `ForbiddenInRoom` (§6 named only the problem type). Callers that
  already treat `ForbiddenInRoom` as a denial keep refusing a removed guest; without it the knowledge
  config socket's watchdog (`contexts/knowledge/interfaces/config_access.py:63`) would read the new
  error as a transient fault and keep the socket open. `room_read_outcome` catches it first.
- **D-2.** The ban stores a plain sha256 of the browser id; the requester chose this at plan
  approval. The id is a client-generated random UUID, and `guest_sessions.browser_id` stays plaintext
  until the purge (FU-15), so a keyed hash would add key management for no gain.
- **D-3.** A session that joined without a browser id is banned by session only; agreed at plan
  approval. Removing an already-removed session succeeds and changes nothing; banning it adds the
  ban. Events are emitted only when something changed.
- **D-4.** Ids of another room answer 404 with `conversation/guest-session-not-found` or
  `conversation/guest-ban-not-found` (§6 named only the status).
- **D-5.** `remove` takes the per-room join lock as well, so a resume from the same browser cannot
  land between the revoke and the ban.
- **D-6.** The guest access check reads the session after the room's parents are known to be live,
  so a removed guest of a deleted room is told the room is gone.
- **D-7.** `removed` is recorded from a guest-removed answer to any request carrying this tab's guest
  token, not only the ticket path, since the server answers it only to that session; the landing
  page's session create is excluded because it may name another room.
- **D-8.** The settings copy "Share this permanent link" lost "permanent", which rotation made untrue.
- **D-9.** Built on `feat/guest-kick-and-ban`, stacked on `fix/guest-session-backend-hardening`,
  whose PR #237 was not yet merged into main; the PR for this dossier must target that branch or
  wait for #237.

After `/code-review` of PR #238, the requester chose to fix findings 1 to 7 on this branch (finding 8,
the refresh retry's fixed 500 ms, came from #237's `5f002c9b` and is left to it):

- **D-10.** A ban also revokes every live session of the banned session's browser in the room
  (`GuestSessionRepository.revoke_live_for_browser`), and the removal announces each ended session.
  A guest removed, rejoined from the same browser, then banned through an old message kept the new
  session before, since refresh never consults bans. Regression tests: unit and db tier.
- **D-11.** The browser id lives in its own per-room key, `smap:guest-browser:{room}`
  (`utils/guestHint.ts` `guestBrowserId`), which the boot restore never removes; a session that
  expired used to drop the hint and with it the id the ban keys on, so the ban lapsed after the
  7-day cookie. Regression test: `GuestLandingView.test.ts`.
- **D-12.** `_room_readable` reads `GuestRemoved` as "not readable", so an orchestration record of a
  removed guest's former room answers the same 404 as a missing one ([R15.24]). Regression test:
  `test_orchestration_room_scoped_reads.py`.
- **D-13.** The guest ws-ticket route refuses a removed session (`ensure_guest_session_live`, shared
  with the access check) instead of minting tickets the handshake then closes; closes FU-4 for that
  route (the account ticket route is unaffected: a guest token never reaches it usefully).
- **D-14.** Rotation emits no `chatroom.updated` (§6 said it would): nothing a viewer's room DTO
  carries changed, so the frame only told guests an invisible write happened.
- **D-15.** Removal emits no `chatroom.members_changed` (§6 said it would): a removed session keeps
  labelling its messages, so the roster is unchanged.
- **D-16.** `read_guest_link` now uses the same room-level row-18 gate as the new routes; closes FU-10.

## 16. Follow-ups

- **FU-1.** Registered guests (`chatroom_guests`) cannot be removed from a room either (Q-6).
- **FU-2.** The guest branch of the auth middleware accepts a guest token without any database read;
  routes outside the room access layer (the two ticket routes) rely on the handshake check. Consider a
  shared guest-session check there if more such routes appear.
- **FU-3.** Nothing closes a removed guest's open sockets server-side at removal; the watchdog does,
  within about 60 s (`shared_kernel/realtime/connection.py:82`), and meanwhile the socket still
  receives room frames and may send typing and drafts. The spec accepts this window (AC-1); a
  per-principal close on the `chatroom.guest_removed` frame would remove it. Security audit, medium.
- **FU-4.** (Guest route closed by D-13.) The account ticket route (`app/api/v1/auth.py` ws-ticket)
  does not check guest sessions; the handshake refuses a removed one with 4408.
- **FU-5.** Pre-existing sibling of the hardening dossier's bytes fix: registered-guest enrolment
  (`contexts/conversation/application/guest_service.py:63`) still compares the link token as `str`,
  so a non-ASCII token answers 500.
- **FU-6.** `GET .../guest-bans` is unbounded; only moderators create bans, but a cap or paging would
  bound it.
- **FU-7.** A ban of a session that joined without a browser id does not stop re-entry; the UI could
  tell the moderator.
- **FU-8.** Socket close codes (4403, 4404, 4408) are literals in the chat view, the canvas provider
  and the backend; export them from `@shared/transport`.
- **FU-9.** `ConversationFacade` now has 51 public methods and guest session, moderation and link
  rotation share `GuestSessionService`; a guest facade and service split would separate them.
- **FU-10.** (Closed by D-16.)
- **FU-11.** `test_guest_session_hardening_db.py` and `test_guest_identity_writes_db.py` still carry
  their own copies of the harness now in `tests/integration/guest_db_kit.py`.
- **FU-12.** `slices/agents/__tests__/AgentToolsView.test.ts` (CodeMirror lint diagnostic) failed
  once under the full local run and passes alone; unrelated to this change.
- **FU-13.** `_guest_link_out` builds the link from the request's Host header; a configured public
  base URL would not depend on the proxy pinning it.
- **FU-14.** Another moderator's open settings page does not pick up a rotated link until reload; the
  settings view holds no room socket, and rotation emits no room frame (D-14).
- **FU-15.** `guest_sessions.browser_id` stays plaintext for the session's 30-day life; storing only
  the hash there too would let resume and ban share one form.
