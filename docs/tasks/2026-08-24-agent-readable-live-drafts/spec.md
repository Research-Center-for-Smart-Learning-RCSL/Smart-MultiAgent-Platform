---
type: feature
status: implemented
created: 2026-08-24
requirements: [R13.19, R28.02, R28.09, R30.15, R30.17, R30.19, R30.30, R30.37]
depends_on: [2026-08-24-traceability-extraction-gate, 2026-08-24-observer-presentation-blocks, 2026-08-24-example-agents-quote-unit-two]
---

# A granted agent can read a room's unsent drafts on demand

## 1. Summary

A room member's in-progress text — what is typed into the chat composer, and what is
filled into an activity worksheet — exists only in that browser tab today. This feature
lets a participant's client push periodic snapshots of it to the server, holds them in
Redis under a short TTL, and gives an agent whose binding the room creator has explicitly
granted a `read_drafts` tool that returns the current snapshots on demand. Nothing is
pushed to the agent and nothing wakes it; the agent reads when it decides to.

The grant is per binding, off by default, and independent of the observer role — a normal
facilitator agent can hold it. Participants see a disclosure indicator whenever any agent
in the room holds it.

**This feature makes text a person has not chosen to send readable by a machine that
speaks in the room.** §8 and §10 are the load-bearing sections; the design's job is to make
that legible to the person typing and bounded for everyone else.

## 2. Goals and Non-goals

**Goals**

- A participant's client reports composer and activity-worksheet drafts to the server on a
  throttled interval, and clears them on send/submit.
- Drafts live in Redis with a TTL and are never written to Postgres.
- A `read_drafts` tool, offered only to bindings the room creator granted, returns the
  current drafts on demand, keyed by truncated participant code.
- The grant is per (room, binding) and dies with the binding, exactly like the activity
  control grant ([R30.37]).
- A draft is never readable on looser terms than the corresponding submitted payload.
- The room shows a disclosure indicator while any binding holds the grant, defaulting on.
- Every read is audited by count, never by content.
- The shipped `creative-thinking-room` prompts and the example guide state the draft rule
  before this feature is usable. By the time this task runs, its predecessor has made unit 2
  submissions quotable and left unit 4 unquotable; **a draft is unquotable in both units**,
  because what governs a draft is not how sensitive the topic is but the fact that its author
  has not chosen to send it. This is in scope, not deferred (§8, AC-16).

**Non-goals**

- **No draft persistence.** Nothing reaches Postgres, an export, a backup, the transcript,
  a notification, or an audit payload.
- **No push and no wake.** A draft update never triggers a turn, never re-arms the silence
  clock, and never appears in an agent's context automatically. The only path is the tool.
- **No draft surface for humans.** No teacher-facing live view of what students are typing.
  This is deliberate: a persistent human-readable panel is a different product decision with
  a different consent conversation, and the agent path at least leaves an audit trail.
- **Message editing is out of scope.** In-progress edits of an already-sent message
  (`useChatroomMessageEditing.ts`) are not reported.
- **No cross-room reads.** The tool is built from the turn's own room.
- **The shipped example packs do not grant it.** Like `may_control_activities`, the pack
  field is advisory; the grant is the teacher's separate act (§6, and Q-7). The pack prompts
  *are* edited by this task — stating the rule is in scope, conferring the authority is not.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which surfaces count as "editing in progress"? | The chat composer draft and the unsent activity worksheet. Message editing is excluded. | The two chosen are the ones with teaching value ("who is stuck on the mandala"). Message editing is the smallest surface and the smallest payoff. |
| Q-2 | How does an agent get the drafts? | A dedicated `read_drafts` tool the model calls when it wants them. | Matches the request ("read when it wants to read"). A context block would put unsent text into every provider call of every turn, including turns that have nothing to do with it, and would make the read invisible. A tool call is one auditable, rate-limitable event. |
| Q-3 | Who may read? | The room creator grants it per bound agent, in that room. Any role is eligible. | Mirrors [R30.37]: default-deny, explicit act, dies with the binding, confers nothing in another room. A blanket room switch cannot express "the teacher agent may, the peer agent may not", which is the first thing a real classroom wants. |
| Q-4 | Do participants know? | `chatrooms.disclose_drafts`, default `true`, creator-only patch; renders a chip on the composer and the activity panel while any binding holds the grant. | Mirrors [R28.09] for consistency, at the user's direction. Recorded honestly: unlike observer disclosure, turning this off leaves a state where a person's unsent words are read and they are not told. §10 carries that as an accepted risk, not a solved one. |
| Q-5 | Where do drafts live? | Redis, TTL-bounded, keyed per (room, user, surface). | A draft is by definition unshared. Postgres would put it in backups, exports and retention scans, and every one of those is a place it must not be. Presence already takes this posture for the same reason (`presence.py:15-21`). |
| Q-6 | How does the client report them? | Client frames on the existing room WebSocket, throttled, mirroring `typing.start`. | The socket is already open, authenticated and room-scoped, and `on_client_message` already writes presence state to Redis from exactly this path (`app/api/ws/chatroom.py:107-129`). A REST endpoint would duplicate the AuthZ and add a round trip per keystroke burst. |
| Q-7 | Should the `creative-thinking-room` pack ship agents holding this grant? | No. The pack carries an advisory field at most; the guide documents the unit-4 caution. | Unit 4 collects negative-affect narratives from 13-year-olds and the shipped prompts already forbid pressing for detail (`creative-thinking-room.json:27`, `:52`). A pack that silently grants draft reads in that unit would undo that by installation. |
| Q-8 | Does this depend on `2026-08-24-observer-presentation-blocks`? | Yes. Overlap prerequisite. | Both add a runtime tool through the same three seams: `BUILTIN_TOOL_NAMES` (`tool_registry.py:119-135`), `build_agent_tools`' signature (`builtin_tools.py:863-873`), and `_builtin_tools` (`turn_engine.py:1457-1528`). Concurrent builds conflict; either could go first. Built second, this one reuses the tool-assembly shape that dossier lands. |
| Q-9 | Does this depend on `2026-08-24-traceability-extraction-gate`? | Yes, for the same reason its sibling does. | This task's SRS Delta opens a new chapter §32 whose requirements each need a `docs/traceability.csv` row, and that dossier regenerates the whole file from a script it builds. |
| Q-10 | Does this depend on `2026-08-24-example-agents-quote-unit-two`? | Yes, **logically**. | AC-16 writes the draft rule into prompts whose submission rule that task is about to split by activity type. Written first, the draft rule would sit beside a flat prohibition that no longer exists, and would have to be rewritten anyway. Written second, it says the sharper thing: unit 2 submissions became quotable, unit 4 submissions did not, and **drafts stay unquotable in both** — because the distinction is not topic sensitivity, it is whether the author chose to send it. |

## 4. Current State

### 4.1 "Someone is editing" already exists, and deliberately carries no content

The room has a full typing indicator:

- The client sends `typing.start` once per burst on a 3-second debounce timer and
  `typing.stop` when it lapses (`ChatroomView.vue:815-836`, `emitTyping` at `:826`), plus a
  final `typing.stop` on socket teardown — which lives in **`useChatroomSocket.ts:615`**, not
  in the view. `ChatroomView.vue`'s `onBeforeUnmount` (`:866-873`) only clears the timer;
  retraction is otherwise server-side per connection (`app/api/ws/chatroom.py:86-105`).
- The server refcounts it per connection in Redis under `ws:typing:{room}:{user}`
  (`presence.py:68-71`, `:175-241`) with `_TYPING_TTL_SECONDS = _CONN_TTL_SECONDS = 150`
  (`:33-53`), and throttles inbound starts at 2s (`app/api/ws/chatroom.py:76-77`).
- **The published event carries a user id and nothing else** (`app/api/ws/chatroom.py:105`,
  `:124`), and the client renders even that as truncated codes — `typingNames`
  (`ChatroomView.vue:875-881`) maps each id through `uid.slice(0, 8)`. So "codes, never names"
  (§5.4, §8) is not a property this feature introduces; it is the posture the room already
  takes, and the reason it is stated there is that a draft-reading tool is the surface most
  likely to be built the other way.

So the room already knows *that* someone is composing. This feature is exactly the step from
that to *what*, and every safeguard below exists because that step is not small.

### 4.2 Activity drafts do not exist anywhere

- `useActivityHost` exposes one operation, `submit`, which posts a complete payload
  (`useActivityHost.ts:43-69`). There is no autosave, no local persistence, no server call
  before submit.
- `activity_sessions` (`contexts/activities/infrastructure/tables.py:53-78`) has no partial
  payload column; the payload appears for the first time on an `activity_submissions` row.
- Therefore a student's half-filled mandala is currently unrecoverable even to themselves
  after a tab reload — worth noting, because this feature incidentally makes it recoverable
  and the guide should not promise that (FU-1).

### 4.3 The plugin SDK contract is closed at four members

`ActivityRenderCtx` is `{schema, session, emit, t}` (`sdk/types.ts:46-53`), the bridge builds
exactly those keys with a comment stating "no other enumerable keys (AC-3)"
(`sdk/bridge.ts:40-46`), and the postMessage kinds are fixed so the deferred isolating iframe
([R30.19]) is a bridge swap rather than a rearchitecture (`types.ts:71-81`). Reporting a
draft from a plugin is therefore a **deliberate contract extension**, not an addition nobody
notices — and it matters here, because `mandala-9grid` is a plugin
(`slices/activities/plugins/mandala9grid/`), so a design that covers only the schema form
would miss the single most valuable worksheet in the example course.

### 4.4 The grant precedent

`chatroom_agents` carries `may_control_activities`, `activity_type_allowlist` and
`granted_by_user_id` (`tables.py:82-102`, migration 0078). `ConversationFacade` exposes
`activity_control_grant` for the runtime to ask once per turn (`facade.py:324-344`) and
`set_agent_activity_grant` for the settings write (`:346-363`). `resolve_activity_control`
fails closed on every error path (`activity_tools.py:69-110`), and the file's docstring
(`:7-19`) states why a tool sourced from a room grant is safe to exist.

### 4.5 The consent gates a draft must not undercut

A *submitted* payload reaches an agent only through two gates:

- `ActivityType.expose_payload_to_agent`, per type. All four example types set it `true` and
  set `echo_includes_content: false`, so agents read a digest the room never sees.
- A platform-wide lock, re-read on every turn so consent withdrawn takes effect immediately
  (`activity_context_provider.py:124-143`), failing closed when unreadable.

A draft is the same content at an earlier moment, minus the participant's decision to share
it. Any design where the draft path is looser than this is wrong by construction.

### 4.6 Slice boundary

Gate #1's `SLICE_DEPS` makes `conversation` the host that imports `activities` one-way
(`frontend/CLAUDE.md`, "Slice isolation"). The activities slice must not reach the chatroom
socket. `ChatroomView.vue:826-836` is the existing example of the host owning a socket send
on behalf of a child component.

## 5. Design

### Options considered

**Option A — extend `typing.start` to carry the text.** Smallest diff. Rejected: `typing.*`
is published to the whole room (`app/api/ws/chatroom.py:124`), so the payload would reach
every member, which is a far larger disclosure than the feature asks for.

**Option B — REST autosave to Postgres, read by a context provider.** Durable, familiar
shape. Rejected twice: durability is the wrong property for unshared text (Q-5), and a
context provider makes the read automatic and invisible, which Q-2 rejected.

**Option C — WS-reported snapshots in Redis, read by a grant-gated tool.** Chosen.

### Decision

Three separable pieces, each mapped onto an existing pattern so there is no new mechanism to
review from scratch:

1. **Reporting** rides the room WebSocket, like `typing.start`.
2. **Storage** is Redis with a TTL, like presence.
3. **Access** is a per-binding room grant plus a structured tool, like activity control.

What was consciously given up: an agent cannot learn about a draft it did not ask for, and
cannot be woken by one. A "notice when a student stalls" behaviour has to come from the
agent's own wake-up triggers plus a read, not from the platform pushing. That is the cost of
keeping the read an explicit, auditable act.

### 5.1 Reporting

Two new client frames on the room channel:

- `draft.update` — `{surface, key?, content}`. `surface` is `"composer"` or `"activity"`;
  `key` is the activity type key for the activity surface, absent for the composer.
- `draft.clear` — `{surface, key?}`.

Throttle and lifecycle:

- **Composer.** `emitTyping` (`ChatroomView.vue:826-836`) already runs a 3s debounce timer for
  `typing.start`/`typing.stop`. The draft update rides the same timer and is bounded by the
  same 2s server-side throttle the typing path uses (`app/api/ws/chatroom.py:76-77`).
  `draft.clear` goes on a successful send, and on teardown at
  **`useChatroomSocket.ts:615`** — the view's `onBeforeUnmount` (`:866-873`) only clears the
  timer and is not a send site, so anchoring the clear there would leave AC-4's unmount half
  unimplemented.
- **Activity worksheet.** `ActivityHost` emits a Vue `draft` event upward; `ChatroomView`
  forwards it to `wsChannel.send()`. **The activities slice never touches the socket**
  (§4.6). `draft.clear` goes on a successful submit and on unmount.
- The server drops a frame whose room has no binding holding the grant, before touching
  Redis. A room nobody may read stores nothing.

  **This costs a Postgres read on the socket path, and the design pays for it deliberately.**
  `on_client_message` closes over `presence` and `publisher` only
  (`app/api/ws/chatroom.py:107-129`); the route's session is opened and closed around the ACL
  check at `:60-71`, so a grant read means a fresh session per frame, the way
  `_notify_presence` already takes one at `:43`. Rather than pay that per frame, the
  connection resolves "does any binding in this room hold the grant" **once at connect**, into
  a connection-scoped flag beside `_typing_active` (`:84`), and re-resolves on the
  `chatroom.agents_changed` event the settings write already publishes. A grant revoked
  mid-session therefore stops new writes at the next event, and the TTL bounds what was
  already stored. A grant *added* mid-session starts collecting at the same point. Both are
  stated here rather than discovered as a lag.

### 5.2 The SDK contract extension

`ActivityRenderCtx` gains a fifth member, `draft(payload: unknown): void` — fire-and-forget,
no promise, no result. `PluginToHostMessage` gains `{ kind: 'draft'; payload: unknown }` in
the same change, so the deferred `IframeBridge` ([R30.19]) still has a complete contract and
remains a bridge swap. `sdk.test.ts`'s "exactly these members" assertion moves from four to
five deliberately; it is not relaxed to "at least".

The built-in `SchemaForm` path reports through the host directly and does not go through the
bridge.

### 5.3 Storage

Redis, one key per (room, user, surface):

```
ws:draft:{room_id}:{user_id}:{surface}[:{key}]  ->  JSON {content, updated_at}
```

- **TTL 900s**, refreshed on every update. Deliberately *not* tied to `_CONN_TTL_SECONDS`:
  a worksheet legitimately sits untouched for ten minutes while a student thinks, and the
  typing TTL would retract it (`presence.py:33-53` explains why that constant is what it is
  for connections, and that reasoning does not transfer).
- Deleted on `draft.clear`, on a successful send/submit, and by the TTL. Not deleted on
  disconnect: a tab reload would otherwise destroy a real draft the student still has on
  screen.
- **Every returned draft carries its age**, so an agent can tell a live draft from one whose
  author left twelve minutes ago. The tool result states the TTL horizon.
- Content is capped at 4 KB per surface and 16 KB per user per room; a longer draft is
  truncated with a marker, never rejected silently.
- The key is not under `ws:presence:` — `scrub_stale_presence` scans that prefix and
  discriminates by counting `:` (`presence.py:300-306`, and `_typing_key`'s comment at
  `:68-71` records this exact trap).

### 5.4 Reading

`read_drafts` — offered on a turn only when the binding holds the grant. Arguments: an
optional `surface` filter and an optional activity `type_key` from a turn-built enum.
Returns, per participant with a live draft:

```
u:1a2b3c4d  composer        (updated 40s ago)  <content>
u:9f8e7d6c  activity mandala-9grid  (updated 6m ago)  <field: value, ...>
```

- **Codes, never names.** Reuses `_subject_code` (`activity_context_provider.py:146-147`).
  There is no legend on this path.
- **The consent gate is applied at read, not at write** (§4.5): an activity draft is omitted
  when its type's `expose_payload_to_agent` is false, and every activity draft is omitted
  when the platform lock withholds payloads. The check re-reads the policy per call, failing
  closed, exactly as the context provider does (`:124-143`).
- Output passes `clip_tool_output` (`tool_registry.py:80-93`) like every other tool.
- At most 3 calls per turn; a fourth returns an error result rather than data. The per-turn
  tool-round cap is a ceiling on rounds, not on one tool's reads.
- The tool description states, in the model's own context, that this is unsent text and that
  quoting it into the room exposes something its author has not chosen to share.

### 5.5 Grant and disclosure

- `chatroom_agents.may_read_drafts BOOLEAN NOT NULL DEFAULT false` plus reuse of the
  existing `granted_by_user_id`. Written from the room's Settings page on the bound agent's
  row, beside the activity-control toggle; creator-gated on the same terms.
- `chatrooms.disclose_drafts BOOLEAN NOT NULL DEFAULT true`, creator-only patch, permitted
  without `RESOURCE_CREATE_EDIT` — the same carve-out [R28.09] makes for observer
  disclosure.
- The chatroom DTO gains `drafts_readable` (any binding holds the grant) and
  `disclose_drafts`. **Guests receive neutral values**, matching how [R28.02] treats
  `observers_present`.
- The client renders a chip on the composer and in the activity panel when
  `drafts_readable && disclose_drafts`, with a tooltip naming what is read and that it is not
  stored. Reuses `ObserverDisclosureChip.vue`'s shape.

### 5.6 Audit

`agent.read_drafts` on every call: room, agent, granting user, the number of drafts returned
and the surfaces involved. **No content, no participant ids** — codes are derived from user
ids, so even the codes are omitted; the count is what an operator needs to answer "how often
was this used". Mirrors [R28.11]'s rule that content never enters audit metadata.

## 6. Detailed Changes

**Backend — `contexts/conversation`**

- New `infrastructure/drafts.py`: `DraftStore` with `put`, `clear`, `list_for_room`, over
  Redis, alongside `presence.py` and following its Lua/pipeline idiom.
- `tables.py`: `chatroom_agents.may_read_drafts`; `chatrooms.disclose_drafts`.
- `domain/models.py`: the two flags on the corresponding models.
- `interfaces/facade.py`: `draft_read_grant(chatroom_id, agent_id)` and
  `set_agent_draft_grant(...)`, mirroring `activity_control_grant` / `set_agent_activity_grant`
  (`:324-363`).
- `application/chatroom_service.py`: the grant write plus its audit event; the
  `disclose_drafts` patch on the creator-authority path that `disclose_observers` already
  uses.

**Backend — `app/api/ws/chatroom.py`**

- `on_client_message` handles `draft.update` / `draft.clear`, reusing the existing throttle
  variable pattern (`:76-77`). **Nothing is published** — a draft frame produces no room
  event, so the write is server-side only.

**Backend — `contexts/agents/application/runtime`**

- New `draft_tools.py`: `resolve_draft_access` (fails closed, logs, returns `None`) and
  `build_read_drafts_tool`, structurally mirroring `activity_tools.py`.
- `tool_registry.py`: `read_drafts` added to `BUILTIN_TOOL_NAMES` (`:119-135`).
- `builtin_tools.py`: `build_agent_tools` gains `draft_access: DraftAccessContext | None`.
- `turn_engine.py`: `_builtin_tools` resolves it, beside the activity-control resolution
  (`:1501-1505`).

**Backend — `contexts/activities`**

- `interfaces/facade.py`: a read for `expose_payload_to_agent` by type key, plus the existing
  policy read, so the draft tool applies §4.5's gate without reaching into the context.

**API contract**

- `ChatroomOut` gains `drafts_readable`, `disclose_drafts`; `ChatroomPatchIn` gains
  `disclose_drafts`; `AgentRolePatchIn` (or the grant endpoint the activity toggle uses)
  gains `may_read_drafts`. `pnpm run gen:api` rerun required: **yes**.
- No new REST endpoint for drafts themselves. This is deliberate: there is no
  human-readable draft surface (§2).

**Frontend — `slices/activities`**

- `sdk/types.ts`, `sdk/bridge.ts`: the fifth ctx member and the new message kind (§5.2).
- `ActivityHost.vue`: a throttled `draft` emit from the schema-form path and from the bridge
  callback; `draft-clear` on submit and unmount.
- `ActivityPanel.vue`: the disclosure chip, driven by props from the host — **the slice reads
  no chatroom state of its own**.

**Frontend — `slices/conversation`**

- `ChatroomView.vue`: forwards the activity `draft` events to `wsChannel.send()`, and extends
  `emitTyping` (`:815-834`) to carry the composer snapshot.
- `ChatroomComposer.vue`: no new logic; the view already owns the timer.
- `ChatroomSettingsView.vue`: the `may_read_drafts` toggle on the bound-agent row, and the
  `disclose_drafts` switch beside `disclose_observers`.
- A disclosure chip on the composer, reusing `ObserverDisclosureChip.vue`'s shape.
- i18n: new keys in both locales, including the tooltip text that tells a participant exactly
  what is read and that it is not stored.

**Example pack and guide** (in scope — §2, AC-16)

- `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json`: TA, SA
  and AA each gain the draft rule alongside the per-type submission rule its predecessor
  leaves in place (`:27`, `:52`, `:77`). The wording follows the existing one: the agent may
  say what it can see, must not repeat it, and must give the reason. The rule is flat across
  both units, and the prompt says why the submission rule is not — the author has not chosen
  to send a draft at all, so the unit 2 relaxation does not reach it. AA's version also states
  that a draft is not a submission and must not be counted as one.
- `docs/examples/creative-thinking-course.md`: a section on the grant, the disclosure chip,
  and the unit-4 caution — the unit collects negative-affect narratives from 13-year-olds
  (`creative-thinking.json:160-166`), and a half-typed one is the worst thing in this product
  to read over someone's shoulder. It states that the packs confer no grant and that a teacher
  enabling it should do so per agent, deliberately.

**Migration** — two boolean columns with server defaults. **Take the revision number from
`alembic heads` at build start, not from this line.** `0079_member_groups` is head today and
`2026-08-24-observer-presentation-blocks` claims 0080, but this dossier and
`2026-08-24-group-activity-submissions` share all three predecessors and nothing orders them
against each other, so whichever builds second would collide on a hard-coded number.

## 7. NFR Checklist

- **i18n** — every chip, tooltip, settings label and confirmation string through `$t()`. The
  tool's own description string is model-facing English and is not an i18n key.
- **Audit log** — `agent.read_drafts` per call; `chatroom.draft_grant_changed` and
  `chatroom.disclose_drafts_changed` on the settings writes. Counts and ids only.
- **Tenant isolation** — no new REST endpoint. The WS route already resolves room access
  before the handler runs; the tool is built from the turn's own `chatroom_id`; the Redis key
  is room-scoped and there is no argument that names another room.
- **Error handling UX** — a dropped or throttled draft frame is silent by design; the feature
  degrades to "the agent reads a slightly older draft". The settings toggles get the standard
  optimistic-update-plus-toast treatment. A grant that is revoked mid-turn simply yields no
  tool on the next turn.
- **Performance** — one Redis write per user per 2s burst window, bounded by the existing
  typing throttle, and **no Postgres read per frame**: the grant is resolved once per
  connection (§5.1), so a 30-typist room costs 30 grant reads for the lesson rather than ~15
  sessions per second on the socket path. One `SCAN`-free room read per tool call (the store
  keeps a per-room index set, in the shape of the roster key `_room_key` at
  `presence.py:56-57` — **not** `_user_rooms_key` at `:60-61`, which is the per-*user* reverse
  index and the wrong key shape here). Content caps in §5.3 bound memory. Redis
  runs `allkeys-lru`, so an evicted draft simply is not returned — the TTL means eviction is
  a bounded loss, which is exactly the property `2026-08-20-onboarding-without-smtp`'s FU-11
  found missing in the email-allowlist keys.

## 8. Security Considerations

This touches WebSocket input, tenant boundaries, agent tool surfaces, and user-input
processing, and it is the most privacy-sensitive surface in the product.

- **What this feature actually is.** It makes text a person has not chosen to send readable
  by an LLM agent that can speak in the room. In the example course that text includes
  13-year-olds' accounts of distressing events (`creative-thinking.json:160-166`, the
  `six-hats-emotion-desk` `event` field). Every control below exists because of that
  sentence, and none of them is decorative.
- **Default deny.** No binding may read without an explicit grant by the room creator, per
  room, and the grant dies with the binding.
- **Disclosure defaults on.** Q-4 records that the creator may turn it off, and §10 records
  that as an accepted, unmitigated risk rather than a solved one.
- **The draft is never looser than the submission.** §5.4's read-time gate is the single most
  important rule in this dossier: an activity type whose payload agents may not see has no
  readable drafts either, and the platform consent lock withholds every activity draft
  immediately, failing closed.
- **No persistence.** Redis only, TTL-bounded, no Postgres, no export, no transcript, no
  audit payload, no notification. Deleting the room's Redis keys deletes the feature's
  entire data footprint.
- **No names.** Codes only, and no legend on this path, so an agent that reads a draft cannot
  attribute it to a named student from this tool alone.
- **Prompt injection.** Draft content is participant-authored and reaches the model as tool
  output. It passes `clip_tool_output` like every other tool, and the composer draft is
  exactly the same text the participant is about to send anyway, so the injection surface is
  not new. The *activity* draft is new content in an agent's context, which is the reason for
  the read-time consent gate rather than a write-time one.
- **Amplification of the quoting risk.** The example prompts already forbid quoting a
  submission because the message box is class-visible
  (`creative-thinking-room.json:27`, `:52`, `:77`). A draft is strictly worse to quote: its
  author has not chosen to send it at all. The pack prompts and the example guide are
  therefore edited **in this task** (§6, AC-16), not deferred — a prompt is not an
  enforcement boundary, but shipping the grant while the shipped prompts are silent about
  drafts would leave the one instruction the model actually reads pointing the wrong way.
  This matters more after `2026-08-24-example-agents-quote-unit-two`: a model that has just
  been told unit 2 answers are quotable will generalise to drafts unless the prompt says
  otherwise.
- **Fail closed everywhere.** Grant resolution, policy read, type read: any exception yields
  no tool or no data, never a permissive default.
- **Guests.** A guest's draft is reported and readable on the same terms as a member's, and
  the guest sees the same disclosure chip. Guests receive neutral values for
  `drafts_readable` when disclosure is off, matching [R28.02].

## 9. Quality Notes

**Existing debt in touched files:**

- `ChatroomView.vue:815-836` keeps the typing timer in the view body as module-level `let`
  state. The draft snapshot must not deepen that: extract a `useDraftReporting` composable
  and have `emitTyping` call into it, rather than adding a second timer beside the first.
- `presence.py` mixes key layout, Lua and policy in one module. `drafts.py` follows its
  idiom, not its scope — no policy decisions in the store.
- `sdk/bridge.ts:40` asserts "exactly the four contract members" in a comment while the test
  enforces it. Update both together, or the comment becomes the lie the test is protecting
  against.

**Patterns to follow:**

- `activity_tools.py` — tool shape, fail-closed resolution, and the docstring that states why
  a grant-sourced tool is safe.
- `presence.py:68-71` — why a new Redis key must not sit under `ws:presence:`.
- `app/api/ws/chatroom.py:107-129` — the client-frame handler shape, including the throttle
  and the connection-scoped flag.
- `ObserverDisclosureChip.vue` — the neutral, content-free disclosure chip.

**Reuse inventory:**

- `_subject_code` (`activity_context_provider.py:146-147`) — the same helper the sibling
  dossier exports; do not restate the truncation.
- `clip_tool_output`, `Tool`, `ToolResult`, `schema_violations` (`tool_registry.py`).
- `ConversationFacade.activity_control_grant` / `set_agent_activity_grant`
  (`facade.py:324-363`) — copy the shape, including the "caller owns commit" contract.
- `ObserverDisclosureChip.vue`, `SToggle`, `STooltip`, `SAlert` — no new atoms.
- The `_CONN_JOIN_LUA` / `_CONN_LEAVE_LUA` pattern (`presence.py:75-85`) if the store needs a
  per-room index set.

## 10. Risks and Rollback

- **The central risk is the feature itself.** Reading unsent text is surveillance-shaped
  however carefully it is built. The mitigations are default-deny, disclosure-on-by-default,
  TTL-bounded non-persistence, codes-not-names, per-call audit, and the read-time consent
  gate. What is **not** mitigated: a creator who grants the tool and turns disclosure off
  produces a room where a person's unsent words are read and nobody tells them. Q-4 records
  that this is the user's explicit choice for consistency with [R28.09]. If that trade is
  ever reconsidered, making `disclose_drafts` non-suppressible is a one-line change plus the
  DTO.
- **Unit 4 of the shipped example is the worst case for this feature.** Extending the pack
  prompts and the guide is therefore part of this task (§6, AC-16). A build that ships the
  grant with AC-16 unticked is not a partial delivery of this dossier; it is the one
  combination the dossier exists to prevent.
- **The migration** adds two booleans with server defaults; forward compatible and reversible
  by `DROP COLUMN`, which revokes every grant — a safe direction to fail.
- **Rollback of the frontend alone is safe.** No client reports drafts, so the store stays
  empty and the tool returns nothing.
- **Redis eviction under `allkeys-lru`** can drop a draft before its TTL. The result is an
  agent reading nothing, never stale-but-wrong data, because every entry carries its age.
- **Throttle drift.** If the composer snapshot is sent per keystroke rather than on the
  existing burst timer, a busy classroom multiplies WS frames by the number of typists. AC-3
  pins the throttle.

## 11. Acceptance Criteria

- [x] AC-1: With no binding holding the grant, a `draft.update` frame writes nothing to Redis
      and no agent in the room is offered `read_drafts`.
      *(`test_ws_chatroom_drafts.py::TestARoomNobodyMayReadStoresNothing`,
      `test_draft_tools.py::TestWhoIsOfferedTheTool`, plus the `db`-tier
      `test_draft_grant_repository.py::test_a_room_with_no_granted_binding_has_no_reader`.)*
- [x] AC-2: A binding granted `may_read_drafts` by the room creator is offered `read_drafts`
      on its turns; revoking the grant, or unbinding the agent, removes it on the next turn.
      The grant confers nothing in any other room the same agent is bound to.
      *(`test_draft_tools.py::TestWhoIsOfferedTheTool`; the room-scoping and
      dies-with-the-binding halves are `db`-tier —
      `TestTheGrantIsScopedToOneRoom`.)*
- [x] AC-3: The composer reports on the existing burst timer, not per keystroke, and the
      server applies the same throttle window the typing path uses.
      *(`useDraftReporting.test.ts`, `test_ws_chatroom_drafts.py::TestTheThrottle` — which
      also pins that the draft frame does not consume the typing window.)*
- [x] AC-4: A successful send clears the composer draft; a successful submit clears that
      activity's draft; both also clear on unmount.
      *(`useDraftReporting.test.ts`, `ActivityHostDrafts.test.ts`,
      `ActivityPanelDrafts.test.ts`. A **failed** send deliberately does not clear.)*
- [x] AC-5: A draft key expires after its TTL with no further updates, and every entry the
      tool returns carries its age.
      *(`db`-tier `test_draft_store_ttl.py` — the expiry is read back off the server, and
      one key is expired for real; mutation-probed red without `EX`.)*
- [x] AC-6: An activity draft is **not** returned when its type has
      `expose_payload_to_agent: false`, and **no** activity draft is returned while the
      platform payload lock is in force. A policy read that fails withholds rather than
      permits.
      *(`test_draft_tools.py::TestTheDraftIsNeverLooserThanTheSubmission`, seven cases
      including a shared key and an unknown key. Mutation-probed: three go red if the
      filter is widened.)*
- [x] AC-7: `read_drafts` output contains no display name and no login email — only `u:`
      codes, surface labels, ages and content.
      *(`test_draft_tools.py::TestOutputNamesNobody`. See D-9: a participant could
      originally forge another's code into the attribution header; now structurally
      impossible.)*
- [x] AC-8: Nothing about a draft reaches Postgres, the transcript, an export, a
      notification, or an audit payload. The audit event carries counts and surfaces only.
      *(`test_draft_tools.py::TestTheAuditTrail` asserts the absence, including that the
      derived codes are omitted too; `test_chatroom_draft_grant_service.py` does the same
      for the grant write. No table gained a draft column — 0082 adds two booleans.)*
- [x] AC-9: A draft update neither wakes an agent, nor re-arms the silence clock, nor counts
      toward `every_n_messages`.
      *(`test_ws_chatroom_drafts.py::TestNothingLeavesTheServer` — four behavioural cases
      plus one that reads the handler's own source, since the behavioural ones can only see
      the branches that exist today. Mutation-probed: all four go red if a publish is added.)*
- [x] AC-10: A fourth `read_drafts` call in one turn returns an error result, not data.
      *(`test_draft_tools.py::TestThePerTurnCap`, including that a refused call reads
      nothing and writes no audit row, and that the cap is per turn rather than per tool
      object.)*
- [x] AC-11: With `drafts_readable && disclose_drafts`, the composer and the activity panel
      each show the chip; with disclosure off, neither does, and a guest's chatroom DTO
      carries neutral values.
      *(`test_chatroom_draft_api.py::TestTheDisclosurePredicate`,
      `ActivityPanelDrafts.test.ts`. **See D-8** for what "neutral" resolves to for a guest
      and why it differs from the observer path.)*
- [x] AC-12: `disclose_drafts` is patchable by the room creator without
      `RESOURCE_CREATE_EDIT`, and by nobody else.
      *(`test_chatroom_draft_api.py::TestTheCreatorOnlyCarveOut`, in both directions,
      including that a mixed patch still needs both gates.)*
- [x] AC-13: The activities slice contains no import of the conversation slice and no socket
      access; `pnpm lint` passes gate #1 and `check:boundaries-enforced` still enforces it.
      *(`pnpm lint` green locally. `check:boundaries-enforced` is a bash script this host
      cannot run — it is a CI job, `frontend-gate-boundaries`, and passed on PR #167.)*
- [x] AC-14: `ActivityRenderCtx` exposes exactly five members, asserted as an exact set; the
      `PluginToHostMessage` union carries the new `draft` kind.
      *(`sdk.test.ts` — the exact-set assertion moved from four to five deliberately and was
      not relaxed to "at least". The bridge comment moved with it, per §9.)*
- [x] AC-16: All three `creative-thinking-room` prompts state that a draft is unquotable
      **for every activity type** and say why that differs from the per-type submission rule
      (**see D-2** — the "both units" framing was stale), asserted over the shipped file by
      `backend/tests/unit/test_agent_example_packs.py` alongside the constraints it already
      asserts; AA additionally states a draft is not a submission and must not be counted.
      `docs/examples/creative-thinking-course.md` carries the grant, the disclosure chip,
      the unit-4 caution and a dry-run checklist item. *(Mutation-probed: all three prompt
      assertions go red if the flatness clause is removed.)*
- [x] AC-15: The full Definition of Done passes — `pytest -q`, `ruff`, `mypy`, `pnpm test`,
      `pnpm lint`, `pnpm typecheck`, `pnpm build`, `check:openapi-drift`,
      `check:boundaries-enforced`.
      *Closed by **CI run `32862687028`** on PR #167: 22 of 22 jobs green, one skipped
      (`compose-boot-prod`, which runs only on `main`). That run is what closes the gates
      this host cannot execute — `backend-db`, `backend-wiring`, `backend-integration`,
      `frontend-e2e`, `frontend-gate-openapi-drift`, `frontend-gate-boundaries`,
      `frontend-gate-bundle`, `frontend-gate-type-coverage`, `frontend-csp-font`.*
      **It took two runs.** The first (`32861505020`) failed `backend-lint` on
      `ruff format --check` over `test_draft_tools.py`: the last edit of the task was
      followed by `ruff check` and `mypy` but not `ruff format`. Nothing about the code,
      everything about the discipline — running two of the three mechanical gates is
      running none of them, because the one skipped is the one that fails.
      **§17 still stands for what CI does not cover**: the browser pass, and the fact that
      no real model has ever called `read_drafts`.

## 12. Test Plan

- **AC-1, AC-2, AC-10** — unit, new `backend/tests/unit/test_draft_tools.py`, mirroring
  `test_activity_control_tools.py`: no grant means no tool, a stale grant means no tool, and
  the per-turn call cap.
- **AC-5, AC-8 (Redis half)** — unit over `DraftStore` with a fake Redis, plus one
  `pytest.mark.db`-free integration test against the compose Redis for the TTL, since a fake
  clock proves nothing about `EXPIRE`.
- **AC-6** — unit with a type whose `expose_payload_to_agent` is false, a policy-locked
  fixture, and a policy read that raises. All three must withhold.
- **AC-7** — unit with fixtures whose display names and emails are distinctive strings,
  asserting none appears in the tool output.
- **AC-8 (the rest)** — a repository-level assertion that no Postgres table gains a draft
  column, plus a unit test asserting the audit metadata keys.
- **AC-9** — unit on the WS handler: a `draft.update` frame produces no publish, no wake-up
  evaluation, and no silence-clock touch. This is the regression most likely to be introduced
  later by someone "improving" the handler.
- **AC-3, AC-4, AC-11, AC-13, AC-14** — frontend unit/component specs, plus the existing
  `sdk.test.ts` updated for the five-member ctx.
- **AC-16** — unit, extending `backend/tests/unit/test_agent_example_packs.py`, which already
  asserts four constraints over the shipped pack files rather than leaving them to review;
  the draft rule becomes the fifth. The guide half is a doc-diff review.
- **Browser pass** — `frontend:verify` against the compose stack: two sessions, one typing in
  the composer and one filling a mandala, with the chip observed in both places and in both
  disclosure states; then confirm via the Redis keys that a send and a submit each cleared
  the right entry. Note the standing constraint that `fake_provider.py` cannot produce a real
  agent turn, so the tool half is covered by backend tests, not by the browser pass.

## 13. SRS Delta

To be applied to `REQUIREMENTS.md` on approval, as a new chapter after §31.

### 32. Live Draft Context

An agent may read a room's **unsent** text — the chat composer draft and the in-progress
activity worksheet — when the room creator has explicitly granted that binding the authority.
Drafts are reported by the participant's own client, held only in Redis under a TTL, and read
on demand through a tool; nothing about a draft is ever persisted, published, or pushed.

- **[R32.01]** A participant's client reports composer and activity-worksheet drafts as
  `draft.update` / `draft.clear` frames on the room WebSocket channel, throttled on the same
  window as `typing.start`. A draft frame publishes no room event, wakes no agent, does not
  re-arm the silence clock, and is not counted by `every_n_messages`.
- **[R32.02]** Drafts are stored only in Redis, keyed per (room, user, surface), under a TTL
  refreshed by each update and cleared on send, on submit, and on an explicit clear. No draft
  value is written to PostgreSQL, an export, the transcript, a notification, or audit
  metadata.
- **[R32.03]** `chatroom_agents.may_read_drafts` (default `false`) is a per-binding grant
  written by the room creator. A granted binding's turns are offered a `read_drafts` tool,
  capped per turn; an ungranted binding is never offered it. The grant is scoped to the room,
  dies with the binding, and confers nothing in any other room. Where no binding in a room
  holds the grant, the server stores no drafts for that room.
- **[R32.04]** `read_drafts` returns truncated participant codes, surface labels, entry ages
  and content — never a display name or a login email. An activity draft is withheld when its
  type's `expose_payload_to_agent` is false or while the platform payload policy withholds
  submission content ([R30.30]); the policy is re-read per call and an unreadable policy
  withholds. A draft is never readable on looser terms than the corresponding submitted
  payload.
- **[R32.05]** `chatrooms.disclose_drafts` (default `true`) controls whether non-creators see
  an indicator that drafts in this room are readable by an agent. Only the creator may change
  it, and may do so without holding `RESOURCE_CREATE_EDIT`. Which agent holds the grant, and
  any draft content, is never disclosed to non-creators regardless of the flag. Guests receive
  neutral values.
- **[R32.06]** Every `read_drafts` call is audited with the room, the agent, the granting
  user, the number of entries returned and the surfaces involved. Draft content and
  participant identifiers never appear in audit metadata or logs. Grant changes and
  disclosure changes are audited.

## 14. Open Questions

- **OQ-1.** A draft survives a disconnect for up to its TTL (§5.3), so an agent can read text
  from someone who has closed the tab. Reporting the age is the chosen answer; deleting on
  last-connection-leave was rejected because a tab reload would destroy a live draft. If the
  age turns out not to be enough in practice, a "stale" flag derived from room presence is the
  cheap next step.
- **OQ-2.** The composer draft and the activity draft ride one grant. A room that wants an
  agent to see worksheets but not chat drafts cannot express that. Splitting the grant is
  additive and deliberately deferred.

## 15. Deviation Log

- **D-1. `chatroom.agents_changed` does not exist.** §5.1 re-resolves the room's grant
  mid-session "on the `chatroom.agents_changed` event the settings write already
  publishes". No such event exists: `set_agent_activity_grant` writes an audit row and
  publishes nothing, and `chatrooms.py` constructs no `Publisher` at all. Rather than
  invent a broadcast for one reader, the connection-scoped flag carries the time it was
  resolved and goes stale after **60s** (`_grant_ttl_s`). A grant revoked mid-session
  stops new writes within that window and the draft TTL bounds what was already stored; a
  grant added mid-session starts collecting at the same point. Both directions self-heal,
  and the lag is a stated constant rather than a dependency on an event that never fires.
  Taken with the user before implementation.

- **D-2. AC-16's "both units" framing was stale, so the rule is written per type.** The
  dossier assumed a unit-2/unit-4 split in the shipped prompts. Its predecessor left a
  five-**key** rule instead (`time-traveler-next-steps` and `six-hats-shared-case`
  quotable, `mandala-9grid` content-invisible, the two emotion-desk types forbidden), so
  "unquotable in both units" no longer names anything. The prompts say the draft rule is
  flat across **all five types** and say *why* it does not follow the per-type rule beside
  it — a bare prohibition next to a per-type permission reads as an oversight to a model
  looking for the applicable clause. `test_agent_example_packs.py` asserts the flatness
  phrase and the "does not apply" clause in all three prompts.

- **D-3. The group-proposal form was taken into scope.** `2026-08-24-group-activity-
  submissions` landed a second worksheet surface after this dossier was written:
  `ActivityPanel.vue` renders `SchemaForm` directly for a group proposal, bypassing
  `ActivityHost`, so Q-1's two surfaces no longer enumerate the product. Reported under
  the activity **type key**, so §5.4's read-time consent gate applies to it unchanged.
  Taken with the user rather than deferred.

- **D-4. The reuse target moved.** §9 cites `_subject_code` at
  `activity_context_provider.py:146-147`; the observer dossier's quality gate moved it to
  `contexts/activities/domain/subject_code.py::subject_code`, and the same-named function
  left behind in the provider is now row-shaped. The tool uses the domain one.

- **D-5. No new `ActivitiesFacade` method.** §6 asks for "a read for
  `expose_payload_to_agent` by type key". `list_types(project_id)` already returns every
  reachable type with that field, so the gate uses it rather than adding a second read
  with the same answer.

- **D-6. `DraftReadGrant` carries no allowlist**, unlike `ActivityControlGrant`. What a
  granted agent may read is decided per call by the type's own consent flag and the
  platform policy; a stored list would be a third gate to keep in step with those two, and
  the state where they disagreed would be a draft readable on looser terms than its own
  submission — the one thing AC-6 forbids. The grant route's body is therefore one field.

- **D-7. `may_read_drafts` reuses `granted_by_user_id`.** Migration 0082 adds no second
  grantor column: both reads ask the same question ("is anyone answerable for this?") and
  two columns would admit a state where they disagree. The consequence is that a draft
  revoke clears the grantor **only when the activity grant is also off** — a `db`-tier
  test pins that, because clearing it unconditionally would silently make the room's
  activity-control grant inert.

- **D-8. Guests are not specially neutralised.** [R32.05] says "Guests receive neutral
  values"; §8 of this dossier says the guest "sees the same disclosure chip" and receives
  neutral values *when disclosure is off*. The two readings conflict and §8 is the more
  specific, so `drafts_readable` is `disclose_drafts && has_readers` for everyone — a
  guest's own unsent text is read on exactly a member's terms, so suppressing the chip
  would withhold the disclosure from the person it is about. A guest still learns nothing
  about *which* agent holds the grant. The observer fields beside it keep their guest
  suppression, and a test asserts the asymmetry is deliberate rather than forgotten.

- **D-9. Two gate findings changed the design after implementation**, both mine:
  - The security gate found that a participant could **forge another participant's
    attribution header** by embedding a look-alike line in their own draft (the code is
    not secret — the typing indicator renders `uid[:8]`). Fixed structurally: every
    content line carries a `| ` prefix and a header never does. `/code-review` then found
    the first fix incomplete — `split("\n")` honours one of seven line terminators, so
    CR, VT, FF, U+0085, U+2028 and U+2029 all escaped it. `splitlines()` closes it;
    nine parametrised cases pin every terminator.
  - The quality gate found both consumers importing
    `contexts.conversation.infrastructure.drafts` **directly** — a route below the facade,
    and one context's application layer reaching another's infrastructure. The draft
    surface is re-exported from `contexts/conversation/interfaces/` on `PresenceTracker`'s
    terms. `lint-imports` cannot see this (its contracts enforce domain purity only), so
    an AST test asserts it.

- **D-10. The per-user entry cap is new.** `MAX_USER_CHARS` bounds bytes and not key
  count, so a thousand one-character drafts under distinct keys sat inside the budget
  while making the read path's MGET a thousand keys wide. `MAX_USER_ENTRIES = 8` was added
  at the security gate; `/code-review` then found the first version counted *index
  members* rather than live entries, which would have silently and permanently refused a
  legitimate participant once eight stale members accumulated (closing a tab fires no
  unmount hook). It now counts live values and prunes the dead ones, which costs nothing
  extra — the byte budget already fetches them — and makes the situation self-healing.

## 16. Follow-ups

- **FU-1.** This feature incidentally makes a half-filled worksheet survive a tab reload on
  the server side for up to the TTL. Nothing restores it to the participant's screen, and the
  guide must not imply otherwise. A real draft-restore feature is separate work.
- **FU-2.** A `draft_activity` presentation block — who is mid-way through which worksheet —
  is the obvious meeting point between this dossier and
  `2026-08-24-observer-presentation-blocks`. Deliberately not in either: it would put unsent
  text into a stored observation, which §2's no-persistence rule forbids without a separate
  decision.
- **FU-3.** `ChatroomView.vue` will own two socket-send responsibilities on one timer after
  this. If a third arrives, the timer belongs in a composable rather than in the view body
  (§9). *(Partly discharged: the draft half went into `useDraftReporting` rather than into
  the view body, so the view now delegates rather than accumulating. The `typing.*` timer
  itself is still module-level `let` state in the view.)*

- **FU-4. The round-change cancel has no component-level test.** `cancelGroupDraft()` in
  `ActivityPanel`'s activation watcher is what stops one round's pending values going out
  under the next round's key, and therefore under the next type's consent gate. Two
  attempts at a component test for it passed with the cancel deliberately removed — vacuous
  once because a wire-shaped activation never reached the store, and once for a reason not
  established. A test that cannot fail is worse than none, so neither was kept. The
  cancel's own behaviour is pinned in `useDraftThrottle.test.ts`; what is unpinned is that
  the watcher calls it. Worth a proper test by someone who has the panel's store harness
  in their head.

- **FU-5. `ws_chatroom` now carries eight connection-scoped closures.** The draft handling
  added a third responsibility to a route body that was already long. The closures-over-
  connection-state idiom is the file's own and the comments explain why that state cannot
  be module-level, so extracting it needs a class holding `chatroom_id`, the store and two
  flags rather than a simple move. Recorded rather than done here.

- **FU-6. The 64 KiB WS frame cap's comment is now out of date.**
  `shared_kernel/realtime/connection.py:60-64` says inbound frames "are tiny control
  messages (ping / refresh / typing)". `draft.update` carries composer text, so that is no
  longer true. The cap is still enforced and still generous — `DraftStore.clip` bounds
  what is stored at 4 000 chars regardless — but the comment now explains the constant with
  a premise that does not hold.

- **FU-7. Draft reads have no per-agent rate limit across turns.** `MAX_CALLS_PER_TURN`
  bounds one turn; an agent woken on `every_n_messages: 1` in a busy room can call
  `read_drafts` three times per message indefinitely. Each call is audited, so the
  behaviour is visible rather than silent, and the room's own message rate bounds it — but
  nothing makes "this agent read the room's drafts four hundred times this lesson"
  harder than the fourth read in one turn.

## 17. What was and was not verified

AC-15 is ticked by CI run `32862687028`, so this section is no longer about a missing
gate — it is about the two things **no** gate covers, which is the more useful record.

**Ran, locally, green:** the backend unit tier (7 672), `ruff check`, `ruff format
--check`, `mypy`; the frontend suite (1 664), `pnpm lint` (all 12 gates), `pnpm
typecheck`, `pnpm build`. The **`db` tier for this diff** — 16 tests against a real
PostgreSQL and Redis inside the compose network: the TTL is asserted against a real
`EXPIRE` rather than a fake clock, and the shared-grantor guard against real rows.
Migration 0082 applied, downgraded and re-applied. `backend/openapi.json` regenerated
inside the container (no BOM) and `pnpm run gen:api` rerun.

**Nine assertions were mutation-probed** rather than assumed — each was made to fail
before being trusted: the Redis TTL (red without `EX`), the shared-grantor guard (red when
the clear goes unconditional), AC-9's no-publish tripwire (four red when a publish is
added), AC-6's consent gate (three red when the filter is widened), AC-16's flatness
clause (three red when the phrase is removed), the header-forgery prefix (three red
without it), the line-terminator coverage (red under `split("\n")` for six of nine
terminators), the entry-count cap (red without it), and the null-payload emit (red under a
`!== null` guard).

**Ran remotely, green:** PR #167, CI run `32862687028` — 22 of 22 jobs, one skipped
(`compose-boot-prod`, `main`-only). That includes everything this Windows host cannot
execute: `check:boundaries-enforced`, `check:openapi-drift` and `check:bundle-size` are
bash scripts, and the `integration` / `wiring` / `e2e` tiers need neo4j, Vault and MinIO.
`backend-db` 2m37s, `backend-wiring` 1m52s, `frontend-e2e` 9m52s.

The **first** run (`32861505020`) failed one job, and the reason is worth keeping: the
final edit of the task was followed by `ruff check` and `mypy` but not `ruff format`, so
`backend-lint` caught a wrapped line the formatter wanted collapsed. Running two of the
three mechanical gates is running none of them — the one skipped is the one that fails.

**Not run at all — the honest gap:**

- **The browser pass (§12's last item).** Two sessions, one typing in the composer and one
  filling a mandala, the chip observed in both places and in both disclosure states, then
  the Redis keys checked to confirm a send and a submit each cleared the right entry.
  Nothing in CI substitutes for this, and the dossier asked for it specifically. The full
  compose stack is what it needs, and this host could not carry it.
- **`read_drafts` has never been called by a real model.** `fake_provider.py` cannot
  produce a real agent turn (the standing constraint §12 records), so every claim about
  what the tool *returns* rests on unit tests over a fake store, and every claim about how
  a model *reads* the output — the `| ` prefix rule especially — rests on the description
  being written clearly, which no test can establish. The example guide's dry-run checklist
  now carries the corresponding item.
- **AC-13's `check:boundaries-enforced`** passed on CI, not here.
