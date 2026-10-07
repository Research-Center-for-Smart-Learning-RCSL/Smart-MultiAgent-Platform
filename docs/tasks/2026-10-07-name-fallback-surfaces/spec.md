---
type: bugfix
status: approved
created: 2026-10-07
requirements: [R13.17, R13.33, R28.06, R28.09, R28.10]
depends_on: [2026-10-07-room-roster-completeness]
---

# Resolve names on the remaining surfaces that show ids

## 1. Summary

Several surfaces show an id where a name is known somewhere. Guests see an eight-character
id instead of the agent name in a released observation's header and on the past messages of
an agent no longer bound to the room; once a deleted agent's leftover binding is removed,
every viewer sees its past messages under an id; message search results label every hit
with the sender's id; the chat export labels every message as `user (abcd1234)` or
`agent (…)`; and an avatar whose name starts with an emoji shows a broken glyph, because it
takes half of a surrogate pair as its initial. This dossier fixes findings F-9 to F-14 of
`docs/audits/2026-10-07-display-name-resolution/findings.md`.

## 2. Observed vs Expected

- **Observed**:
  - Agent names on the room view come from the project's agent names, which guests skip
    (`frontend/src/slices/conversation/views/ChatroomView.vue:648-669`), layered under the
    room's bound agents (`:671-681`); the room agent list drops observer bindings for anyone
    but the creator (`backend/app/api/v1/chatrooms.py:757-761`), and the project name list
    excludes deleted agents (`backend/contexts/agents/infrastructure/repositories.py:263-276`).
    The bubble header of a released observation resolves `observer_agent_id` through that map
    and falls back to `observer.slice(0, 8)`
    (`frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:326-329`); message
    authors fall back the same way (`ChatroomView.vue:1471-1472`).
  - Search hits are labelled `h.sender_id.slice(0, 8)`
    (`frontend/src/slices/conversation/components/ChatroomSearchPanel.vue:56`); the panel
    receives no names (`:85-89`, `composables/useChatroomSearch.ts:32`) and the backend hit
    carries none (`backend/app/api/v1/search.py:24-27, 61-64`).
  - The export manifest carries only `sender_type` and `sender_id`
    (`backend/contexts/conversation/application/chat_export_service.py:124-133`), and the
    Markdown and PDF transcripts label each message `<type> (<8 chars of id>)` (`:218-228`).
  - `SAvatar` computes its initial as `props.name.charAt(0)`
    (`frontend/src/shared/ui/SAvatar.vue:18`), while names deliberately keep emoji
    (`backend/shared_kernel/labels.py:29-39`).
- **Expected** — a released observation carries the observer's identity when disclosure is
  on, so it can be shown ([R28.06], [R28.09]); `ChatroomMessageBubble.test.ts:128` pins
  "resolves observer_agent_id against agentNames instead of showing a raw id". Deleted agents
  keep their names in history (`repositories.py:288-293`, `AgentsFacade.agent_names`,
  `backend/contexts/agents/interfaces/facade.py:146-154`), and a caller with no name renders
  an unknown-agent label (`frontend/src/slices/conversation/api/index.ts:202-203`;
  `docs/UI/07-conversation.md:1229`). People are named by display name on surfaces other
  members read, never by email ([R13.33]). The export is a readable room history ([R13.17]).
  Observer bindings stay hidden from non-creators ([R28.10]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where do guests, and members for deleted agents, get names of agents not currently bound? | A room-gated agent-label read returning names for every agent that authored a message in the room and for the observer named in a released observation, resolved through `AgentsFacade.agent_names`. | Requester's choice. It names only ids a reader of the room's history already sees, and `observer_agent_id` is present only when disclosure was on at release (`backend/contexts/conversation/application/observation_service.py:158-161`), so no undisclosed observer is revealed ([R28.10]). Extending the room agent list was rejected because every consumer of the bound list (sidebar, mentions, status) would have to learn to skip non-bound rows; an unknown-agent label alone was rejected because guests would still lose names members see. |
| Q-2 | How is a deleted agent's past message labelled? | By its stored name; "Unknown agent" only when no name exists at all. | Requester's choice; the documented intent of `names_for_ids`. |
| Q-3 | Which export formats carry sender names? | All three: Markdown and PDF label each message with the sender's name, and the JSON manifest gains `sender_name` next to `sender_id`. | Requester's choice. The name is the agent's name, the anonymous guest's session name or registered guest's room label, or the account display name, never an email; the id is kept as the fallback when no name exists. |
| Q-4 | Does this depend on another dossier? | `depends_on: [2026-10-07-room-roster-completeness]`. | Overlap prerequisite: both edit `ChatroomView.vue`'s name maps and their refetch logic; building this second avoids conflicting diffs. |

## 4. Reproduction

1. A room with disclosure on, an observer agent and a bound agent "Tutor". The creator
   releases an observation; an anonymous guest sees "Analysis from 3f2a9c1b, released by the
   room owner".
2. Tutor posts, then is unbound (still in the project). The guest reloads: Tutor's messages
   show an id; a member still sees "Tutor".
3. Delete Tutor while bound, then remove the orphan binding from settings. Every viewer sees
   Tutor's messages under an id.
4. Search the room for a word Tutor or a member wrote: every hit reads `<8 chars> · <time>`.
5. Export the room as Markdown: every message is labelled `user (abcd1234) — <time>` or
   `agent (…)`.
6. A guest named "🦊 Fox" posts: the avatar initial renders as a replacement glyph.

## 5. Root Cause Analysis

1. **F-9, F-10, F-11:** the room view has no source that names an agent outside the room's
   current non-observer bindings for guests, nor a deleted unbound agent for anyone
   (`ChatroomView.vue:648-681`, `chatrooms.py:757-761`, `repositories.py:263-276`); the
   label falls back to the id rather than to an unknown-agent label (`ChatroomView.vue:1472`,
   `ChatroomMessageBubble.vue:328`).
2. **F-12:** the search panel is given hits only; the view's name maps never reach it
   (`ChatroomSearchPanel.vue:85-89`).
3. **F-13:** the export never resolves names; `_sender_label` formats the type and id
   (`chat_export_service.py:218-228`).
4. **F-14:** `charAt(0)` works on UTF-16 code units, not on characters a reader sees
   (`SAvatar.vue:18`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — guests in rooms with released observations or a changed agent roster;
  all viewers of rooms with a deleted and unbound agent; every search; every export; every
  avatar of a name starting with an astral character (emoji, CJK Extension B).
- **Sibling suspects**:
  - Agent sidebar, streaming bubble and agent status fall back to `agentNames[id] ??
    slice(0, 8)` but only for bound agents, which the room agent list names — cleared.
  - Other `charAt(0)` initials: search the frontend for `charAt(0)` and `[0]` on names when
    implementing; any other avatar-like initial goes through the same fix.
  - The research export is pseudonymised by design (`test_research_export_builder.py:118`) —
    cleared, must not gain names.
  - Canvas cursors: `2026-10-07-canvas-awareness-names`.

## 7. Fix Design

1. **Agent labels for the room.** A conversation-side read (for example
   `GET /api/chatrooms/{id}/agent-labels`) behind `resolve_room_access` and `ensure_can_read`
   returns `{agent_id, name}` for the distinct agent senders of the room's live messages (a
   repository query beside `distinct_user_sender_ids`,
   `backend/contexts/conversation/infrastructure/repositories/message_repo.py:168-196`,
   capped the same way) plus the `observer_agent_id` values of released-observation
   messages, resolved through `AgentsFacade.agent_names`. `ChatroomView.vue` layers it under
   the bound list for every viewer, and the bubble's and author label's last fallback becomes
   an "Unknown agent" i18n label instead of the id.
2. **Search names.** `ChatroomSearchPanel` receives the view's user and agent name maps and
   labels hits through them, falling back like the message author.
3. **Export names.** `ChatExportService` resolves sender names once per export: agents
   through `AgentsFacade.agent_names`, guests through `load_room_guests`
   (`backend/contexts/conversation/application/room_guests.py:50-60`), accounts through
   `IdentityFacade.get_display_names` (never `get_chat_labels`, which falls back to email).
   Each serialized message gains `sender_name` (`None` when unresolved), and `_sender_label`
   uses it, keeping the eight-character id as the fallback.
4. **Avatar initials.** `SAvatar` takes the first grapheme with `Intl.Segmenter`
   (`[...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(name)][0]`),
   falling back to `Array.from(name)[0]` where the API is missing.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- Backend: the agent-label route returns the name of an unbound past author and of a
  soft-deleted unbound author, and the observer named in a disclosed released observation;
  it fails today because the route does not exist. A second case: an observer bound to the
  room but never disclosed in a release is absent, for a non-creator.
- `frontend/src/slices/conversation/__tests__/ChatroomMessageBubble.test.ts` or a room-view
  test: as a guest, a released observation's header shows the observer's name, and a message
  from an agent absent from every source shows "Unknown agent"; it fails today with the
  eight-character id.
- `frontend/src/slices/conversation/__tests__/ChatroomSearchPanel.test.ts`: a hit from a
  sender the passed map names shows that name; it fails today with the id.
- `backend/tests/unit/` export test (beside the existing chat export tests): the manifest
  carries `sender_name` for a member, a guest and an agent, and the Markdown transcript
  labels with them; it fails today because no name is resolved. A member without a display
  name keeps the id label and never an email.
- `frontend/src/shared/ui/__tests__/SAvatar.test.ts`: the initial of "🦊 Fox" is "🦊"; it
  fails today with a lone surrogate.

## 9. Security Considerations

- Authorization: the agent-label read is gated exactly like the messages it labels
  (`resolve_room_access` + `ensure_can_read`) and returns names only for ids already present
  in the room's messages or disclosed release metadata, so it neither enumerates the
  project's agents nor reveals an observer binding ([R28.10]).
- Privacy: export names come from `get_display_names` and room guest labels only; the login
  email never enters the export ([R13.33]).

## 10. Risks and Rollback

- One more room read on open; the distinct-agent query scans the room's agent messages,
  bounded by the same cap as the user query.
- Consumers parsing the JSON manifest see one new optional field; existing fields are
  unchanged.
- Rollback: revert; no schema or data change.

## 11. Acceptance Criteria

- [ ] AC-1: the regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: guests and members see the same agent names on past messages and in released
  observation headers, including for unbound and soft-deleted agents.
- [ ] AC-3: an agent with no resolvable name is labelled "Unknown agent", never by its id.
- [ ] AC-4: search hits show the sender's name wherever the room view names that sender.
- [ ] AC-5: Markdown, PDF and JSON exports name each sender, never by email, keeping the
  short id only when no name exists.
- [ ] AC-6: avatar initials never render a broken glyph for a name starting with an emoji
  or another astral character.
- [ ] AC-7: backend and frontend lint, typecheck, tests, OpenAPI drift and build pass in CI.

## 12. SRS Delta

None.

## 13. Deviation Log

Appended by /build.

## 14. Follow-ups

- **FU-1.** Search hits from authors past the roster cap still fall back to the id; the
  search API could return names itself if that cap ever bites.
