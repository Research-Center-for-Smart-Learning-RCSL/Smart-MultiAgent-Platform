---
type: audit
status: reviewed
created: 2026-10-05
requirements: [R5.04, R6.11, R6.12, R13.04, R13.06, R13.06a, R13.06b, R13.07, R13.32, R13.33, R13.59, R15.24, R30.26]
---

# Audit: Guest anonymous session

## 1. Scope

**Area.** The anonymous guest path introduced by
`docs/tasks/2026-09-04-guest-anonymous-session/` and its follow-up fixes (60bc1a6b, 56ba5d81,
340e697e, f70d5891, fff0c02a): the `guest_sessions` table, repository and service, the guest
routes in `app/api/v1/guests.py`, the guest branch of `AuthMiddleware`, `ws_auth` and the
realtime watchdog, the guest branch of `resolve_room_access`, the `guest` sender type wherever
messages are read back (member roster, agent transcript, exports), and the frontend guest flow
(`GuestLandingView.vue`, the guest session store, `axios.ts` guest context and refresh,
`ws-manager.ts`, the identity session store, router guards, and the guest-facing parts of
`ChatroomView.vue`, `ChatroomHeader.vue` and `ChatroomPresence.vue`). Because the guest
principal reaches every route that accepts `current_principal`, the sweep followed it into
attachments, activities, canvas, orchestration, keys and chat export.

**Intent sources.** `REQUIREMENTS.md` [R5.04], [R6.02], [R6.11], [R6.12], [R13.04]-[R13.07]
(including [R13.06a] and [R13.06b]), [R13.32], [R13.33], [R13.46], [R13.59], [R15.24],
[R24.43], [R30.26], and permission matrix row 19; the guest dossier
(`docs/tasks/2026-09-04-guest-anonymous-session/spec.md`), whose 23 acceptance criteria are all
ticked; and, for the cross-feature findings, `docs/tasks/2026-07-22-chat-export-authz-and-polling/`
and the canvas template dossier. Intent coverage is good. The guest dossier's §10 also proposed
a `require_registered_user` guard that would have bounded much of what follows; it was never
implemented.

**Depth.** Thorough. Six investigation lenses (state and lifecycle; boundary inputs;
concurrency and async; isolation as correctness; event flow and notifications; error paths)
produced roughly 75 raw candidates, deduplicated to 33. Each went through one adversarial
verification round whose explicit task was to refute it, run by four independent verifiers
grouped by code region; verifiers read every alembic migration up to head (0097) to settle each
foreign-key claim. Verification was static: no stack was launched and nothing below was
reproduced against a running system. That matters here because the dossier's test plan names
`frontend/e2e/guest-access.spec.ts`, `frontend/e2e/guest-session-lifecycle.spec.ts` and
`tests/integration/audit/test_guest_audit.py`, none of which exists, so most guest ACs were
ticked without an executed test.

**Root causes.** Most findings trace to two facts. First, an anonymous guest's
`Principal.user_id` is a `guest_sessions.id`, and only code that goes through
`resolve_room_access` was taught about it: anything that authorizes through the role resolver
plus the legacy `chatroom_guests` table, reads `guest_sessions.display_name`, or writes the id
into a column with a foreign key to `users.id` was not. Second, the frontend's guest context
lives only in memory and is keyed off the presence of a guest token, so any path that loses or
replaces the token loses the guest identity with it.

## 2. Coverage

Read in full: the guest dossier, the cited REQUIREMENTS entries, `guest_session_service.py`,
`guest_session_repo.py`, `guests.py`, the guest branches of `app/api/middleware/auth.py`,
`ws_auth.py`, `connection.py` and `ws/chatroom.py`, `access.py`, `read_chatroom` and the members,
presence and agents handlers in `chatrooms.py`, `messages.py` send and dispatch, the
`message_service` send and edit paths, `message_repo.py` sender filters, `chat_export_service.py`,
`transcript.py` and the label resolvers in `turn_engine.py`, the attachments and tus routes and
services, the activities routes with `_resolve_session` and `group_proposal_service`, the canvas
template routes, repository and service, `ws/canvas.py`, `keys.py` and `key_service.upload`,
the FK inventory across all migrations, and on the frontend `GuestLandingView.vue`, the guest
session store, `axios.ts`, `ws-manager.ts` refresh and close handling, `session.ts`, `main.ts`,
`router.ts`, `guards.ts`, conversation `routes.ts`, and the guest-related parts of
`ChatroomView.vue`, `ChatroomPresence.vue` and `ChatroomHeader.vue`.

Sampled rather than read in full: `ChatroomView.vue` beyond the guest-related regions, the
guest cleanup worker, the rate-limit middleware (bucket selection only), and the notification,
skills, agents, graphrag, knowmap and rag routes (grepped for role gates, not traced). Backend
tests were searched for pins on specific behaviors.

Not covered, and therefore not claimed clean:

- **Runtime behavior.** Foreign-key failures, close codes, cookie path matching and refresh
  races were established from schema and code, not observed. The FK findings are high-confidence
  because the constraints are read directly from migrations, but no request was executed.
- **Guest participation in drafts (SRS §32) and observer surfaces** beyond confirming neutral
  values are returned.
- **Rendering and copy.** No visual review; i18n keys for guest error states exist in both
  locales.

## 3. Findings

## F-1: A guest cannot take part in an activity: every write fails on a foreign key to `users`

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/activities.py:1128-1142` (submit), `:1002-1015` (completion),
  `:1076-1084` (open session) pass `principal.user_id` as subject and producer;
  `backend/contexts/activities/application/submission_service.py:111-125,491-501` and
  `session_service.py:294-308` reach `session_repo.create_open`
  (`backend/contexts/activities/infrastructure/repositories/session_repo.py:143-169`, `ON CONFLICT
  DO NOTHING`, which does not cover FK violations); `activity_sessions.subject_user_id` and
  `activity_submissions.producer_user_id` reference `users.id`
  (`backend/alembic/versions/0049_activities.py:97-101,141-145`; 0081 relaxed NOT NULL only);
  no global `IntegrityError` handler exists in `app/`
- **Failure scenario**: A teacher starts an activity in a room with guest links on. A student who
  joined by link opens the Activity tab and submits; the insert into `activity_sessions` raises a
  foreign-key violation and the student gets a 500. Marking the session complete fails the same
  way.
- **Blast radius**: Every guest student in every activity, which is the core classroom workflow.
  The fff0c02a test `TestGuestSubmission` mocks every repository and its own docstring concedes it
  cannot see FK violations. Group proposals and votes are gated before any insert
  (`group_proposal_service.py:244-250,630-634`) and fail with a clean 4xx instead.
- **Intent source**: [R30.26] ("a guest who satisfies the room's access tier is a full activity
  participant"); guest dossier OQ-2, left open as "needs verification"

## F-2: A guest cannot attach a file: the upload fails on a foreign key to `users`

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/attachments.py:69-94` (gated only by `ensure_can_send`, which a
  guest passes) and `tus.py:201-206`/`tus_service.py:381-392`; `attachment_service.py:146-151`
  writes the object to MinIO first, then `:226-235` inserts `message_attachments.uploaded_by_user_id`,
  which references `users.id` (`0018_attachments_chatroom.py:40-48`); the composer offers uploads
  to everyone (`ChatroomView.vue:235-245`)
- **Failure scenario**: A guest drops a file on the composer and gets a 500. The object written
  first is orphaned until the `chat-uploads` bucket's 3-day lifecycle removes it.
- **Blast radius**: Every guest upload, single-shot and resumable.
- **Intent source**: [R5.04] and permission matrix (guests may send in the room); [R30.26] for
  activity uploads

## F-3: Anonymous guests are refused the room record, so settings and export controls appear

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/chatrooms.py:457-490` (`read_chatroom`) and `:1199-1240`
  (`list_chatroom_members`) authorize through `roles_for` plus `is_chatroom_guest`, which reads
  only `chatroom_guests`, and never reach the guest branch of `resolve_room_access`
  (`access.py:94-95,134-165`); `frontend/src/slices/conversation/views/ChatroomView.vue:29-30`
  computes `can-export`/`can-settings` as `!(data?.viewer_is_guest ?? false)`, which is true when
  the query errored; `ChatroomHeader.vue:190-191`; the settings route lacks `allowGuestSession`
  (`routes.ts:28-33`) and the guard redirects to login (`guards.ts:28-30`)
- **Failure scenario**: A guest enters by link. The room query returns 403, so the header shows
  the settings gear and the export button. Clicking the gear sends the guest to the login page,
  which they cannot use; export returns 403. The room title falls back to `#` plus eight
  characters, and every registered author is shown to the guest as an eight-character id.
- **Blast radius**: Every anonymous guest session. The dossier's Phase 3 note assumed
  `viewer_is_guest` "is already available from ChatroomOut", which is unreachable for this
  population; FU-10 of the dossier records only that the component test was not written.
- **Intent source**: guest dossier AC-19 and §6 Phase 3 "Settings gear hiding"; [R5.04]

## F-4: A guest's display name never reaches any other participant

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `chatrooms.py:1229-1240` (roster is `chatroom_guests` plus
  `distinct_user_sender_ids`, which filters `sender_type == 'user'`,
  `backend/contexts/conversation/infrastructure/repositories/message_repo.py:181-190`);
  `guest_sessions.display_name` is read only when minting the guest JWT;
  `ChatroomView.vue:1275-1290` (author label falls back to `sender_id.slice(0, 8)`) and `:874-888`
  (roster refetch only on unknown `user` senders); `guests.py:170-187` (rename emits nothing);
  presence frames carry only `user_id` (`ws/chatroom.py:286-289`)
- **Failure scenario**: Guest "Alice" posts. Every member and every other guest sees her messages
  and presence entry labelled `3f2a9c1b`. When she renames herself, nothing changes for anyone
  else.
- **Blast radius**: Every room with guests; in a classroom of guest students, nobody can tell who
  said what. Guest dossier FU-9 records only the presence-panel symptom.
- **Intent source**: [R13.06] (the visitor supplies a display name), guest dossier §5 Option B
  ("participant resolution"), AC-21

## F-5: Agents see every anonymous guest as the same speaker, "Guest"

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/transcript.py:73-77,135` (guest maps
  to role `user`); `turn_engine.py:3584-3624` (`_room_user_labels` consults `chatroom_guests`,
  then identity, then the literal `"Guest"`); `:3724-3725` (`_provider_message` renders
  `Guest: ...`); `:3626-3659` (activity legend omits unlabelled participants)
- **Failure scenario**: Guests Alice and Bob both address the teaching assistant agent. The
  prompt shows two `Guest:` speakers; the agent cannot tell them apart, address either by name,
  or attribute activity rows, which appear as bare codes.
- **Blast radius**: Every agent turn in a room with more than one guest. The example course's
  agents are built around addressing individual students.
- **Intent source**: [R13.33] ("Human labels resolve as room guest label, then account display
  name ... so an agent can always tell two speakers apart"); the prompt's own participant note

## F-6: A guest cannot rename themselves, and sees their own typing indicator

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `ChatroomPresence.vue:20,33` (edit control requires `u.isYou && viewerIsGuest`);
  `ChatroomView.vue:504,1113` (`isYou` compares against `session.me?.id`, null for a guest);
  `ChatroomView.vue:313-317,400-404` (the non-tabbed branch renders `ChatroomPresence` without
  `viewer-is-guest`, `viewer-name` or the update handler); `:1104` (typing filter uses the same
  null id) with `ws/chatroom.py:239` (typing is broadcast to the sender too)
- **Failure scenario**: A guest opens the People rail. Their own row shows an id, no "you" tag and
  no edit icon; there is no other rename path. While typing, they see "3f2a9c1b is typing..."
  under their own composer.
- **Blast radius**: Every anonymous guest; AC-21 is unreachable through two independent gates.
- **Intent source**: guest dossier AC-21

## F-7: Reloading the page or opening a new tab ends a live guest session

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/transport/axios.ts:28-31,43-53` (guest token and context in
  memory only; `setGuestContext` called only from `GuestLandingView.vue:101`);
  `frontend/src/app/main.ts:74-80` and `frontend/src/slices/identity/stores/session.ts:70-77`
  (boot hydrates only through `/api/auth/refresh`); `guards.ts:28-30`; `GuestLandingView.vue:106`
  (token stripped from the URL); the refresh cookie is path-scoped to `/api/guest/{id}`
  (`guests.py:116`)
- **Failure scenario**: A guest presses F5. Hydrate fails against the user refresh endpoint, the
  guard redirects to `/login`, and the 7-day guest refresh cookie is never presented. The only way
  back is the original link.
- **Blast radius**: Every guest who reloads, which on school devices is routine.
- **Intent source**: [R13.06b] ("silent refresh extends the session indefinitely while the cookie
  survives"); guest dossier §6 Phase 2 `axios.ts` bullet ("or if no JWT exists but a guest refresh
  cookie might"), AC-16

## F-8: A failed background refresh silently strands the guest with no expiry message

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `axios.ts:278-280` (`attemptRefresh` nulls the token on any error, including
  network errors) and `:294` (`refreshAccessToken` never calls `onUnauthorized`);
  `ws-manager.ts:385-403`; `ChatroomView.vue:1019-1023` (close handler returns early once
  `isGuestSession` is false, so `markExpired` never runs); `axios.ts:195-197,309` (the next ticket
  request has no bearer and is not refresh-eligible); `main.ts:83-85` with `session.ts:70`
  (the next focus hydrate clears the session)
- **Failure scenario**: A laptop wakes after the 4-hour token expired; the socket's refresh timer
  fires before Wi-Fi is back and fails. The token is nulled, the server closes the socket with
  4401, and the handler ignores it. The guest sees a "reconnecting" pill forever, sends fail, and
  no "session expired" banner or rejoin link appears; on the next focus the room is wiped, although
  the refresh cookie was still valid.
- **Blast radius**: Any guest whose single proactive refresh fails; idle guests rely on that path
  only.
- **Intent source**: guest dossier AC-20, §6 WebSocket reconnect guidance; [R13.06b]

## F-9: Guest context survives a later sign-in and hijacks the user's refresh

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `clearGuestContext` is called only at `frontend/src/app/router.ts:93`; `session.ts`
  `clear()` (`:51-59`) and `login()` (`:25-31`) never call it; `axios.ts:271-274` and `:309` route
  refresh and WS tickets to guest endpoints whenever the context is set; `guests.py:204-205`
  rejects a user principal on the guest ticket route
- **Failure scenario**: A guest clicks the header Back button or the settings gear (F-3), lands on
  `/login`, and signs in with a real account without reloading. Every chatroom socket now asks
  `/guest/ws-ticket` with the user bearer, gets 404, and reconnect-loops. At the first user-token
  expiry the 401 path posts to `/api/guest/{id}/refresh`; if the guest cookie is still valid, the
  user's session is silently replaced by a guest token.
- **Blast radius**: Shared classroom devices, where a student moves from guest to account in one
  tab.
- **Intent source**: guest dossier §6 Phase 2 (guest context only "when a guest JWT is active"),
  D-6; [R6.11]

## F-10: "Enter as Guest" breaks the signed-in user's own session

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `GuestLandingView.vue:165-168` and `:100-101` (`setAccessToken(guestJWT)` while
  `session.me` stays set); `session.ts:16` (`isAuthenticated` derives from `me`) and `:70`
  (hydrate skipped while a guest token is held)
- **Failure scenario**: A signed-in project member opens a guest link and picks "Enter as Guest",
  then uses the sidebar. The full app shell remains, but every request carries the guest token:
  project and room reads return 403 or empty lists, the user notification socket is refused, and
  an admin sees edit controls the backend rejects. Only a hard reload recovers, and that discards
  the guest session (F-7).
- **Blast radius**: Teachers testing the student path from their own browser.
- **Intent source**: guest dossier Q-4, AC-23, and D-6, which names this exact breakage and says
  Phase 3 replaces the guard that prevented it; Phase 3 shipped without a replacement

## F-11: A member's narrowed chat export includes every guest's messages

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `message_repo.py:346-352` (narrowed predicate `sender_type != 'user' OR
  sender_id = own`); `chat_export_service.py:113-150` (rows serialized with content, edit history
  and attachment paths); `backend/contexts/conversation/domain/models.py:12-16` (`GUEST` sender
  type added 2026-09-04); `backend/tests/unit/test_export_authz.py:209-214` copies the same
  predicate into its mock
- **Failure scenario**: A project member, who receives the narrowed export, exports a room where
  guests posted. The archive contains every guest's messages, edits and attachment object paths.
- **Blast radius**: Every narrowed export from a room with guest links. The export dossier
  predates the guest sender type, when `!= 'user'` meant exactly agent plus system; the guest
  dossier never revisited matrix row 19. The member can already read these messages in the room,
  so this breaks the specified narrowing rather than a read boundary.
- **Intent source**: `REQUIREMENTS.md` permission matrix row 19 (own messages plus agent and system
  messages only); chat-export dossier Q-1a; `access.py:448-452`

## F-12: Project canvas templates leak across tenants and can be applied into any room

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/canvas_templates.py:146` (membership checked only when
  `project_id` is supplied); `backend/contexts/canvas/infrastructure/repositories/canvas_template_repo.py:42-52`
  (with `scope=project` and no project id, the only filter is the scope); `canvas_templates.py:250-271`
  and `backend/contexts/canvas/application/template_service.py:157-208` (apply checks the target
  room only and never compares `template.project_id` with the room's project); both routes accept
  any principal, including a guest (`app/api/middleware/auth.py:82-114`)
- **Failure scenario**: Any signed-in user, or an anonymous guest from any public link, calls
  `GET /api/canvas-templates?scope=project` and receives every project's templates across all
  tenants. Applying a foreign template into their own room copies its content onto the canvas,
  where they can read it; templates saved with "save as template" are copies of real project
  canvases. The `elements` branch also overwrites a non-empty canvas.
- **Blast radius**: Every tenant's project templates. Not guest-specific; it surfaced on the guest
  sweep because guests reach the route. Templates dossier D-4 believed the guest list bypass fixed,
  but a685b07e changed an empty result into "all project templates". Also routed to
  `check-security` (FU-2).
- **Intent source**: [R13.59] (project templates readable by project members)

## F-13: The room record and member list ignore room access flags

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `chatrooms.py:457-490,1199-1240` call neither `ensure_can_read` nor
  `_satisfies_room_flags`; turning `allow_guest_links` off deletes no `chatroom_guests` rows
- **Failure scenario**: A registered guest whose room has guest links turned off, or a project
  member refused by `allow_project_owners_only` or a member-group restriction, requests the room by
  id and receives its name, access flags and the full member display-name roster (members also get
  `created_by_user_id` and `observers_present`), while the messages return 403.
- **Blast radius**: Requires knowing the room id; listings are already filtered.
- **Intent source**: [R13.32]. Already recorded for `read_chatroom` as FU-10 of
  `docs/tasks/2026-07-22-settings-form-reconciliation/`; the members endpoint is not recorded
  anywhere.

## F-14: No audit row has the shape AC-9 specifies

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `message_service.py:178-194` (`message.sent` for a guest has the guest id as actor
  but no `guest` metadata); `guest_session_service.py:114-129,159-174,210-223` (session events have
  `guest: true` but a null actor, from 60bc1a6b, although migration 0096 in the same commit dropped
  the FK that motivated it)
- **Failure scenario**: An administrator queries `actor_user_id = :gsid AND metadata @>
  '{"guest":true}'` for a guest who joined and posted, and gets zero rows; the trail is split across
  two keys and the audit query service has no metadata filter.
- **Blast radius**: Audit investigations of guest activity.
- **Intent source**: guest dossier AC-9 and §6 "Audit events" (the 60bc1a6b deviation is not in
  the dossier's deviation log)

## F-15: Concurrent joins can exceed the per-room guest cap

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/conversation/infrastructure/repositories/guest_session_repo.py:95-110`
  (`FOR UPDATE` locks only existing rows; the docstring claims it prevents the race);
  `guest_session_service.py:141-151` (count then insert, under READ COMMITTED)
- **Failure scenario**: With 49 active guests, two students submit the join form at the same
  moment; each counts 49 and each inserts, giving 51. With zero rows nothing is locked at all.
- **Blast radius**: Bounded by the per-IP auth rate limit, but a class joining at once is exactly
  the concurrent case.
- **Intent source**: [R13.06a]; guest dossier AC-4

## F-16: A guest can enroll in a room whose workspace or project was deleted

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `guest_session_service.py:85-91,190-194` check only `chatrooms.deleted_at`
  (`chatroom_repo.py:120-128`); workspace and project soft deletes do not cascade to rooms
  (`workspace_service.py:135-157`, `project_service.py:204-242`); `ws/chatroom.py:77` catches only
  `ChatroomNotFound` and `ForbiddenInRoom`
- **Failure scenario**: The owner deletes the project. A visitor opens an old link, the form
  succeeds and consumes a cap slot, and every subsequent request fails; the socket shows "guest
  access has been disabled by the room owner" (deleted project) or an error close (deleted
  workspace) instead of the invalid-link state.
- **Blast radius**: Stale links after a course ends; no access is gained.
- **Intent source**: [R6.12]; guest dossier §6 Phase 2 invalid-link state

## F-17: Orchestration reads answer 403 to guests instead of filtering or 404

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `access.py:346-350` (`_room_readable` catches only `ChatroomNotFound` and
  `WorkspaceNotFound`); `access.py:145-146` (the guest branch raises `ForbiddenInRoom` for any other
  room, mapped to 403); `backend/app/api/v1/orchestration.py:282-289,312-319`
- **Failure scenario**: A guest of room A requests an approval belonging to room B and receives
  403 where a byte-identical 404 is required; listing a workflow run's approvals fails entirely as
  soon as one row belongs to another room instead of returning room A's rows.
- **Blast radius**: Guests on orchestration surfaces; ids are UUIDs.
- **Intent source**: [R15.24]

## F-18: A display name of only invisible characters ends in a dead-end "invalid link" screen

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `GuestLandingView.vue:70,283` (JavaScript `trim()` keeps U+200B);
  `guests.py:66` (`min_length=1`); `backend/shared_kernel/labels.py:58-62` (strips format
  characters to `None`); `guest_session_service.py:93-95` raises `GuestTokenInvalid`, mapped to
  404; `GuestLandingView.vue:87-91,312-324` (classified as invalid, no retry)
- **Failure scenario**: A student pastes a name consisting of a zero-width space and is told the
  link is no longer valid, with no way to correct the name short of reloading.
- **Blast radius**: Rare input, confusing outcome.
- **Intent source**: guest dossier §6 Phase 2 states (a validation error is not an invalid token)

## F-19: "Try Again" does nothing after a failed resume or account choice

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `GuestLandingView.vue:338-344` (retry calls the enroll submit handler);
  `:140-158,170-177` (`doResume` and `chooseOwnAccount` never set `displayName`, so validation
  fails invisibly)
- **Failure scenario**: A returning guest clicks "Enter Chatroom", the request hits a network
  error, and the retry button has no effect until a reload.
- **Blast radius**: Transient errors on the resume path.
- **Intent source**: the template's own "transient error (retryable)" state

## F-20: The "guest access disabled" banner is missed if the socket is reconnecting

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `ws/chatroom.py:77-79` (pre-accept close, seen by the browser as 1006);
  `connection.py:220-223`; `guests.py:198-215` (ticket route does not check `allow_guest_links`)
- **Failure scenario**: A guest's socket drops (sleep, network blip); while it is down the owner
  turns guest links off. Each reconnect fails as 1006, and the guest sees a reconnecting pill and
  failing polls, then the wrong "expired" message from the HTTP path, never the disabled banner.
- **Blast radius**: Narrow window; AC-22 is literally scoped to "while a guest is connected".
- **Intent source**: guest dossier AC-22

## F-21: Guest refresh-token rotation is not atomic

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `guest_session_service.py:196-202` (reads by hash, then `update_refresh_hash`
  updates by id with no compare-and-swap, `guest_session_repo.py:124-129`); the user path rotates
  atomically in Redis (`auth_service.py:766-777`)
- **Failure scenario**: Two tabs of the same guest refresh at once after a laptop wakes. If both
  reads precede the first commit, both succeed and the stored hash can end up mismatched with the
  browser's cookie depending on response order.
- **Blast radius**: Strict single-use rotation is the house design, so the loser failing is
  intended; the defect is only the missing compare-and-swap.
- **Intent source**: [R13.06b]

## F-22: An upper-case room id in the link breaks silent refresh

- **Severity**: minor
- **Verdict**: plausible (low reachability)
- **Evidence**: `guests.py:116,157` (cookie path built from the canonical lower-case UUID);
  `GuestLandingView.vue:22,101` and `axios.ts:273` (refresh URL uses the route parameter verbatim;
  cookie path matching is case-sensitive)
- **Failure scenario**: A hand-edited link with an upper-case room id works for 4 hours, then the
  refresh carries no cookie and the session expires. Generated links are lower-case
  (`chatrooms.py:1266`).
- **Blast radius**: Hand-altered links only.
- **Intent source**: [R13.06b]

## F-23: The legacy enroll route returns 500 when called with a guest token

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `guests.py:43-59` (accepts any principal) to `guest_service.py:52-74`, which inserts
  `chatroom_guests.user_id` referencing `users.id` (`0016_chatrooms.py:79-81`)
- **Failure scenario**: A signed-in user who earlier chose "Enter as Guest" (F-10) follows another
  guest link in the same tab and picks "use my account"; the enroll request carries the guest token
  and fails with a foreign-key 500. Otherwise reachable only by a crafted request.
- **Blast radius**: Narrow; a consequence of F-10.
- **Intent source**: guest dossier AC-11, D-6

## F-24: No way exists to remove or ban a single anonymous guest

- **Severity**: minor
- **Verdict**: confirmed (requirements gap with no recorded decision)
- **Evidence**: `guests.py` exposes only create, refresh, rename and ticket routes; no code places a
  guest token's jti on the denylist; `access.py:134-165` does not check that the session row still
  exists, so even deleting it leaves the 4-hour token valid
- **Failure scenario**: A guest posts abuse. The owner's only options are turning guest links off,
  which ejects every guest and readmits the abuser when turned back on (the link token is
  permanent and a new browser id suffices), or deleting the room.
- **Blast radius**: Moderation of public-link rooms.
- **Intent source**: [R6.12] and [R13.07] ("revoked only by deleting the room or banning the
  specific user"); the guest dossier amended neither, and its non-goals, open questions and
  follow-ups do not mention moderation. Needs a requirements decision.

## F-25: A turn with no trigger message stages an older member's attachments instead of a guest's

- **Severity**: minor
- **Verdict**: confirmed (mechanism); moot while F-2 stands
- **Evidence**: `backend/contexts/conversation/interfaces/facade.py:674-679`
  (`latest_user_attachments` matches only `SenderType.USER`); `turn_engine.py:1686-1688` (fallback
  when there is no `trigger_message_id`)
- **Failure scenario**: Once guest uploads work, a silence-triggered turn after a guest posted a
  file stages a registered member's earlier attachment (up to 20 rows back) as if it were the
  latest.
- **Blast radius**: Silence and coalesced turns in guest rooms.
- **Intent source**: [R30.26]; the attachment staging contract for wake turns

## 4. Refuted Candidates

- **Group proposals and votes by a guest hit a foreign key.** They do not: `_pin_voters` raises
  `NotAGroupMember` and `_locked` returns `GroupProposalNotFound` before any insert
  (`group_proposal_service.py:244-250,630-634`), and guests can never be member-group members.
- **Workflow `sender_filter="user"` never fires for guest messages.** Documented semantics:
  `docs/workflow.schema.json:210,359`, `docs/UI/08-workflow.md:411,493` and both trigger forms list
  `guest` as a separate value, and `event_dispatch.py:57-58` matches exactly. See FU-9 for the
  inconsistency it reveals.
- **Guest upload failure leaves permanent orphans.** The `chat-uploads` bucket has a 3-day lifecycle
  (`backend/smap/bootstrap/minio_init.py:143-146`), so orphans self-expire; F-2 stands on the 500.

## 5. Hand-off

Triaged with the requester on 2026-10-05: every finding is fixed, grouped by root cause so each dossier is independently reviewable. F-24 was decided as a feature (per-guest removal and ban, with an SRS amendment to [R6.12] and [R13.07]) rather than a requirements retreat. Dossier paths below are the planned slugs; each becomes a link once `/spec` creates it.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-identity-foreign-keys/` |
| F-2 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-identity-foreign-keys/` |
| F-3 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-room-read-and-identity/` |
| F-4 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-room-read-and-identity/` |
| F-5 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-room-read-and-identity/` |
| F-6 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-room-read-and-identity/` |
| F-7 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-8 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-9 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-10 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-11 | fix (2026-10-05) | `docs/tasks/2026-10-05-chat-export-guest-narrowing/` |
| F-12 | fix (2026-10-05) | `docs/tasks/2026-10-05-canvas-template-tenant-scope/` |
| F-13 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-room-read-and-identity/` |
| F-14 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-session-backend-hardening/` |
| F-15 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-session-backend-hardening/` |
| F-16 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-session-backend-hardening/` |
| F-17 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-session-backend-hardening/` |
| F-18 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-19 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-20 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-21 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-session-backend-hardening/` |
| F-22 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-frontend-session-lifecycle/` |
| F-23 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-identity-foreign-keys/` |
| F-24 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-kick-and-ban/` |
| F-25 | fix (2026-10-05) | `docs/tasks/2026-10-05-guest-identity-foreign-keys/` |

## 6. Out-of-scope Observations

- **FU-1 (check-security).** No route-level guard rejects guest principals: the guest dossier's §10
  `require_registered_user` was never implemented, so a guest token reaches every
  `current_principal` route. Concretely, `POST /api/keys` (`keys.py:189-215`, which runs no matrix
  decision despite its docstring) performs an outbound provider probe and Vault Transit encryption
  for an anonymous caller before the owner FK insert fails with 500.
- **FU-2 (check-security).** The canvas template cross-tenant read and apply (F-12) should also go
  through a security review of the canvas surface as a whole.
- **FU-3 (check-security).** Client `yjs-sync-step-2` frames bypass the guest canvas rate limit
  (`ws/canvas.py:235-241`; only `yjs-update` calls `_check_guest_rate`), persist on flush, and are
  never broadcast, so peers diverge; `yjs-sync-step-1` is also unlimited. The shipped client never
  sends step-2. [R13.46]; crdt-sync AC-11 was ticked by code review only.
- **FU-4 (check-security).** `refresh_principal` lets an in-socket refresh swap a user principal for
  a guest one or back with no same-subject check (`ws_auth.py:143-146`).
- **FU-5 (check-security).** `create_or_resume` raises different problem types for a missing room
  and a bad token, an existence oracle the dossier's §8 rules out; the guest refresh token has no
  server-side age check, so a captured cookie works until the 30-day cleanup rather than 7 days;
  `browser_id` is a bearer-equivalent resume key.
- **FU-6 (test coverage).** The dossier's planned `frontend/e2e/guest-access.spec.ts`,
  `frontend/e2e/guest-session-lifecycle.spec.ts` and `tests/integration/audit/test_guest_audit.py`
  do not exist, yet AC-12 to AC-22 are ticked. A db-tier test for any guest write would have caught
  F-1 and F-2.
- **FU-7 (dossier hygiene).** The guest dossier's FU-12 and FU-15 appear fixed in code but are
  still listed open; FU-5 (non-unique `(chatroom_id, browser_id)` index) is still open and has a
  concrete trigger (a returning guest whose row was purged double-submits the name form, after
  which `one_or_none()` raises on every resume). The 60bc1a6b audit-actor change is not in the
  deviation log.
- **FU-8 (check-quality).** The canvas `apply-template` elements branch skips the `CanvasNotEmpty`
  guard that the objects branch enforces; `guest_cleanup.py:29-31` uses `%d` placeholders with
  loguru, so its counts never appear.
- **FU-9 (consistency).** Legacy registered guests still send as `sender_type='user'`, so the
  workflow `guest` filter never matches them; and the collaborative-canvas dossier's Q-2 says canvas
  writers are "guests who can send messages" while the canvas socket gates on `ensure_can_read`.
- **FU-10 (cross-audit).** The research export collapses every guest into the identifier `"guest"`;
  recorded as F-10 of `docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md`.
