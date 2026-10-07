---
type: bugfix
status: in-progress
created: 2026-10-07
requirements: [R13.19, R13.33]
depends_on: []
---

# Name every participant who is present in a room, and keep names current

## 1. Summary

The room roster that names people in the participant list, the typing indicator and the
mention autocomplete covers only those who have sent a message, registered guests and
anonymous guest sessions. A member who opens a room and reads without posting, which at the
start of a class is most of the audience, is therefore listed by the first eight characters
of their user id, typing a first message shows "3f2a9c1e is typing", and that person cannot
be found in the mention autocomplete. Separately, a member who changes their display name
keeps the old name in every room already open on other people's screens. This dossier fixes
findings F-2, F-5 and the chat-roster part of F-15 of
`docs/audits/2026-10-07-display-name-resolution/findings.md`; the canvas part of F-15 belongs
to `2026-10-07-canvas-awareness-names`.

## 2. Observed vs Expected

- **Observed** — `list_chatroom_members` unions distinct message senders, every
  `chatroom_guests` row and the room's guest sessions
  (`backend/app/api/v1/chatrooms.py:1220-1237`). A member who has not posted is in none of
  these, and a member who opens the guest link is not enrolled either
  (`backend/contexts/conversation/application/guest_service.py:68-73`). On the client the
  roster is re-read only on `chatroom.members_changed`, on reconnect
  (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:437-441, 675-679`) and
  when a message from an unknown sender arrives
  (`frontend/src/slices/conversation/views/ChatroomView.vue:986-1000`); `presence.joined` and
  `typing.start` only update the store (`useChatroomSocket.ts:442-451`). The participant list
  and the typing indicator then fall back to the id
  (`ChatroomView.vue:1281, 1300`; `components/ChatroomPresence.vue:162`), and the mention
  autocomplete leaves unnamed users out (`ChatroomView.vue:767-773`). A profile rename
  (`backend/app/api/v1/auth.py:532-537` → `AuthService.update_display_name`,
  `backend/contexts/identity/application/auth_service.py:974-990`) publishes nothing to any
  room channel, so open rooms keep the old name until the viewer's tab regains focus.
- **Expected** — the participant list shows display names
  (`docs/UI/07-conversation.md:615`); the typing indicator falls back to a truncated id only
  when no display name resolves (`:571`); the participant list and typing indicator are
  surfaces room members read ([R13.33]), and room viewers are told to re-read the roster when
  it changes ([R13.19], as the guest rename path already does,
  `backend/app/api/v1/guests.py:209-210, 214-231`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does a present-but-silent member's name come from? | The roster also includes everyone currently present in the room (`PresenceTracker.list_room`), and the client re-reads the roster once per id it cannot name, when that id appears in presence or in the typing set. | Requester's choice. It names only people already visible as present, so the roster reveals nothing a viewer cannot already see as an id; the frames stay ids-only, which the room channel requires (no per-recipient filtering). Listing every eligible reader was rejected because it names people who never entered and can be large; a per-id lookup endpoint was rejected as new API surface needing its own participant check. |
| Q-2 | How does a member's rename reach open rooms? | After a profile rename, emit `chatroom.members_changed` to every room the user is currently present in, using the presence tracker's user-to-rooms index. | Requester's choice. Those are the rooms where the stale name is on screen next to a live participant. Rooms where the user only posted earlier pick up the name on the next focus or load; emitting to them was rejected as an unbounded fan-out per rename. |
| Q-3 | Does this depend on another dossier? | `depends_on: []`. | No dossier that is not implemented touches the roster route, `ChatroomView.vue`'s roster handling, `useChatroomSocket.ts` or the profile route. `2026-10-07-canvas-awareness-names` and `2026-10-07-name-fallback-surfaces` from the same audit read the roster and edit `ChatroomView.vue`, so they will list this one in their `depends_on`. |

## 4. Reproduction

1. A project with a room open to project members; members A ("Alice") and B.
2. A opens the room and posts nothing. In B's room, A's participant row reads as eight hex
   characters; B's mention autocomplete does not offer Alice.
3. A starts typing a first message. B sees "<8 hex> is typing".
4. A sends the message; B's view now names Alice (the unknown-sender re-read).
5. A changes the display name to "Alicia" in the profile while B keeps the room focused. B
   still sees "Alice" on A's participant row and messages until B's tab loses and regains
   focus.

## 5. Root Cause Analysis

1. **Root cause (F-2, F-5):** the roster's id set is built from message authorship and guest
   records only (`chatrooms.py:1220-1237`), so presence, which is how a silent participant
   becomes visible, never contributes a name.
2. Aggravating: the client's only trigger for a re-read on a new id is an incoming message
   (`ChatroomView.vue:986-1000`), so even a roster that could name present users would not be
   asked again when they join.
3. **Root cause (F-15, chat):** the profile rename path ends at the identity repository
   (`auth_service.py:990`) and nothing tells the conversation side that a participant's name
   changed, while the guest rename path does (`guests.py:209-210`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every viewer of every room, for every member, and every registered
  guest without a room label, who has not posted in that room; transiently every member's
  first typing burst; every open room after a member rename.
- **Sibling suspects**:
  - Registered guests: always in the roster through `chatroom_guests` — cleared for the
    participant list; their label cannot change (no rename endpoint) — cleared for F-15.
  - Anonymous guests: always in the roster through their session, and their rename already
    emits — cleared.
  - Admins viewing a room: present, not posting, not enrolled — same defect, fixed by the same
    change.
  - Agents: named from the agent lists, not the roster (`ChatroomView.vue:671-681`) — not
    affected by this defect; their fallbacks are in `2026-10-07-name-fallback-surfaces`.
  - Canvas awareness names: a separate source (token claims) —
    `2026-10-07-canvas-awareness-names`.
  - Other writers of `users.display_name`: admin provisioning sets a name only at creation
    (`admin_service.py:187`); Google sign-in sets one only when the account has none
    (`auth_service.py:522-523`) — both happen before the user can be present in a room, so
    neither needs the rename event. Cleared.

## 7. Fix Design

1. **Roster includes present users.** `list_chatroom_members` adds
   `PresenceTracker().list_room(chatroom_id)` to the user ids it resolves, minus the room's
   guest session ids (already listed with `kind: "guest_session"`), with `kind` decided as
   today (`room_guest` for unaffiliated registered guests, else `member`). The presence read
   reconciles ghosts already (`contexts/conversation/infrastructure/presence.py:243-246`).
   The route already gates on `ensure_can_read`, and presence holds only principals that
   passed the same gate at their handshake.
2. **Client re-reads for unknown present or typing ids.** Generalise the unknown-sender
   watcher in `ChatroomView.vue` so that ids from the room's presence set and typing set are
   checked against `userNames` the same way, sharing the one-attempt-per-id set, so an
   account with no display name is asked about once and then left alone.
3. **Rename reaches open rooms.** A conversation facade method (for example
   `ConversationFacade.announce_participant_renamed(user_id)`) reads the user's rooms from
   the presence tracker's reverse index (`presence.py:60-61, 120`; a reader method is added
   beside `list_room`) and emits `chatroom.members_changed` to each room channel after
   commit, ids only and with transport failure swallowed, as `guests._emit_members_changed`
   does (`guests.py:214-231`). The profile route calls it when the stored name actually
   changed (`auth.py:532-537`).

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/unit/test_guest_roster_events.py` or a new
  `backend/tests/unit/test_room_roster.py`: with the presence tracker stubbed to report a
  member id that has never posted, the members route returns that id with its display name
  and `kind: "member"`; it fails today because presence is not read. A second case asserts a
  present anonymous guest is listed once, as `guest_session`.
- A profile-route test: a rename by a user the presence index places in two rooms emits
  `chatroom.members_changed` on both room channels after commit, and an unchanged name emits
  nothing; it fails today because nothing is emitted.
- `frontend/src/slices/conversation/__tests__/ChatroomViewGuest.test.ts` (or a new room-view
  test): an id added to the presence set that the roster does not name triggers one roster
  re-read, and the participant row then shows the returned name; the same for an id added
  to the typing set. It fails today because only messages trigger the re-read.

## 9. Risks and Rollback

- The roster grows by the room's present users; one extra Redis read per roster request,
  and one extra roster request per newly seen id on each client. Both are bounded by the
  room's live audience.
- A stale entry in the user-to-rooms index only causes a harmless extra
  `members_changed` frame.
- Rollback: revert; no schema or data change.

## 10. Acceptance Criteria

- [ ] AC-1: the regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: a member who is present in a room and has a display name is shown by that name in
  every viewer's participant list and mention autocomplete without having posted, including
  in their own row.
- [ ] AC-3: the typing indicator names a member with a display name from their first
  keystroke.
- [ ] AC-4: after a member renames themselves, every room they are present in shows the new
  name to every viewer without a reload or refocus.
- [ ] AC-5: an account with no display name causes at most one roster re-read per viewer, and
  members' frames on the room channel remain ids only.
- [ ] AC-6: backend and frontend lint, typecheck, tests and build pass in CI.

## 11. SRS Delta

None. The participant list and typing indicator already name participants by display name
(`docs/UI/07-conversation.md:571, 615`; [R13.33]).

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** Viewers of a room where a renamed member only posted earlier (and is not present
  now) keep the old name until their next focus or load (Q-2).
- **FU-2.** The roster still has its 1000-author cap with an arbitrary drop order
  (`backend/contexts/conversation/infrastructure/repositories/message_repo.py:168-194`), a
  documented limitation the audit did not count as a defect.
