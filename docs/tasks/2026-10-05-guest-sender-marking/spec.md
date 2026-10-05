---
type: feature
status: approved
created: 2026-10-05
requirements: [R6.11, R13.06, R13.33, R13.34, R30.38]
depends_on: [2026-10-05-guest-frontend-session-lifecycle]
---

# Mark guest senders so a guest cannot present as the room owner or a member

## 1. Summary

Since `2026-10-05-guest-room-read-and-identity`, a guest's self-chosen name reaches every other
participant and every agent. Nothing distinguishes it from a member's name, so a holder of the
classroom guest link can join as the teacher's exact display name: classmates see an identical
author label, and the agent reads that guest's turns under the label its system note says belongs
to the room's owner. This feature appends a fixed, platform-applied guest marker to every label that
comes from a guest identity, in the UI (message author, participant list, typing indicator) and in
the model context (transcript prefixes, activity legend), and tells the agent what the marker means.
It implements FU-4 (and closes FU-2) of that dossier, a MEDIUM finding of its security audit.

## 2. Goals and Non-goals

**Goals**
- Every guest identity is visibly marked as a guest wherever room members read its label (message
  author, participant list, typing indicator). A guest identity is an anonymous guest session, or a
  registered guest (`chatroom_guests` row) who holds no role in the room's project, whatever label it
  shows (room label or account display name).
- The same labels carry a fixed guest marker in the model context (transcript speaker prefixes and
  the activity legend), applied after the [R13.34] one-line guard, and the system note's labelling
  sentence states what the marker means.
- The marker is applied by the platform, so a guest cannot remove it by choosing a name; a member's
  label never carries it.

**Non-goals**
- Rejecting or suffixing colliding names (Q-1). Two participants may still present the same string,
  as [R13.33] already accepts.
- Homoglyph or look-alike detection.
- Canvas presence cursors, which are client-asserted Yjs awareness and need their own design (FU-1).
- Chat export, research export and the teacher dashboard, which show no guest names today (§4.5).
- Changing who may read a room or what a guest may do.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Mark guests, reject collisions, or both? | Mark guests only. | Marking covers every collision class at once: a guest named as the owner, as a member who never spoke, as a member who renames later, as a bound agent, and look-alike spellings, with no migration and no new error path. Rejecting collisions needs reads of the owner, every member and every guest at each join and rename, races under concurrent joins (no unique index), misses silent members and homoglyphs, and contradicts [R13.33]'s accepted collisions more directly. |
| Q-2 | Where does the marker appear in the UI? | Message author, participant list and typing indicator. The roster API gains a `kind` per entry. | Presence and typing frames carry ids only (`backend/app/api/ws/chatroom.py:137,239,286-289,313-316`), so the client needs the kind from the roster; marking only messages would leave the participant list, where students look to see who is present, unmarked. |
| Q-3 | Are registered guests (signed-in users enrolled through `chatroom_guests`) marked? | Yes, when they hold no role in the room's project, whether their label is a room label or their account display name. A user who can already read the room is no longer enrolled as a registered guest when they choose "enter with my account" on the link. (Revised 2026-10-05 after review, requester's choice.) | Their label is self-chosen either way: the room label at enrolment (`backend/app/api/v1/guests.py:39-64`) overrides the account name (`backend/contexts/conversation/interfaces/author_labels.py:12-14`), and the account name is the user's own. The approved rule ("marked when the room label is set") missed a registered guest with no room label, and marked any member or owner who had clicked the link and chosen their account, because `chooseOwnAccount` (`frontend/src/slices/conversation/views/GuestLandingView.vue:170-176`) enrolls every signed-in user (`backend/contexts/conversation/application/guest_service.py:42-74`). Project roles are what make someone a member, so they decide. |
| Q-4 | What is the model-facing marker? | The fixed English suffix ` (guest)` after the normalised label, e.g. `Alice (guest): ...` and `u:1a2b3c4d = "Alice (guest)"`. | Fixed text is stable across turns (Q-5 of the prerequisite rejected shifting disambiguators). It is appended after `_one_line_label`, so truncation cannot cut it and it contains no delimiter the containers use ([R13.34]). Model-facing scaffolding is English throughout `turn_engine.py`. |
| Q-5 | Does this depend on `2026-10-05-guest-frontend-session-lifecycle` (approved, not built)? | Yes, by the requester's choice at approval: build it after the session-lifecycle dossier. | Both edit `ChatroomView.vue` and the conversation locale files. The regions are disjoint (sender labels, presence and typing here; socket close handling, banners and boot there), so this is an ordering preference rather than a hard technical prerequisite: reload recovery comes first for the pilot. |

## 4. Current State

### 4.1 Where guest names enter

- Anonymous: session create and resume (`backend/contexts/conversation/application/guest_session_service.py:88-198`)
  and rename (`:68-86`), each through `normalise_label` (`backend/shared_kernel/labels.py:49-62`).
  Neither compares the name with any other label.
- Registered: guest enrolment stores a self-chosen `display_name` (`backend/app/api/v1/guests.py:39-64`,
  `backend/contexts/conversation/application/guest_service.py:62-74`), which wins over the account name
  (`author_labels.py:12-14`).

### 4.2 Roster

`list_chatroom_members` (`backend/app/api/v1/chatrooms.py:1191-1228`) unions distinct user senders,
registered guests and anonymous guest sessions and returns `ChatroomMemberOut {user_id, display_name}`
(`:218-220`) with no kind, although the route knows each entry's source (`:1211-1227`).

### 4.3 Model context (`backend/contexts/agents/application/runtime/turn_engine.py`)

- `_room_guest_names` (`:3589-3607`) returns `{**sessions, **registered}`, one map of every guest
  identity's label, fetched once per turn (`:2696`).
- `_room_user_labels` (`:3609-3632`) resolves transcript speaker labels (guest label, then chat label,
  then the literal `Guest`); `_room_display_labels` (`:3634-3667`) resolves the activity legend and
  the owner label. Both pass labels through `_one_line_label` (`:406-423`).
- `_provider_message` (`:3699-3739`) renders `f"{label}: {content}"` for humans and agents alike
  (`:3726-3727,3733`), so a guest named like a bound agent also reads as that agent.
- The system note (`_ROOM_OWNER_NOTE`, `:377-382`; `_participant_note`, `:385-388`) states the
  labelling convention and names the owner; `_PARTICIPANT_NOTE_MEASURE` (`:403`) budgets its
  worst-case length.
- The legend is built in `backend/contexts/activities/application/activity_context_provider.py:146-164,234-274`
  through the injected `LabelResolver` (`:44`).

### 4.4 Frontend

- `ChatroomView.vue` builds `userNames` from the roster (`:627-639`); `senderName` (`:1305-1317`)
  branches on `sender_type`; typing names (`:1118-1124`) and presence rows (`:1126-1134`) are ids
  mapped through `userNames`.
- `ChatroomMessageBubble.vue:47-57` renders the avatar and `bubble__sender`; the message prop carries
  `sender_type` (`:271`).
- `ChatroomPresence.vue:7-71` and `ChatroomTypingIndicator.vue:23-34` receive plain names.
- `SBadge` (`frontend/src/shared/ui/SBadge.vue:5-21`, variants info/success/warning/danger/neutral,
  sizes sm/md) is the existing badge, used at `ChatroomListView.vue:269-276`.

### 4.5 Other surfaces, checked

Chat export labels by sender type and id prefix, no names (`chat_export_service.py:219-228,270`);
research export collapses guest rows to the literal `guest` (`research_export_builder.py:214-219`);
the dashboard watchlist uses subject codes (`StudentWatchlist.vue:17,35`); canvas comments show an id
prefix (`CanvasCommentPopover.vue:146`). None shows a guest's chosen name. Canvas cursors use the
guest JWT's `display_name` claim through client-asserted awareness (`useYjsProvider.ts:59-67`,
`CanvasRenderer.vue:177`).

## 5. Design

### Options considered

**Option A, mark guest labels**: append a platform-applied marker to every label from a guest
identity, in the UI and the model context. Covers every collision class, including look-alikes and
names that collide later; no migration; names may still repeat but a guest is always identifiable.

**Option B, reject colliding names**: compare a new guest name, case-folded, against the owner, all
members and all guests, and refuse a collision (409). Needs several reads per join and rename, races
under concurrent joins without a new unique index, misses members who never spoke and homoglyphs,
and breaks when a member renames after the guest joined.

**Option A plus an owner-name check**: Option A, and additionally reject a guest name equal to the
owner's display name. Adds an error path for the single most likely abuse, but the marker already
neutralises it, and look-alikes still pass.

### Decision

Option A (Q-1). The marker is applied where a label is resolved, from the knowledge of who the
participant is (Q-3: a guest session, or a registered guest without a project role), never from the
label text, so a guest cannot opt out and a member cannot opt in except by being a guest. Given up: two participants can still show the same name; the marker makes the
difference visible rather than preventing it.

## 6. Detailed Changes

- **Backend**
  - Conversation facade: one read, `guest_identity_ids(chatroom_id)`, returns the room's guest
    identities (Q-3): its anonymous session ids, plus its registered guests' user ids minus those
    holding a role in the room's project. The role test is one batched tenancy read with
    `roles_for` semantics (project members, org owners, owner of a user-owned project); a new
    `TenancyFacade` method if no batch form exists. Both the roster route and the turn engine use it.
  - `turn_engine.py`: a module constant `GUEST_LABEL_MARKER = " (guest)"` and a helper apply it, after
    `_one_line_label`, to the label of every id in `guest_identity_ids` in `_room_user_labels` and
    `_room_display_labels` (so transcript prefixes, the legend and the owner label all agree); labels
    of other ids are untouched even when they come from the registered-guest map. The generic fallback for a purged session stays
    `Guest`, unmarked. The labelling sentence in the system note gains a clause stating that a label
    ending in `(guest)` belongs to someone who joined through the room's guest link, that the platform
    adds it, and that a name without it is not thereby verified. `_PARTICIPANT_NOTE_MEASURE` is
    recomputed for the longer worst case.
  - `activity_context_provider.py`: no change; it receives marked labels from the injected resolver.
  - `chatrooms.py` `list_chatroom_members`: each entry carries `kind`: `"guest_session"` for an
    anonymous session, `"room_guest"` for a registered guest identity (Q-3), else `"member"`. The two
    guest kinds are distinct because removal (`2026-10-05-guest-kick-and-ban`) applies to sessions
    only.
  - `guest_service.py` `enroll`: a principal who already holds a role in the room's project is not
    written to `chatroom_guests`; the call succeeds and the client enters the room as itself.
  - Migration required: no.
- **API contract**: `ChatroomMemberOut` gains `kind: Literal["member", "guest_session", "room_guest"]`.
  `gen:api` rerun required: yes.
- **Frontend** (`slices/conversation`)
  - `ChatroomView.vue`: the roster map becomes `{name, kind}`; one `isGuestAuthor(id, senderType)`
    (true for `sender_type === 'guest'` or a roster kind other than `member`) feeds the bubble,
    presence and typing.
    An anonymous guest's message is marked even before the roster re-read lands, because its sender
    type says so.
  - `ChatroomMessageBubble.vue`: an `SBadge` (neutral, sm) after the sender name when the author is a
    guest.
  - `ChatroomPresence.vue`: the same badge on guest rows (the viewer's own row included).
  - `ChatroomTypingIndicator.vue`: guest names rendered through a `{name} ({guest})`-style i18n
    message rather than bare.
  - i18n (both `en.json` and `zh-TW.json`): a guest badge label and the typing-name pattern.
- **Deploy/config**: none.

## 7. NFR Checklist

- [ ] i18n: the badge and typing pattern go through `$t()` in both locales; the model-facing marker
  is fixed English scaffolding, like the rest of the system note.
- [ ] Audit log: N/A, no state change or new action.
- [ ] Tenant isolation: N/A, no new endpoint; the roster route keeps its room read gate.
- [ ] Error handling UX: N/A, no new error path.
- [ ] Performance: no new query; the roster route already reads all three sources.

## 8. Security Considerations

- **Agent manipulation** (check-security Part F, prompt surface): the marker is appended after
  `_one_line_label`, contains no delimiter the transcript, legend or note use ([R13.34]), and is
  decided by identity source, never by text, so it cannot be forged off or onto a label. A guest who
  names themselves `X (guest)` gains nothing: they are marked again.
- **Social engineering of classmates**: a guest is always badged in the message list, participant
  list and typing line; the badge is rendered by the client from the sender type or the roster kind,
  not from the name.
- **Disclosure**: `kind` tells room readers which participants are guests, which every room reader
  already infers (guest senders have `sender_type: guest`). No new identifier is exposed.
- **Residual**: two participants may still share a name ([R13.33]); canvas cursors remain
  client-asserted (FU-1).

## 9. Quality Notes

- **Existing debt**: `canvas.guest` is referenced (`CanvasCommentPopover.vue:146`) but missing from the
  canvas locale files (FU-2). `ChatroomView.vue:1137-1138` says a registered guest's room label is set
  by the owner; it is self-chosen at enrolment (§4.1) and the comment is corrected here. The
  `{**sessions, **registered}` merge loses which map an id came from; this design needs only "came
  from the guest map", so it is kept.
- **Patterns to follow**: [R13.34] guards in `_one_line_label` and the legend's `_one_line`;
  `SBadge` usage in `ChatroomListView.vue:269-276`; roster response shaping in
  `list_chatroom_members`; label tests in `backend/tests/unit/test_turn_participant_note.py`.
- **Reuse inventory**: `_room_guest_names`, `_one_line_label`, `prefer_guest_label`, `SBadge`,
  `convKeys.chatroomMembers`, the `chatroom.members_changed` invalidation (a new guest's kind arrives
  with the same re-read that brings its name).

## 10. Risks and Rollback

- **Label tests pin exact strings** (`test_turn_participant_note.py:258,270,282`,
  `test_agent_turn_loop.py:1165-1190`); they change deliberately, and the note-length measure must be
  recomputed or the budget test fails.
- **Roster object equality** in `test_guest_room_reads.py:170-179` changes with the new field.
- **Agents quoting labels**: an agent addressing "Alice (guest)" verbatim is acceptable; the note
  explains the suffix.
- **Rollback**: no migration; revert the commits. Old clients ignore the extra `kind` field.

## 11. Acceptance Criteria

- [ ] AC-1: an anonymous guest's messages, participant-list row and typing entry are badged as a guest
  for every viewer, including before the roster re-read after the guest joins (messages).
- [ ] AC-2: a registered guest with no role in the room's project is badged the same way, with or
  without a room label; a member, owner or admin is never badged, including one who once entered
  through the guest link with their account.
- [ ] AC-3: the roster returns `kind` per entry (`guest_session`, `room_guest` per Q-3, or `member`);
  a signed-in user with a project role who chooses "enter with my account" on the link creates no
  `chatroom_guests` row.
- [ ] AC-4: in an agent turn, transcript prefixes and activity-legend entries for guest identities end
  in ` (guest)`, applied after the one-line guard; members' labels do not; a purged session stays
  `Guest`.
- [ ] AC-5: a guest named exactly as the room owner appears to the agent as `<Owner name> (guest)`,
  distinct from the owner's label, and the system note explains the marker.
- [ ] AC-6: a guest whose chosen name already ends in `(guest)` or contains delimiters cannot remove or
  alter the marker.
- [ ] AC-7: backend and frontend lint, typecheck, tests, OpenAPI drift and build pass in CI.

## 12. Test Plan

- Backend unit (`test_turn_participant_note.py`, `test_agent_turn_loop.py`): marked labels for session
  and registered-guest ids; members unmarked; purged `Guest`; owner-name collision (AC-5); forged
  suffix and quote/newline names (AC-6); note-length budget with the marker.
- Backend unit (`test_guest_room_reads.py`): roster `kind` for each source (AC-3).
- Frontend component (`ChatroomViewGuest.test.ts`, `ChatroomPresence.test.ts`, a new
  `ChatroomTypingIndicator` case, `ChatroomMessageBubble` test): badges per AC-1 and AC-2, including a
  guest message whose sender is not yet in the roster.
- CI for AC-7.

## 13. SRS Delta

(Revised 2026-10-05 after review, Q-3; REQUIREMENTS.md updated to match.)

Append to [R13.33], after its human-label precedence sentence:

> The label of a guest identity (an anonymous guest session, or a registered guest who holds no role
> in the room's project, whichever label it shows) carries a fixed guest marker that the platform
> appends after the label is normalised ([R13.34]): in the model context the suffix ` (guest)`, and on
> every surface room members read (message author, participant list, typing indicator) a guest badge.
> The marker is decided by who the participant is, never by the label's text, so a guest cannot present as the room
> owner or a member by choosing their name, and the labelling sentence of the system context states
> what the marker means. Names may still coincide; the marker makes a guest identifiable rather than
> making names unique.

Append to [R30.38], after "a participant with no display name is absent from the legend":

> A guest's legend entry carries the same guest marker as its transcript label ([R13.33]).

## 14. Open Questions

None.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- **FU-1.** Canvas presence cursors take the name from the guest JWT's `display_name` claim through
  client-asserted Yjs awareness (`useYjsProvider.ts:59-67`, `CanvasRenderer.vue:177`), so any
  participant can show any name there and guests are unmarked. Needs a server-attested awareness
  identity; separate dossier.
- **FU-2.** `canvas.guest` is used (`CanvasCommentPopover.vue:146`) but missing from the canvas locale
  files, so the raw key renders.
- **FU-3.** Research export collapses every guest row to the literal `guest`
  (`research_export_builder.py:214-219`), so guests are indistinguishable from one another in the
  dataset; route to the `research-export-data-shape` dossier from the dashboard audit.
