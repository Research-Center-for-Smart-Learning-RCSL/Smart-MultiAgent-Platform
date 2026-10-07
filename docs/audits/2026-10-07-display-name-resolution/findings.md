---
type: audit
status: closed
created: 2026-10-07
requirements: [R6.11, R6.14, R6.15, R9.10, R13.17, R13.33, R13.34, R13.55, R28.06, R30.38]
---

# Audit: display-name resolution across room participants

## 1. Scope

- **Area** — every surface on which one chat-room participant sees another's name, and the
  rules that produce those names. People-facing: message author, participant list, typing
  indicator, mention autocomplete, released-observation header, message search, chat export,
  canvas awareness and canvas comments, avatars. Name sources: the room roster
  (`GET /api/chatrooms/{id}/members`), account display names (registration, profile update,
  admin provisioning, Google sign-in), anonymous guest session names, registered guest room
  labels, agent names. Model-facing: how the turn engine labels each speaker (members,
  registered and anonymous guests, other agents, system rows) in the transcript, the
  participant note, the activity legend, compaction summaries and A2A payloads.
- **Intent sources** — [R13.33] and [R13.34] (labels in the model context, label precedence,
  guest marker, normalisation at source and at render), [R13.55] (canvas awareness carries
  each editor's display name), [R6.11] (a guest enters a display name), [R6.14] and [R6.15]
  (Google sign-in issues the same session artifacts as a password login), [R28.06]
  (released observations), [R9.10] (what a compaction summary is), [R30.38] (activity
  legend), [R13.17] (chat export). UI intent: `docs/UI/07-conversation.md:571` (the typing
  indicator falls back to a truncated id only when no display name resolves) and `:615`
  (the participant list shows display names). The audit treats the eight-character id
  fallback as intended only where no name exists anywhere; showing an id while the server
  holds a name is a defect. Requirements say nothing about names in the chat export, the
  search panel or summaries, so findings there rest on internal consistency.
- **Depth** — thorough. Three initial investigation lenses (people-facing surfaces, name
  rules and storage, model context) produced 32 candidates; a fourth lens (rename
  propagation and refetch timing) was added during verification. Every candidate then went
  through one independent adversarial verification pass, in four groups, whose brief was to
  refute it. `normalise_label`, `_one_line_label` and `_to_history` were exercised by
  throwaway probe scripts; the asyncpg to SQLAlchemy error translation for an over-length
  string was checked against the installed SQLAlchemy 2.0.51. No Postgres instance or
  running stack was used.

## 2. Coverage

Read in full: the roster route and its sources (`app/api/v1/chatrooms.py`
`list_chatroom_members`, `contexts/conversation/application/room_guests.py`,
`GuestSessionRepository.list_labels`, `MessageRepository.distinct_user_sender_ids`), the
identity display-name paths (`contexts/identity/application/auth_service.py` registration,
profile update and Google completion; `admin_service.py` provisioning and hard delete;
`infrastructure/repositories.py` `get_display_names` and `get_chat_labels`;
`infrastructure/oauth/google.py`), `shared_kernel/labels.py`, the guest services, the
frontend name resolution in `slices/conversation/views/ChatroomView.vue`, the presence,
typing, bubble, search and settings components, `slices/canvas/composables/useYjsProvider.ts`
and the canvas renderer, `shared/ui/SAvatar.vue`, the turn engine's label and context-block
assembly (`contexts/agents/application/runtime/turn_engine.py`), `transcript.py`,
`summariser.py`, and the A2A handler. Sampled: the activities slice (only to confirm it
renders counts, not names), notification components, the knowledge/GraphRAG ingestion of
chat (only to confirm it carries no names). Not covered: the admin console's user lists,
org/project member lists, email templates, the research export (covered by its own tests as
deliberately pseudonymised), mobile layouts, and right-to-left rendering in a real browser
(the bidi observations below are code-level). Lenses not applied: concurrency between
roster refetch and WebSocket delivery beyond the presence and typing paths.

## 3. Findings

Ordered by severity.

## F-1: a Google name longer than 50 characters fails sign-in with a 500 and can lock the account out of Google sign-in

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/identity/infrastructure/oauth/google.py:231` (raw `name`
  claim), `backend/contexts/identity/application/auth_service.py:522-523` (existing user,
  no savepoint) and `:532-548` (new user, catches only `IntegrityError`),
  `backend/contexts/identity/infrastructure/tables.py:24` and
  `backend/alembic/versions/0033_user_display_name.py:26` (`users.display_name` is
  `VARCHAR(50)`), `auth_service.py:713` (`complete_google_callback` catches only
  `IdentityError`), `backend/app/api/v1/auth.py:679`.
- **Failure scenario**: a Google account whose profile name exceeds 50 code points signs in.
  Postgres rejects the insert or update with SQLSTATE 22001; asyncpg raises
  `StringDataRightTruncationError` (a `DataError`), which SQLAlchemy surfaces as a
  `DBAPIError`, not an `IntegrityError`, so neither handler catches it and the callback
  answers a JSON 500 instead of the redirect to the SPA, after the one-time OAuth state has
  already been consumed. On the existing-user branch the identity link inserted just before
  is rolled back with the request, so every retry takes the same branch and fails again.
- **Blast radius**: new sign-ups through Google, and existing accounts without a display
  name that sign in with or link Google, whenever the Google profile name is long (common
  for full names with middle names or titles).
- **Intent source**: [R6.14], [R6.15]

## F-2: a member who is in the room but has never sent a message appears as an eight-character id in the participant list

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/chatrooms.py:1220-1237` (roster is distinct message
  senders, `chatroom_guests` rows and guest sessions only),
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:442-444`
  (`presence.joined` updates presence only), `:437-441` and `:675-679` (the only roster
  invalidators besides the unknown-sender watcher),
  `frontend/src/slices/conversation/views/ChatroomView.vue:986-1000` (the watcher reacts to
  messages, not presence), `:1300`, `frontend/src/slices/conversation/components/ChatroomPresence.vue:162`;
  `backend/contexts/conversation/application/guest_service.py:68-73` (a member who opens
  the guest link is not enrolled, so gets no roster row either).
- **Failure scenario**: a student with display name "Alice" opens the room and reads
  without posting. Every other client lists her as `3f2a9c1e`; she sees her own id next to
  "(you)" although the session holds her name. The label stays an id for as long as she
  does not post. Because the mention autocomplete draws on the same roster
  (`ChatroomView.vue:767-773`), she also cannot be @mentioned through it.
- **Blast radius**: every viewer; affects every member and every registered guest without
  a room label who has not yet posted in the room, which in a class is most of the
  audience at the start of a session.
- **Intent source**: `docs/UI/07-conversation.md:615`; [R13.33] (the participant list is a
  surface room members read)

## F-3: canvas cursor labels show the full user UUID for members and registered guests

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/canvas/composables/useYjsProvider.ts:67-69`
  (`claims.display_name ?? claims.email ?? userId`), `backend/shared_kernel/auth/jwt.py:78-90`
  (member access tokens carry neither claim; no caller passes either through `extra`,
  `auth_service.py:360,760`, `impersonation_service.py:81`),
  `backend/app/api/ws/canvas.py:254-262` (awareness is passed through unchanged),
  `frontend/src/slices/canvas/components/CanvasRenderer.vue:177`.
- **Failure scenario**: two members open the room canvas; each sees the other's cursor
  labelled with a 36-character UUID. Anonymous guests are labelled correctly because the
  guest token carries `display_name` (`jwt.py:205`).
- **Blast radius**: every canvas editor, for every member and registered guest.
- **Intent source**: [R13.55]

## F-4: every system message, not only compaction summaries, is lifted into the system prompt as an "Earlier conversation summary"

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/transcript.py:73-79` (sender
  type `SYSTEM` maps to role `system`) and `:142` (only the compact-summary check precedes
  that mapping), `:218-219` (system rows survive history loading),
  `backend/contexts/agents/application/runtime/turn_engine.py:2799-2803` (every
  `role == "system"` row becomes `[Earlier conversation summary]` in the system prompt) and
  `:2878` (and is dropped from the message stream), `:4739-4744` (the newest system row also
  becomes the summary retrieval query);
  `backend/contexts/conversation/application/observation_service.py:183-189` (released
  observations are system messages),
  `backend/contexts/activities/application/submission_service.py:317-334, 640-678` (activity
  echoes are system messages and can carry a digest of participant-written content).
- **Failure scenario**: the room owner releases an observer's analysis to the room. On the
  next turn the agent reads it not where it occurred in the conversation but at the top of
  the system prompt, framed as a summary of earlier conversation; an activity echo carrying
  a participant's submitted text is framed the same way, at summary-level trust. A room that
  has also been compacted presents several unrelated blocks all labelled as the summary.
- **Blast radius**: every agent turn in a room that has released observations or activity
  echoes; the agent misattributes and misorders them, and participant-authored content gains
  system-prompt standing (security aspect routed to FU-6). No test covers a system row that
  is not a summary (fixtures in `test_agent_runtime_transcript.py:79,144` and
  `test_turn_context_budget.py:656` all use `compact_summary`).
- **Intent source**: [R28.06] (a released observation follows the standard broadcast and
  wake-up paths), [R9.10] (a summary is what `/compact` writes)

## F-5: the typing indicator shows an eight-character id while a member types a first message

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:1281`,
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:449-451` (`typing.start`
  does not refetch the roster); root cause shared with F-2.
- **Failure scenario**: a member who has not posted starts typing; everyone sees
  "3f2a9c1e is typing" until the message lands and the unknown-sender watcher refetches.
- **Blast radius**: every viewer, transiently, once per member per room.
- **Intent source**: `docs/UI/07-conversation.md:571`

## F-6: Google profile names bypass the source normalisation every other display-name path applies

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/identity/infrastructure/oauth/google.py:231`,
  `backend/contexts/identity/application/auth_service.py:523,539`,
  `backend/contexts/identity/infrastructure/repositories.py:74,116` (no normalisation in the
  repository); contrast `auth_service.py:989` (profile update), `admin_service.py:187`
  (provisioning), `guest_session_service.py:128,169`, `guest_service.py:87`, all through
  `shared_kernel/labels.py` `normalise_label`. No test covers a name with control
  characters (`tests/unit/test_auth_service_oauth.py` uses "New Name").
- **Failure scenario**: a Google profile name carrying bidi overrides or other format
  characters is stored as is and shown on every surface; in the model context
  `_one_line_label` (`turn_engine.py:437`) still collapses whitespace, so a newline cannot
  open a fake turn, but format characters survive.
- **Blast radius**: Google sign-ups and accounts that gain their display name from Google.
- **Intent source**: [R13.34] (account display names are normalised at the source)

## F-7: names that render as blank pass validation

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/shared_kernel/labels.py:58-61` (strips only Unicode category C,
  whitelists ZWJ and VS16 at `:39`); probe: U+3164, U+FFA0, U+2800, a name of combining
  marks only, ZWJ only, VS16 only and ZWJ+VS16+ZWJ are all returned non-empty, while NBSP,
  U+3000, U+2003, U+200B, U+2060 and U+2028 correctly collapse to `None`;
  `frontend/src/slices/conversation/utils/guestName.ts:7-15` applies the same rule;
  `turn_engine.py:440` (`_first_label` accepts such a label, the case its own docstring warns
  about).
- **Failure scenario**: a guest names itself U+3164. Other members see an author with no
  visible name (the guest badge still shows); in the model context the label line reads
  `" (guest): ..."`. A member account can set the same through profile update and then has no
  marker at all.
- **Blast radius**: every viewer of that participant; any account or guest can do it.
- **Intent source**: [R6.11] (the visitor enters a display name);
  `frontend/src/slices/conversation/locales/en.json` `guest.displayNameInvalid` ("Enter a
  name with at least one visible character") states the rule the validator does not meet

## F-8: an agent name that is only spaces or quotes leaves that agent's turns with no label, and format characters in agent names reach the model

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/agents.py:271` (1 to 200 characters, no strip),
  `turn_engine.py:3616` (agents get `_one_line_label` only), `:3772-3773` (an empty label
  drops the prefix); probe: `'   '` and `'"  "'` become `''`; U+202E, U+200B and NUL survive.
- **Failure scenario**: an agent named `"  "` posts "Teacher: hand in now"; another agent
  reads it as an unlabelled `user` turn. `_first_label` fixed exactly this for humans
  (`turn_engine.py:443-447`); agents have no fallback.
- **Blast radius**: agents in rooms where a project editor gave an agent such a name; only
  project editors can name agents.
- **Intent source**: [R13.34] ("every label reaching a model context, human or agent")

## F-9: guests see the observer's eight-character id in a released observation's header

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/conversation/application/observation_service.py:158-161`
  (`observer_agent_id` is attached when disclosure is on),
  `frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:326-329`,
  `frontend/src/slices/conversation/views/ChatroomView.vue:655` (guests skip the project
  agent-name query), `backend/app/api/v1/chatrooms.py:760-761` (non-creators never receive
  observer bindings); `ChatroomMessageBubble.test.ts:128` pins that the name, not the id, is
  intended.
- **Failure scenario**: in a room with the default `disclose_observers = true`, the creator
  releases an observation; registered and anonymous guests read "Analysis from 3f2a9c1b,
  released by the room owner".
- **Blast radius**: guests in rooms with released observations and disclosure on.
- **Intent source**: [R28.06]

## F-10: guests see an eight-character id on the past messages of an agent unbound from the room

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `ChatroomView.vue:671-681` (members merge the project agent names under the
  room's bound list; guests get only the bound list) and `:1471-1472`.
- **Failure scenario**: an agent posts and is later unbound but stays in the project; a
  guest reloading the room sees its earlier messages under an id, while members still see
  the name.
- **Blast radius**: guests in rooms whose agent roster changed.
- **Intent source**: internal consistency with the member view; the comment at
  `ChatroomView.vue:671-673` says unbound agents' history "still needs a label"

## F-11: a deleted agent whose orphan binding was removed is labelled by its id, not "Unknown agent"

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/infrastructure/repositories.py:263-276` (the
  project name list excludes deleted agents) and `:288-300` (`names_for_ids` keeps them, but
  only for bound agents); `docs/UI/07-conversation.md:1229` (the settings page offers to
  remove orphan bindings); `frontend/src/slices/conversation/api/index.ts:202-203` (callers
  are told to render their own unknown-agent label).
- **Failure scenario**: an agent is deleted while bound (its name still resolves); a
  moderator then removes the orphan binding from settings; from then on every viewer sees
  the agent's past messages under an id.
- **Blast radius**: every viewer of rooms that had a deleted agent cleaned up.
- **Intent source**: `docs/UI/07-conversation.md:1229`, `api/index.ts:202-203`

## F-12: message search results show every sender as an id

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/components/ChatroomSearchPanel.vue:56`
  and its props at `:85-89` (no names are passed), `useChatroomSearch.ts:32`; the backend
  `SearchHit` carries no name (`backend/app/api/v1/search.py:24-27, 61-64`); no test pins the
  label (`ChatroomSearchPanel.test.ts` uses `sender_id: 'u1'`).
- **Failure scenario**: any search lists hits as `3f2a9c1e · <time>`, for users and agents,
  although the view holds both name maps.
- **Blast radius**: every reader who searches.
- **Intent source**: internal consistency (every other author surface resolves names)

## F-13: the chat export labels every sender as its type and id

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/conversation/application/chat_export_service.py:219-228`
  (`user (abcd1234)`), manifest at `:124-165` (only `sender_type` and `sender_id`); the
  no-names test (`test_research_export_builder.py:118`) belongs to the separate research
  export, not this one.
- **Failure scenario**: a moderator exports a class discussion; the Markdown and PDF
  transcripts cannot be read as a conversation between named people.
- **Blast radius**: moderators and owners who export.
- **Intent source**: [R13.17] is silent on names; the finding rests on the export being a
  readable transcript

## F-14: avatar initials break for names that start with an emoji or an astral character

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/ui/SAvatar.vue:18` (`charAt(0)` takes half a surrogate
  pair); `shared_kernel/labels.py:29-39` deliberately keeps emoji.
- **Failure scenario**: a guest named with a leading emoji shows a replacement glyph as its
  avatar initial in bubbles, the participant list and agent status.
- **Blast radius**: every viewer of such participants; guests often choose emoji names.
- **Intent source**: internal consistency with the label rule that keeps emoji

## F-15: renames do not reach open rooms

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: no identity path publishes to room channels after a profile update
  (`auth_service.py:982-989`); the roster refreshes only on focus, reconnect or remount;
  canvas awareness is set once on connect (`useYjsProvider.ts:150`) and is not re-run after
  a guest rename (`guests.py:209-210` emits `members_changed` for the chat roster only).
- **Failure scenario**: a member renames themselves; a classmate in a focused tab keeps
  seeing the old name in bubbles, presence and typing until the tab loses and regains focus.
  A guest renames; canvas cursors keep the old name until the canvas socket reconnects.
- **Blast radius**: every viewer, until their next refetch.
- **Intent source**: [R13.33] (labels are what participants choose for themselves; the
  guest rename path already announces renames, the member path does not)

## F-16: messages of a purged guest session show an id in the room while the model context labels them generically

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `backend/app/workers/tasks/guest_cleanup.py:17-27`,
  `backend/contexts/conversation/infrastructure/repositories/guest_session_repo.py:202-206`,
  roster from live rows only (`room_guests.py:54`, `chatrooms.py:1234-1237`); the model side
  uses `Guest (guest)` (`turn_engine.py:3607, 3663`) as [R13.33] requires; the only stored
  copy of a name is `chatroom_guest_bans.display_name`, for banned guests.
- **Failure scenario**: a room reused across terms: guests from a session 30 days idle now
  appear on their old messages as an id with the guest badge.
- **Blast radius**: long-lived rooms with guest participation.
- **Intent source**: [R13.33] specifies the generic label for the transcript only; the room
  surface is unspecified, so this is partly a requirements gap

## F-17: label truncation can split a grapheme cluster

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/shared_kernel/labels.py:61` (`[:max_len]` on code points); probe: a
  ZWJ family emoji cut leaves a dangling ZWJ, and a flag is cut to one regional indicator.
- **Failure scenario**: today barely reachable, because every API field's `max_length` equals
  the cap and counts code points (`guests.py:40,73,179`, `auth.py:225`, `admin_users.py:61`);
  it becomes reachable as soon as F-1 and F-6 are fixed by truncating Google names to 50.
- **Blast radius**: names truncated by the server.
- **Intent source**: `shared_kernel/labels.py` docstring (printable Unicode, including emoji,
  is kept)

## 4. Refuted Candidates

- Canvas comment authors shown as ids (`CanvasCommentPopover.vue:146`): the component is
  not mounted anywhere (`canvas/index.ts` exports only `CanvasPanel`); latent, see FU-8.
- Hard-deleted members' messages shown as ids (`admin_service.py:470-515`): hard delete is
  the 60-day GDPR purge and losing the name fits its erasure intent.
- Roster caps of 1000 senders and 1000 guest sessions: documented at
  `message_repo.py:175-179` and `guest_session_repo.py:68-81`.
- Accounts with no display name shown as ids everywhere: documented behaviour; the
  requirements expect unnamed accounts ([R13.33] omits the owner sentence for an unnamed
  creator, [R30.38] leaves unnamed participants out of the legend,
  `docs/UI/07-conversation.md:571` documents the fallback). Recorded as a requirements
  question, FU-2.
- `prefer_guest_label` letting `""` beat the account name: blank labels are stored as `None`
  since `guest_service.py:87`; only rows written before that guard could hold `""`.
- Different empty-name handling between guest sessions, enrolment and profile update: each
  matches its documented meaning.
- No NFC/NFKC normalisation or confusables check: [R13.33] states that labels are not
  authentication and that two participants may present the same string.
- Zalgo names (a base character with up to 99 combining marks): kept by the stated intent
  of `normalise_label` (printable Unicode is kept); the effect is cosmetic.
- Login email as the agent-facing label of an unnamed member
  (`identity/infrastructure/repositories.py:137-153`): required by [R13.33]; the owner note
  and the activity legend never see the email. The residual risk of an agent repeating it is
  FU-1.
- Unnamed members absent from the activity legend: required by [R30.38] and pinned by
  `test_turn_participant_note.py:229, 398`.
- Former members labelled `Guest` and purged guests merged as `Guest (guest)` in the model
  context: required by [R13.33] and pinned by `test_turn_participant_note.py:271, 439`.
- Message bodies fabricating labelled lines: [R13.34] governs labels, and each message is
  its own provider message; the residual is FU-5.

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
Triaged with the requester on 2026-10-07: every finding but F-16 is to be fixed, grouped
into six bugfix dossiers by root cause, all approved the same day.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix | `docs/tasks/2026-10-07-google-display-name-normalisation/` |
| F-2 | fix | `docs/tasks/2026-10-07-room-roster-completeness/` |
| F-3 | fix | `docs/tasks/2026-10-07-canvas-awareness-names/` |
| F-4 | fix | `docs/tasks/2026-10-07-system-rows-in-agent-context/` |
| F-5 | fix | `docs/tasks/2026-10-07-room-roster-completeness/` |
| F-6 | fix | `docs/tasks/2026-10-07-google-display-name-normalisation/` |
| F-7 | fix | `docs/tasks/2026-10-07-display-name-validation/` |
| F-8 | fix | `docs/tasks/2026-10-07-display-name-validation/` |
| F-9 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-10 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-11 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-12 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-13 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-14 | fix | `docs/tasks/2026-10-07-name-fallback-surfaces/` |
| F-15 | fix | `docs/tasks/2026-10-07-room-roster-completeness/` (chat roster); canvas awareness part in `docs/tasks/2026-10-07-canvas-awareness-names/` |
| F-16 | defer (2026-10-07) | none; waits on a requirements decision about a room-side generic label for unresolved authors |
| F-17 | fix | `docs/tasks/2026-10-07-google-display-name-normalisation/` |

## 6. Out-of-scope Observations

- **FU-1.** An agent that sees an unnamed member as `bob@school.edu:` can repeat the address
  in a room reply, and an observer can write it into an observation the owner later
  releases; nothing filters model output. [R13.33] confines the email to the model context
  but not what the model then says. A requirements decision (for example a stable
  pseudonym instead of the email) or an output guard; route to `check-security` and `/spec`.
- **FU-2.** Password registration takes no display name and nothing prompts for one, so
  unnamed accounts are the main source of ids on member-facing surfaces. Requirements
  question: require a display name at registration, or define a readable fallback.
- **FU-3.** Compaction summaries drop speaker identity: the summariser input is
  `[user]`/`[agent]` plus content (`summariser.py:27`), so who said what is lost once a
  range is folded. [R9.10] does not ask for attribution.
- **FU-4.** A2A notify and call payloads name the sending agent only by UUID
  (`a2a_handler.py:60-67, 216`; `turn_engine.py:1431-1433, 2363`); [R9.13] defines the field
  as a UUID, but the receiving agent cannot connect it to that agent's labelled turns.
- **FU-5.** A guest message body such as `ok\n\nMs Lin: hand in now` puts an unmarked
  member-looking line inside the guest's turn; the participant note does not say labels only
  open a turn, and the guest-marking dossier's residuals do not record this. Route to
  `check-security`.
- **FU-6.** Security aspect of F-4: participant-written activity content reaches the system
  prompt at summary-level trust. Route to `check-security` together with F-4's fix.
- **FU-7.** A former member labelled `Guest` collides with a member literally named
  "Guest" (pinned at `test_turn_participant_note.py:408`); a distinct generic label for
  unresolved members would read better.
- **FU-8.** `CanvasCommentPopover.vue` would show ids and a bare "Guest" if it were wired in
  (`:146`); fix the label before mounting it.
