---
type: bugfix
status: draft
created: 2026-10-05
requirements: [R5.04, R13.06, R13.19, R13.32, R13.33, R13.34, R28.10, R30.38]
depends_on: [2026-10-05-guest-identity-foreign-keys]
---

# Anonymous guests cannot read their room, are unnamed to everyone else, and cannot rename

## 1. Summary

An anonymous guest's principal id is a `guest_sessions.id` (`backend/app/api/middleware/auth.py:103-109`),
and three read surfaces never learned that: the room record, the member roster, and the agent list
all authorize through tenancy roles plus the legacy `chatroom_guests` table, so a guest gets 403 from
each. The visible results in a classroom are that a guest student sees the settings and export
buttons (which bounce them to a login page they cannot use), every guest is an eight-character id to
everyone else, every guest is the same speaker "Guest" to every agent, guests cannot rename
themselves, see their own typing indicator, and cannot see or @-mention the room's agents. The same
two room endpoints also ignore the room's access flags, so callers refused everywhere else can still
read room names and member rosters. This dossier fixes audit findings F-3, F-4, F-5, F-6 and F-13 of
`docs/audits/2026-10-05-guest-anonymous-session/findings.md`, plus the agent-list gap the analysis
found (§6).

## 2. Observed vs Expected

- **Observed**
  - `read_chatroom` (`backend/app/api/v1/chatrooms.py:457-490`) and `list_chatroom_members`
    (`:1199-1240`) allow a non-admin only when `roles_for` returns a role or
    `ConversationFacade.is_chatroom_guest` finds a `chatroom_guests` row
    (`backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:1011-1029`). An
    anonymous guest has neither, so both return 403. Neither ever reaches the guest branch of
    `resolve_room_access` (`backend/contexts/conversation/application/access.py:94-95,134-165`), and
    neither evaluates the room flags (`_satisfies_room_flags`, `access.py:201-235`).
  - `ChatroomView.vue:29-30` computes `can-export`/`can-settings` as
    `!(roomQuery.data.value?.viewer_is_guest ?? false)`, which is true when the room read errored;
    `ChatroomHeader.vue:190-191` then renders both. The settings route has no `allowGuestSession`
    (`frontend/src/slices/conversation/routes.ts:28-33`), so the guard sends the guest to login
    (`frontend/src/app/guards.ts:28-30`). The header Back button (`ChatroomHeader.vue:3-11`) leads to
    a members-only route the same way.
  - The roster is `chatroom_guests` plus `distinct_user_sender_ids`, which filters
    `sender_type == 'user'` (`backend/contexts/conversation/infrastructure/repositories/message_repo.py:181-190`).
    No code outside the guest service reads `guest_sessions.display_name`. `senderName`
    (`ChatroomView.vue:1272-1287`) therefore labels every other guest's messages, presence entries and
    typing with `sender_id.slice(0, 8)`. The rename route (`backend/app/api/v1/guests.py:171-188`)
    emits nothing, and no event invalidates the members query (`useChatroomSocket.ts:421-434,668-671`).
  - `TurnEngine._room_guest_names` (`backend/contexts/agents/application/runtime/turn_engine.py:3588-3599`)
    reads only `chatroom_guests`; `_room_user_labels` (`:3601-3624`) falls back to the literal
    `"Guest"` (`:3622`) and `_room_display_labels` (`:3626-3659`) omits the guest from the activity
    legend. `_provider_message` renders every anonymous guest as `Guest: ...` (`:3724-3725`).
  - `ChatroomView.vue:504` sets `myId = session.me?.id ?? null`, null for a guest, so `isYou`
    (`:1110`) is never true and the self-typing filter (`:1101`) never applies. The rename control
    requires `u.isYou && viewerIsGuest` (`ChatroomPresence.vue:20,33`), and the two non-tabbed
    presence render sites pass no guest props at all (`ChatroomView.vue:313-317,400-404`); the tabbed
    sites (`:278-284,365-371`) only render when an observer surface or activity exists (`:693-697`).
  - `list_chatroom_agents` (`chatrooms.py:743-785`) refuses any caller without a project role
    (`:757-758`), and agent names come from the project-scoped name list, which a guest also cannot
    read. Guests get an empty agent rail, no @-mention autocomplete, and agent messages labelled with
    ids.
- **Expected**
  - Guest dossier (`docs/tasks/2026-09-04-guest-anonymous-session/spec.md`) AC-19 (no settings gear
    for a guest), AC-21 (a guest renames itself from the participant list), and its §5 Option B, which
    counted "participant resolution" as part of the design (`:159`); FU-9 (`:854-857`) already named
    "extending the members query to include guest sessions" as the remedy.
  - [R13.06]: a guest supplies a display name, which only means something if others see it.
  - [R13.33]: human labels resolve "room guest label, then account display name, then the login
    email ... so an agent can always tell two speakers apart".
  - [R30.38]: the activity legend maps each code to that participant's display name.
  - [R13.32]: a caller must not learn the name or existence of a room they cannot open; recorded as
    FU-10 of `docs/tasks/2026-07-22-settings-form-reconciliation/spec.md:725-733` for `read_chatroom`.
  - `docs/UI/07-conversation.md:1301`: a guest gets no chatroom settings, no export, no agent binding,
    no admin actions.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which access rule governs the room record and roster? | `resolve_room_access` + `ensure_can_read`, the rule messages, presence and the socket already use. | It is the only gate that knows anonymous guests, and it is [R13.32]'s rule. Same direction the requester chose for the dashboard (`docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md` §5, F-9). Callers refused by room flags lose 200s they could not use: they already get 403 on messages and the socket. |
| Q-2 | Should guests see the room's agents? | Yes. `list_chatroom_agents` moves to the same read gate and returns each bound agent's id, name and avatar to any room reader; creator-only fields stay creator-only. | Students need to @-mention the teaching-assistant agent. Agent names already appear on every agent message a guest can read, so the names disclose nothing new; bindings and grants remain the creator's ([R28.10], [R32.05]). |
| Q-3 | How do other clients learn of a guest joining or renaming? | A new ids-only room event `chatroom.members_changed {chatroom_id}`, emitted after commit when a guest session is created or its name changes, consumed by invalidating the members query. | Chosen over reusing `chatroom.updated`, which would refetch the room record and agent lists for every guest join and is tied to [R28.09] disclosure semantics (`chatrooms.py:294-313`). Emitting only on a real change keeps the public create/resume route from being used to flood the room. |
| Q-4 | Which guest sessions does the roster list? | Every session of the room still on record, newest first, capped at 1000 like the user roster. | Covers guests who posted (for as long as their row exists) and online guests who have not, without scanning `messages`. Sessions are purged 30 days after last use, after which their messages fall back to the id, as today. |
| Q-5 | What does an agent see for two guests with the same name, or for a guest whose session was purged? | Unchanged policy: identical names stay identical; a purged session is labelled `Guest` in the transcript and is absent from the legend. | [R13.33] already accepts that labels are self-chosen and may collide, and its "labels are not authentication" sentence is the mitigation. Disambiguating with codes would make labels unstable across turns. Recorded as FU-2 if the classroom pilot shows it matters. |
| Q-6 | How does the client know "this is me" for a guest? | One shared `guestSessionId` computed (the guest JWT's `sub`) in `shared/transport`, and a view-level `viewerId = guestSessionId ?? session.me?.id`, guest first. | `sub` is the id the server uses for the guest everywhere (presence and typing frames, `sender_id`). Guest first because `session.me` can be stale while a guest token is active (audit F-10). |
| Q-7 | How is "viewer is a guest" decided for the header? | `isGuestSession || roomQuery.data?.viewer_is_guest === true`. | `isGuestSession` covers anonymous guests even while the room read is loading or failed; `viewer_is_guest` covers registered `chatroom_guests` guests, who hold a user token. |
| Q-8 | Does this depend on another dossier? | `depends_on: [2026-10-05-guest-identity-foreign-keys]`. | Logical and overlap prerequisite: that dossier adds `ConversationFacade.is_guest_session` next to which the new guest-session reads belong, and edits `ChatroomView.vue`'s attachment wiring and `conversation/interfaces/facade.py`. Build on its branch or after it merges. |

## 4. Reproduction

Preconditions: a room with `allow_guest_links` on, one member, one agent bound, and two browsers.

1. Browser A: member opens the room. Browser B: open the guest link, enter "Alice", enter the room.
2. In B observe: the settings gear, export and Back are visible; the agent rail is empty; typing
   shows "xxxxxxxx is typing..." under Alice's own composer; the People list shows Alice as an id with
   no "(you)" and no edit icon.
3. Alice posts. In A observe the author label is an eight-character id.
4. Alice @-mentions the agent by name: no `mention_agent_ids` is sent (`useChatroomMessages.ts:297-303`).
5. A second guest "Bob" posts; the agent's provider request shows two speakers both prefixed `Guest:`.
6. As a project member refused by an owners-only room, `GET /api/chatrooms/{id}` returns the room name
   and `GET .../members` the roster, while `GET .../messages` returns 403.

## 5. Root Cause Analysis

1. The guest feature represents a guest by a `guest_sessions.id` and taught exactly one gate about it:
   `resolve_room_access` (`access.py:94-95`).
2. `read_chatroom`, `list_chatroom_members` and `list_chatroom_agents` predate that branch or bypass
   it, authorizing through roles and `chatroom_guests` instead. **Root cause for F-3, F-13 and the
   agent-list gap.**
3. The only persisted record of a guest's name, `guest_sessions.display_name`, has no reader on any
   label path: not the roster, not the turn engine. **Root cause for F-4 and F-5.**
4. The client identifies "me" by `session.me`, which an anonymous guest never has. **Root cause for
   F-6.** Aggravating: the non-tabbed presence branches were added without the guest props, so even
   a correct `isYou` would not show the control there.

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Every anonymous guest in every room. The access-flag gap (F-13) exposes room names
  and member display names to project or org members refused by `allow_project_owners_only`,
  member-group restriction, or `allow_org_members` off, and to registered guests after guest links
  are turned off (turning the flag off deletes no `chatroom_guests` rows).
- **Sibling suspects.**
  - `list_chatroom_agents` (`chatrooms.py:756-758`): **confirmed**, same root cause; in scope (Q-2).
  - `get_chatroom_presence` (`chatrooms.py:1314-1328`): **cleared**, already uses
    `resolve_room_access`.
  - Approvals list (`chatrooms.py:1354-1355`): **cleared**, same gate.
  - `read_workspace` (`backend/app/api/v1/workspaces.py:143-153`) and the project agent-name list
    (`backend/app/api/v1/agents.py:456`): **confirmed** guest-forbidden, but correct: a guest is not a
    member of the workspace or project. The view must stop depending on them for a guest (§7.4).
  - Drafts labels (`draft_tools.py:201`): **cleared**, codes only by [R32.04].
  - `ActivityPanel.vue:447` `can-vote="!!session.me?.id"`: excludes guests, which is correct because
    guests can never be member-group members (group dossier OQ-1). Left alone.

## 7. Fix Design

### 7.1 Room record and roster (backend)

- `read_chatroom` and `list_chatroom_members`: `access = await resolve_room_access(...)`, then
  `ensure_can_read(access, is_admin=principal.is_admin)`. `room = access.chatroom`; a pure guest is
  `access.is_guest and not access.roles`; a moderator is `principal.is_admin or access.is_moderator`.
  `_project_id_for_chatroom`, the role resolver and `is_chatroom_guest` drop out of both routes.
  `_to_out`'s guest neutralisation (`chatrooms.py:218-260`) is unchanged and now reaches anonymous
  guests, so they receive `viewer_is_guest=true`.
- Roster: union the existing user entries with `ConversationFacade.guest_session_labels(chatroom_id)`,
  a new facade read over a new `GuestSessionRepository.list_labels(chatroom_id, *, limit=1000)` that
  selects only `id, display_name` (the domain `GuestSession` carries the refresh hash and browser id,
  which must not be loaded for a roster), ordered by `last_seen_at DESC`, served by
  `ix_guest_sessions_chatroom_last_seen`. `ChatroomMemberOut` is unchanged (`user_id`,
  `display_name`); session and user ids are independent `gen_random_uuid` values.

### 7.2 Agent list (backend)

`list_chatroom_agents` moves to `resolve_room_access` + `ensure_can_read`. Every reader receives each
normal-role binding's `agent_id` plus the agent's `name` and avatar reference (resolved through
`AgentsFacade`, the way the creator's settings view already resolves them); the creator-only fields
stay `None` for everyone else, so `response_model_exclude_none` keeps their responses shape-compatible.
The frontend builds `agentNames` from this list instead of the project-scoped name list.

### 7.3 Membership event (backend)

- `GuestSessionService.update_display_name` and `create_or_resume` report whether a session was
  created or its stored name changed (a field on their results).
- `guests.py` session create/resume and display-name PUT: after commit, when that flag is set, emit
  `chatroom.members_changed` `{chatroom_id}` on the room channel inside `try`/`except` with a logged
  warning, the posture of `_emit_chatroom_updated` (`chatrooms.py:287-345`). The PUT also returns the
  normalised name so the client shows what was stored.

### 7.4 Agent labels (backend)

`TurnEngine._room_guest_names` returns the legacy `chatroom_guests` map merged with
`ConversationFacade.guest_session_names(chatroom_id)` (the same room-scoped read as §7.1, as a
dict). Because both resolvers already consult that map first, the transcript prefixes, the activity
legend and the participant note pick the names up with no change to `_room_user_labels` or
`_room_display_labels`. `transcript.py:73-77` gains an explicit `SenderType.GUEST: "user"` entry.
Docstrings describing the precedence are corrected (`turn_engine.py:3569-3573,3589,3608`,
`activity_context_provider.py:35-43`).

### 7.5 Frontend

- `shared/transport/axios.ts`: export `guestSessionId` (the guest token's `sub`, else null).
  `useChatroomMessages.ts:62-66` and `canvas/components/CanvasCommentPopover.vue:31-36` reuse it.
- `ChatroomView.vue`:
  - `viewerId` replaces `myId`; the typing filter, `isYou`, the rename session id and the own-guest
    label use it or `guestSessionId`.
  - `viewerIsGuest` per Q-7; header gets `:can-export`, `:can-settings` and a new
    `:can-navigate-back` all bound to `!viewerIsGuest`.
  - One `presenceBindings` object (props and the rename handler) bound at all four `ChatroomPresence`
    sites.
  - Members query uses a new `convKeys.chatroomMembers(id)`; the unknown-sender watcher accepts
    `'guest'` senders; after a successful rename the members query is invalidated and the returned
    name replaces the local override.
  - `agentNames` comes from the room agent list (§7.2) for every viewer.
- `ChatroomHeader.vue`: `canNavigateBack` prop (default true) gates Back.
- `useChatroomSocket.ts`: handle `chatroom.members_changed` (room-checked like `chatroom.updated`) by
  invalidating `chatroomMembers`; add the members key to the reconnect invalidation.
- `ChatroomPresence.vue`: avatar initials from `displayName || id`.

### 7.6 Existing debt (record, do not imitate)

- The members query key is an inline literal (`ChatroomView.vue:622-626`) outside `convKeys`; fixed
  here because the new event must invalidate it.
- `ChatroomView.vue:557-560` documents a 403 on the room read as intended degradation; the comment is
  rewritten, since the read now succeeds for guests.
- `ChatroomPresence` has no test file; one is added.

### 7.7 Patterns to follow

- Room gates: `get_chatroom_presence` (`chatrooms.py:1314-1328`) is the exemplar of
  `resolve_room_access` + `ensure_can_read` in a route.
- Post-commit room events: `_emit_chatroom_updated` (`chatrooms.py:287-345`).
- Frontend query keys: `frontend/src/slices/conversation/queries/index.ts`.
- Faking a guest in frontend tests: `setAccessToken` with an unsigned token
  (`admin/__tests__/AdminFeedback.test.ts:124-125`), or mocking `isGuestSession`
  (`activities/__tests__/ActivityPanel.test.ts`).

### 7.8 Reuse inventory

`resolve_room_access`, `ensure_can_read`, `_to_out` neutralisation, `ConversationFacade.is_guest_session`
(from the prerequisite), `Publisher`/`room_channel`, `normalise_label` (names are already normalised on
write), `_one_line_label`/`_one_line` render guards, `isGuestSession`/`accessTokenClaims`, existing i18n
keys (`conversation.chatroom.you`, `conversation.guest.editName`, `displayNameUpdated`,
`displayNameUpdateFailed`); no new strings are needed.

### 7.9 Security considerations

- Tenant and room boundary: both room endpoints become stricter for every non-guest caller and open
  only to guests of that exact room (`_resolve_guest_access` rejects another room before any lookup,
  `access.py:145-146`). The roster read is scoped by `chatroom_id` in SQL.
- Disclosure: room readers learn the display names of guests who joined and did not speak. They already
  see those guests in presence while online; recorded in Q-4.
- Agent list: only names and avatars reach non-creators; binding grants stay creator-only ([R28.10],
  [R32.05]).
- WebSocket: the new event carries only the room id, like `chatroom.updated`, so it discloses nothing
  to a guest; emission only on create or real rename bounds its rate to the guest-session creation
  rate limit.
- Prompt surface: guest names enter agent context through the same `_one_line_label` guard as every
  other label ([R13.34]).

## 8. Regression Test Plan

Written first, failing against current code:

1. Backend route tests (unit tier, `resolve_room_access` exercised through the real function with a
   patched repository layer, following `test_observer_agents.py:695-738`):
   - an anonymous guest gets 200 on the room record with `viewer_is_guest=true` and neutralised
     fields; 403 when guest links are off;
   - a project member in an owners-only room gets 403 on the room record, the roster and the agent
     list; a registered guest after guest links are turned off gets 403;
   - the roster includes guest-session entries with their names;
   - the agent list returns names to a guest and omits creator-only fields.
2. `backend/tests/integration/test_guest_identity_writes_db.py` (db tier): `guest_session_labels`
   returns the room's sessions only, excludes another room's, projects no hash.
3. Agent labels: `test_turn_participant_note.py` gains `TestRoomGuestNames` (merged map) and an F-5
   regression: two guest sessions resolve to "Alice" and "Bob", a purged id to "Guest", and a name
   with a newline is collapsed; `_provider_message` renders `Alice: hi`; the legend includes a live
   guest and omits a purged one.
4. Rename event: route test that a name change emits `chatroom.members_changed` after commit and a
   same-name resume does not.
5. Frontend:
   - `ChatroomView.test.ts` guest suite: no gear, export or Back for a guest whose room read fails;
     hidden for a registered guest; shown for a member; "(you)" and the edit icon in the non-tabbed
     rail at both breakpoints; no self-typing entry; other guests' messages labelled from the roster;
     rename refetches the roster.
   - New `ChatroomPresence.test.ts`; `ChatroomHeader.test.ts` covers `canSettings` and
     `canNavigateBack` (closing guest dossier FU-10).
   - `useChatroomSocket.test.ts`: `chatroom.members_changed` invalidates the roster for this room only;
     reconnect invalidates it.

## 9. Risks and Rollback

- **Stricter room reads.** Org members and refused project members lose the room record and roster
  for rooms they cannot open. Intended ([R13.32]); the settings view for such rooms was already
  unusable because every write 403s.
- **Roster growth.** Up to 1000 guest entries join the roster; bounded by the 50-active cap and the
  30-day purge in practice.
- **Event noise.** Bounded to session creation and real renames (Q-3).
- **Rollback.** No migration. Revert the commits.

## 10. Acceptance Criteria

- [ ] AC-1: an anonymous guest reads its room record (200, `viewer_is_guest=true`, neutralised fields)
  and its member roster; both return 403 when guest links are off.
- [ ] AC-2: a caller refused by the room's access flags gets 403 on the room record, the roster and the
  agent list ([R13.32]); a platform admin and a room moderator are unaffected.
- [ ] AC-3: the roster lists the room's guest sessions with their display names, and other viewers'
  message, presence and typing labels show those names.
- [ ] AC-4: renaming emits `chatroom.members_changed` after commit; other open clients show the new
  name without a reload; an unchanged-name resume emits nothing.
- [ ] AC-5: in an agent turn, two anonymous guests are labelled with their own names in the transcript
  and in the activity legend; a purged guest session is labelled `Guest` and absent from the legend.
- [ ] AC-6: a guest sees no settings gear, no export and no Back; a member still sees all three.
- [ ] AC-7: a guest sees "(you)" and the rename control in every participant-list layout, renames
  successfully, and does not see its own typing indicator.
- [ ] AC-8: a guest sees the room's agents by name and an @-mention of an agent name sends
  `mention_agent_ids`; creator-only binding fields are not exposed to non-creators.
- [ ] AC-9: backend and frontend lint, typecheck, tests, OpenAPI drift and build pass in CI.
- [ ] AC-10: on a running stack with two browsers (guest and member), steps 2-5 of §4 no longer
  reproduce. Left unticked, with the reason, if no stack is available.

## 11. SRS Delta

Amend [R13.19] by adding to its event list:

> - `chatroom.members_changed`: `{chatroom_id}` only; the room's participant roster changed (an
>   anonymous guest joined or renamed). Clients refetch the roster. Emitted after commit, only on an
>   actual change.

Amend the last sentence of [R13.33] to:

> Human labels resolve as the anonymous guest's session display name or the registered guest's room
> label, then account display name, then the login email; the email fallback exists so an agent can
> always tell two speakers apart and is confined to the model context, never to a surface other room
> members read. An author whose identity no longer resolves (a purged guest session) is labelled
> generically in the transcript and is absent from the activity legend ([R30.38]).

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** The guest-facing room record and roster are no longer reachable by callers the room flags
  refuse; the settings-form dossier's FU-10 and the guest dossier's FU-9 and FU-10 close when this
  ships.
- **FU-2.** Name collisions between guests, or between a guest and a member, are not disambiguated
  (Q-5). Revisit if the pilot shows agents confusing students.
- **FU-3.** A guest's session label in the JWT (`display_name` claim) lags a rename until the next
  token refresh; the client now uses the PUT's returned name, so nothing renders stale, but any server
  path reading the claim would.
