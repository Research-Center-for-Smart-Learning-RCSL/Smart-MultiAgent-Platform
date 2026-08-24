---
type: feature
status: draft
created: 2026-08-24
requirements: [R13.19, R28.02, R28.09, R30.15, R30.17, R30.19, R30.30, R30.37]
depends_on: [2026-08-24-traceability-extraction-gate, 2026-08-24-observer-presentation-blocks]
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
  field is advisory; the grant is the teacher's separate act (§6, and Q-7).

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
| Q-8 | Does this depend on `2026-08-24-observer-presentation-blocks`? | Yes. Overlap prerequisite. | Both add a runtime tool through the same three seams: `BUILTIN_TOOL_NAMES` (`tool_registry.py:119-135`), `build_agent_tools`' signature (`builtin_tools.py:863-873`), and `_build_tools` (`turn_engine.py:1489-1528`). Concurrent builds conflict; either could go first. Built second, this one reuses the tool-assembly shape that dossier lands. |
| Q-9 | Does this depend on `2026-08-24-traceability-extraction-gate`? | Yes, for the same reason its sibling does. | This task's SRS Delta opens a new chapter §32 whose requirements each need a `docs/traceability.csv` row, and that dossier regenerates the whole file from a script it builds. |

## 4. Current State

### 4.1 "Someone is editing" already exists, and deliberately carries no content

The room has a full typing indicator:

- The client sends `typing.start` once per burst on a debounce timer and `typing.stop` when
  it lapses (`ChatroomView.vue:815-834`), plus a final `typing.stop` on teardown (`:615`).
- The server refcounts it per connection in Redis under `ws:typing:{room}:{user}`
  (`presence.py:68-71`, `:175-241`) with `_TYPING_TTL_SECONDS = _CONN_TTL_SECONDS = 150`
  (`:33-53`), and throttles inbound starts at 2s (`app/api/ws/chatroom.py:76-77`).
- **The published event carries a user id and nothing else** (`app/api/ws/chatroom.py:105`,
  `:124`), and the client renders it as a name list (`ChatroomView.vue:875-877`).

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
  and the guide should not promise that (FU-2).

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
socket. `ChatroomView.vue:815-834` is the existing example of the host owning a socket send
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

- **Composer.** `emitTyping` (`ChatroomView.vue:815-834`) already runs a debounce timer for
  `typing.start`/`typing.stop`. The draft update rides the same timer, at the same 2s server
  throttle the typing path uses (`app/api/ws/chatroom.py:76-77`), and `draft.clear` is sent
  where `typing.stop` is sent on teardown (`:615`) and on a successful send.
- **Activity worksheet.** `ActivityHost` emits a Vue `draft` event upward; `ChatroomView`
  forwards it to `wsChannel.send()`. **The activities slice never touches the socket**
  (§4.6). `draft.clear` goes on a successful submit and on unmount.
- The server drops a frame whose room has no binding holding the grant, before touching
  Redis. A room nobody may read stores nothing.

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
- `turn_engine.py`: `_build_tools` resolves it, beside the activity-control resolution
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

**Migration** — 0081: two boolean columns with server defaults.

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
  typing throttle; one `SCAN`-free room read per tool call (the store keeps a per-room index
  set, as presence does at `presence.py:60-61`). Content caps in §5.3 bound memory. Redis
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
  (`creative-thinking-room.json:27`, `:52`, `:77`). A draft is strictly worse to quote. The
  pack prompts must be extended in the same change (§6 has no pack edit — that is FU-1, and
  it must land before any teacher is told to use the grant).
- **Fail closed everywhere.** Grant resolution, policy read, type read: any exception yields
  no tool or no data, never a permissive default.
- **Guests.** A guest's draft is reported and readable on the same terms as a member's, and
  the guest sees the same disclosure chip. Guests receive neutral values for
  `drafts_readable` when disclosure is off, matching [R28.02].

## 9. Quality Notes

**Existing debt in touched files:**

- `ChatroomView.vue:815-834` keeps the typing timer in the view body as module-level `let`
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
- **Unit 4 of the shipped example is the worst case for this feature.** FU-1 (extending the
  pack prompts) is a hard precondition for recommending the grant to any teacher, not a
  nice-to-have.
- **Migration 0081** adds two booleans with server defaults; forward compatible and reversible
  by `DROP COLUMN`, which revokes every grant — a safe direction to fail.
- **Rollback of the frontend alone is safe.** No client reports drafts, so the store stays
  empty and the tool returns nothing.
- **Redis eviction under `allkeys-lru`** can drop a draft before its TTL. The result is an
  agent reading nothing, never stale-but-wrong data, because every entry carries its age.
- **Throttle drift.** If the composer snapshot is sent per keystroke rather than on the
  existing burst timer, a busy classroom multiplies WS frames by the number of typists. AC-3
  pins the throttle.

## 11. Acceptance Criteria

- [ ] AC-1: With no binding holding the grant, a `draft.update` frame writes nothing to Redis
      and no agent in the room is offered `read_drafts`.
- [ ] AC-2: A binding granted `may_read_drafts` by the room creator is offered `read_drafts`
      on its turns; revoking the grant, or unbinding the agent, removes it on the next turn.
      The grant confers nothing in any other room the same agent is bound to.
- [ ] AC-3: The composer reports on the existing burst timer, not per keystroke, and the
      server applies the same throttle window the typing path uses.
- [ ] AC-4: A successful send clears the composer draft; a successful submit clears that
      activity's draft; both also clear on unmount.
- [ ] AC-5: A draft key expires after its TTL with no further updates, and every entry the
      tool returns carries its age.
- [ ] AC-6: An activity draft is **not** returned when its type has
      `expose_payload_to_agent: false`, and **no** activity draft is returned while the
      platform payload lock is in force. A policy read that fails withholds rather than
      permits.
- [ ] AC-7: `read_drafts` output contains no display name and no login email — only `u:`
      codes, surface labels, ages and content.
- [ ] AC-8: Nothing about a draft reaches Postgres, the transcript, an export, a
      notification, or an audit payload. The audit event carries counts and surfaces only.
- [ ] AC-9: A draft update neither wakes an agent, nor re-arms the silence clock, nor counts
      toward `every_n_messages`.
- [ ] AC-10: A fourth `read_drafts` call in one turn returns an error result, not data.
- [ ] AC-11: With `drafts_readable && disclose_drafts`, the composer and the activity panel
      each show the chip; with disclosure off, neither does, and a guest's chatroom DTO
      carries neutral values.
- [ ] AC-12: `disclose_drafts` is patchable by the room creator without
      `RESOURCE_CREATE_EDIT`, and by nobody else.
- [ ] AC-13: The activities slice contains no import of the conversation slice and no socket
      access; `pnpm lint` passes gate #1 and `check:boundaries-enforced` still enforces it.
- [ ] AC-14: `ActivityRenderCtx` exposes exactly five members, asserted as an exact set; the
      `PluginToHostMessage` union carries the new `draft` kind.
- [ ] AC-15: The full Definition of Done passes — `pytest -q`, `ruff`, `mypy`, `pnpm test`,
      `pnpm lint`, `pnpm typecheck`, `pnpm build`, `check:openapi-drift`,
      `check:boundaries-enforced`.

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

Empty. Appended by `/build`.

## 16. Follow-ups

- **FU-1.** The `creative-thinking-room` pack's three prompts forbid quoting a *submission*
  and say nothing about a draft. They must gain the equivalent rule, and the example guide
  must carry the unit-4 caution, **before** any teacher is told to use this grant (§8, §10).
  Recorded as a follow-up only because the pack edit is a documentation-and-prompt change
  with its own review; it is not optional.
- **FU-2.** This feature incidentally makes a half-filled worksheet survive a tab reload on
  the server side for up to the TTL. Nothing restores it to the participant's screen, and the
  guide must not imply otherwise. A real draft-restore feature is separate work.
- **FU-3.** A `draft_activity` presentation block — who is mid-way through which worksheet —
  is the obvious meeting point between this dossier and
  `2026-08-24-observer-presentation-blocks`. Deliberately not in either: it would put unsent
  text into a stored observation, which §2's no-persistence rule forbids without a separate
  decision.
- **FU-4.** `ChatroomView.vue` will own two socket-send responsibilities on one timer after
  this. If a third arrives, the timer belongs in a composable rather than in the view body
  (§9).
