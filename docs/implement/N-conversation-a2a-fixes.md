# N — Conversation & A2A Interaction Bug Remediation

**Status:** Ready for implementation
**Date:** 2026-07-02
**Origin:** Full-path audit of Agent-to-Agent (A2A) and Agent-to-User conversation interactions
(backend turn engine, A2A transport, wakeup/approval/instruct services, presence/WS layer,
compaction, and the frontend conversation slice).
**Scope:** 11 mandatory fixes (FIX-01 … FIX-11) + 6 optional low-severity items (APP-1 … APP-6).

Decision already made by the product owner: **FIX-01 changes the implementation to match the
spec** (R15.01 counts user + agent messages), not the other way around.

---

## 1. How to use this document

- Each fix is self-contained: problem, root cause, exact change sites, design decisions,
  edge cases, and acceptance criteria. Line numbers are anchors as of commit `57a6a77`;
  always locate by function name if lines have drifted.
- Respect the hard SoC rules in `CLAUDE.md` / `backend/CLAUDE.md` / `frontend/CLAUDE.md`:
  routes call facades only; `application/` never imports SQLAlchemy tables directly;
  frontend slices import cross-slice only via `index.ts`; no bare `fetch`/`WebSocket`.
- Commit format: `fix(backend): …` / `fix(frontend): …`, one fix (or one coherent batch)
  per commit. Reference the FIX id in the body.
- Verification commands:
  - Backend: `cd backend && pytest -q && ruff check . && ruff format --check . && mypy .`
  - Frontend: `cd frontend && pnpm test && pnpm lint && pnpm run typecheck`
  - If FIX-05 adds a backend endpoint: `pnpm run gen:api` then `pnpm run check:openapi-drift`.

### Suggested execution order (dependency-aware)

| Batch | Fixes | Rationale |
|-------|-------|-----------|
| 1 | FIX-02, FIX-06, FIX-11 | Backend turn-engine local changes, no API surface change |
| 2 | FIX-01, FIX-03, FIX-07, FIX-08 | Backend behavior changes; FIX-01 depends on nothing but touches several layers |
| 3 | FIX-05 (backend part) | New REST endpoint, then `gen:api` |
| 4 | FIX-04, FIX-05 (frontend part), FIX-09, FIX-10 | Frontend slice; FIX-04 restructures the message cache that FIX-10 builds on |
| 5 | APP-1 … APP-6 | Optional, independent |

---

## 2. Shared background (read once)

### 2.1 Message → wakeup pipeline (current)

```
User POST /api/chatrooms/{id}/messages          (app/api/v1/messages.py:186 send_message)
  └─ MessageService.send  → db.commit()
  └─ post-commit, best-effort:
       _dispatch_message_wakeups      → evaluate_message_wakeups(sender_is_user=True)
                                        → OrchestrationFacade.on_message_created
                                        → enqueue("wakeup_agent", agent, room,
                                                  "every_n_messages", msg.id)
       _dispatch_graphrag_builds      → KnowledgeFacade.evaluate_graphrag_message_triggers
       _dispatch_mention_wakeups      → enqueue("wakeup_agent", …, "mention", msg.id)
       _dispatch_message_workflow_signal

Arq worker wakeup_agent                          (app/workers/tasks/orchestration.py:26)
  └─ guards: room alive, agent alive, autostop (non-mention only)
  └─ TurnEngine.run_turn                         (contexts/agents/application/runtime/turn_engine.py:253)
       └─ per-(agent, room) turn lock            (contexts/agents/infrastructure/turn_lock.py)
       └─ _run_locked → history assembly → provider stream → tool rounds
       └─ MessageService.send_agent → commit → WS publish → workflow signal
  └─ on completed: OrchestrationFacade.on_agent_message_sent (autostop += 1)
```

Key fact driving FIX-01: **`OrchestrationFacade.on_message_created` is only ever invoked from
the user-send route.** The agent-reply path (`turn_engine._run_locked`, after the commit at
~line 931) publishes WS events and a workflow signal but never re-enters the wakeup evaluator.

### 2.2 Redis state used by wakeups (contexts/orchestration/infrastructure/wakeup_state.py)

| Key | Meaning |
|-----|---------|
| `wakeup:msg_count:{agent}:{room}` | every_n counter (INCR per counted message) |
| `wakeup:silence_ts:{agent}:{room}` | last-activity timestamp for silence_minutes |
| `wakeup:autostop:{agent}:{room}` | consecutive agent-only completed rounds; reset on user message |
| `wakeup:silence_active:{agent}:{room}` | "1" while live users are present (R15.05b) |

### 2.3 Providers

`contexts/agents/domain/models.py` — `CHAT_MODEL_CATALOG` providers are `claude`, `openai`,
`gemini`. Adapters live in `contexts/keys/infrastructure/adapters/`. The Anthropic Messages
API **rejects a request whose first message has role `assistant` with HTTP 400**, and expects
all `tool_result` blocks for one parallel tool-use turn inside a **single** user message
(consecutive same-role messages are merged server-side, so splitting is tolerated but
against guidance). This is the factual basis for FIX-02.

---

## FIX-01 — `every_n_messages` must count agent messages too (R15.01)

**Severity:** High
**Spec:** `docs/traceability.csv` R15.01 ("counts all messages in the room (user + agent),
scoped to the room (Q49)"), `docs/implement/G-orchestration.md:80`; GraphRAG analogue
R11.02 in `docs/implement/E-agents-knowledge.md:184` ("every_n_messages (user+agent)").
**Decision:** change the implementation to match the spec.

### Problem

Agent replies never advance any agent's `every_n_messages` counter and never touch any
agent's silence timer, because `on_message_created` is only dispatched from the user-send
route. The misleading comment at `contexts/orchestration/application/wakeup_service.py:92-93`
("counts user-sent messages only … to avoid self-trigger loops") contradicts the spec; loop
protection is the job of `autostop_rounds` (R15.03/R15.04), which currently can never engage
for message-driven inter-agent chains. Consequences in a multi-agent room:

- Agent A's reply cannot wake agent B via every_n — inter-agent conversation relayed through
  room messages is impossible except via silence triggers.
- Agent replies do not touch silence timestamps, so a silence trigger can fire "into" an
  actively agent-busy room.
- GraphRAG message triggers (R11.02: user+agent) are likewise only evaluated on user sends
  (`app/api/v1/messages.py:295 _dispatch_graphrag_builds`).

### Change sites

1. **`contexts/conversation/application/triggers.py` — `evaluate_message_wakeups`**
   Add a keyword-only parameter `sender_agent_id: uuid.UUID | None = None` and pass it
   through to `OrchestrationFacade.on_message_created`. Docstring: set when the message was
   authored by a room-bound agent; used to exclude the author from its own wake list.

2. **`contexts/orchestration/interfaces/facade.py` — `on_message_created`**
   Thread `sender_agent_id` through to the service.

3. **`contexts/orchestration/application/wakeup_service.py` — `on_message_created`**
   - Delete the stale comment at lines 92-93; replace with a comment referencing R15.01/Q49
     and the autostop backstop.
   - Semantics per agent in `agent_ids` (unchanged unless noted):
     - `is_inert()` / `call_only` skip: unchanged.
     - `touch_silence_timestamp`: unchanged — now correctly fires for agent messages too.
     - `reset_autostop`: **only when `sender_is_user=True`** (unchanged — agent replies must
       NOT reset autostop, or the backstop dies).
     - `increment_message_count`: unchanged — increments for **every** bound agent, including
       the author (the counter counts messages in the room, per Q49).
     - Wake decision: when the counter hits a multiple of `n`, **skip appending the author
       itself** (`if agent_id == sender_agent_id: continue` before the presence gate). An
       agent must not be woken by its own reply; its counter still advanced, so it will fire
       on the next counted message like any other agent.

4. **`contexts/agents/application/runtime/turn_engine.py` — `_run_locked`**
   Add a post-commit, best-effort dispatch after the existing
   `_dispatch_agent_message_signal(chatroom_id, final_text)` call (~line 952). New private
   method mirroring the route-side helpers:

   ```python
   async def _dispatch_agent_reply_wakeups(
       self, agent: Agent, chatroom_id: uuid.UUID, message_id: uuid.UUID
   ) -> None:
       """R15.01: an agent reply counts toward other bound agents' every_n
       triggers and touches their silence timers; R11.02: it also feeds
       GraphRAG message triggers. Best-effort and post-commit — a failure
       here must never fail the turn (mirrors app.api.v1.messages)."""
       try:
           from contexts.conversation.application.triggers import (
               evaluate_message_wakeups, list_bound_agent_ids,
           )
           from contexts.knowledge.interfaces.facade import KnowledgeFacade
           from shared_kernel.queue import enqueue

           bound = await list_bound_agent_ids(self._db, chatroom_id)
           fired = await evaluate_message_wakeups(
               self._db,
               chatroom_id=chatroom_id,
               sender_is_user=False,
               sender_agent_id=agent.id,
               bound_agent_ids=bound,
           )
           for aid in fired:
               await enqueue("wakeup_agent", str(aid), str(chatroom_id),
                             "every_n_messages", str(message_id))
           if bound:
               triggers = await KnowledgeFacade(self._db)\
                   .evaluate_graphrag_message_triggers(agent_ids=bound)
               for trig in triggers:
                   await enqueue("graphrag_build", config_id=str(trig.config_id),
                                 triggered_by=trig.triggered_by)
       except Exception:
           _log.warning("agent-reply wakeup dispatch failed room=%s", chatroom_id,
                        exc_info=True)
           with contextlib.suppress(Exception):
               await self._db.rollback()
   ```

   Call it right after `_dispatch_agent_message_signal` in the completed path of
   `_run_locked`. Note the imports already have precedent: `turn_engine.py` imports from
   `contexts.conversation.application.message_service` at module level; keep the new
   imports function-local to match the trigger-module convention.

5. **Loop-safety hardening — `app/workers/tasks/orchestration.py` `wakeup_agent`**
   The autostop guard currently reads
   `if trigger != "mention" and autostop_limit > 0 and autostop_count >= autostop_limit`.
   `WakeupConfig.from_dict` (`contexts/orchestration/domain/models.py:149`) accepts
   `autostop_rounds=0`, which would disable the guard entirely — with agent-message counting
   enabled that permits an indefinite A↔B ping-pong (throttled only by the per-agent-room
   rate bucket of 30 turns / 300 s in `turn_engine.py:76-77`). Change the guard so a
   non-positive `autostop_rounds` falls back to `autostop_max_default` (field exists,
   default 100) instead of "unlimited":

   ```python
   effective_limit = autostop_limit if autostop_limit > 0 else cfg.triggers.silence_minutes.autostop_max_default
   if trigger != "mention" and autostop_count >= effective_limit:
       ...skip...
   ```

### Design notes / edge cases

- **Loop bound analysis (must hold after the change):** user msg resets autostop for all
  bound agents → A fires, replies (autostop_A=1) → A's reply advances B's counter → B fires,
  replies (autostop_B=1) and advances A's counter → … Each completed agent turn increments
  that agent's autostop via `on_agent_message_sent` (worker, `orchestration.py:144`), and
  `wakeup_agent` skips non-mention wakeups at the limit. Chain length is therefore bounded
  by `autostop_rounds` per agent. Add the unit test below to lock this in.
- The presence gate (`allow_self_open=False` + empty room → suppress + owner notification)
  applies unchanged to agent-sourced wakeups. See APP-3 for the related user-sender nuance.
- The coalescing path is unaffected: if the target agent's turn lock is held, `run_turn`
  parks the trigger exactly as for user-message wakeups.
- `sender_is_user=False` must NOT reset autostop — assert this in tests.
- Do not dispatch from `run_input_turn` (headless A2A/approver turns produce no room message).

### Acceptance criteria / tests

- Unit (`tests/unit/orchestration/`): `on_message_created(sender_is_user=False,
  sender_agent_id=A)` with bound agents {A, B}, B configured `every_n(n=1)`:
  returns `[B]`, never `[A]`; A's and B's counters both incremented; B's silence ts touched;
  B's autostop NOT reset.
- Unit: ping-pong scenario — A and B both `every_n(n=1)`, `autostop_rounds=2`: simulate the
  worker loop; total agent turns per agent ≤ 2 until a user message resets.
- Unit: `wakeup_agent` guard with `autostop_rounds=0` uses `autostop_max_default`.
- Unit (`tests/unit/agents/runtime/`): a completed `_run_locked` calls
  `_dispatch_agent_reply_wakeups`; a failed/skipped turn does not; a raising dispatcher does
  not change the turn's `TurnResult`.
- Integration (existing K.3 wiring tier): two-agent room, user sends once, agents relay per
  their `n`, chain stops at autostop.

---

## FIX-02 — Compacted history can start with an assistant message → Anthropic 400 (burns the key group)

**Severity:** High
**Spec:** R9.09–R9.11 (compaction must never break the turn).

### Problem

`choose_range_to_compact` (`contexts/agents/application/context.py:119`) folds the oldest
messages until the token target is met; the fold boundary can land immediately before a
reply authored by the running agent. After reload, `load_model_history` returns
`[summaries…] + [survivors…]`; summaries are folded into the system prompt
(`turn_engine.py:772-774`), so the provider `messages` array starts with the first survivor.
`_provider_message` (`turn_engine.py:1072-1074`) maps the running agent's own rows to
`role: "assistant"` — producing `messages[0].role == "assistant"`. The Anthropic API rejects
this with a deterministic 400, and per the adapter's own warning
(`contexts/keys/infrastructure/adapters/anthropic.py:87-88`) a deterministic 400 is retried
across every key in the group by the router's rotation. Every subsequent turn in that room
repeats the failure until new user messages shift the fold boundary.

Secondary issue in the same area: `_stream_with_tools` (`turn_engine.py:1276-1287`) appends
one neutral `role:"tool"` message per tool result; the Anthropic adapter
(`adapters/anthropic.py:48-61`) translates each into its own `role:"user"` message with a
single `tool_result` block. Anthropic guidance requires all `tool_result` blocks of one
parallel tool-use turn in a single user message (the API merges consecutive same-role
messages, so this currently works, but it is fragile and against documented contract).

### Change sites

1. **`turn_engine.py` — `_run_locked`**, immediately after the `messages` list is fully
   built (after the `input_text` append at ~line 858-867, before the `if not messages`
   guard). Add a provider-neutral guard:

   ```python
   # Providers (Anthropic in particular) reject a leading assistant turn.
   # Compaction can fold the range so the first survivor is this agent's own
   # reply — anchor the conversation with a neutral user turn in that case.
   if messages and messages[0].get("role") == "assistant":
       messages.insert(0, {"role": "user", "content": _HISTORY_RESUME_NOTE})
   ```

   Module constant:

   ```python
   _HISTORY_RESUME_NOTE = (
       "[Conversation resumes; earlier turns were summarized in the system prompt.]"
   )
   ```

   Apply the same guard in `run_input_turn`? Not needed — its `messages` always starts with
   the caller-supplied user input.

2. **`adapters/anthropic.py` — `_translate_messages`**
   Coalesce consecutive `role == "tool"` inputs into ONE Anthropic user message whose
   content is the list of `tool_result` blocks, preserving order. Implementation sketch:
   accumulate `tool_result` blocks in a buffer; flush the buffer as a single
   `{"role": "user", "content": [ …tool_result blocks… ]}` whenever a non-tool message is
   encountered (and at end-of-list). Everything else in the function is unchanged.

3. **`adapters/gemini.py` — `_contents` (verified: same defect shape).** Lines 37-52
   translate each neutral `role:"tool"` message into its OWN `{"role": "user", "parts":
   [functionResponse]}` content. Gemini's parallel function-calling contract expects all
   `functionResponse` parts for one model turn inside a single user content. Apply the same
   coalescing as (2): buffer consecutive tool messages and flush them as one
   `{"role": "user", "parts": [ …functionResponse parts… ]}`.

4. **`adapters/openai.py` — verified correct, leave untouched.** `_messages`
   (`openai.py:96-103`) emits one `role:"tool"` message per `tool_call_id`, which is exactly
   the OpenAI contract. The engine-level guard in (1) covers the leading-assistant case for
   all three providers.

### Edge cases

- Guard must run AFTER attachment-block splicing and the `input_text` append so it sees the
  final shape.
- The MAX_TOOL_ROUNDS fallback path (`final_messages` rebuild at `turn_engine.py:1296`)
  derives from `messages`, whose head is already fixed — no separate guard needed.
- Do not dedent/strip `_HISTORY_RESUME_NOTE` into an empty string; the Anthropic adapter
  drops empty-content messages (`anthropic.py:86-89`), which would resurrect the bug.

### Acceptance criteria / tests

- Unit: build a history whose first user/agent row is the running agent's own message;
  assert the provider payload's `messages[0]["role"] == "user"` and the note content.
- Unit (`tests/unit/keys/adapters/`): `_translate_messages` with
  `[assistant+2 tool_calls, tool, tool, user]` yields
  `[assistant(tool_use×2), user(tool_result×2), user]` — exactly one user message carrying
  both `tool_result` blocks, `tool_use_id`s preserved in order.
- Regression: existing single-tool-round tests still pass unchanged.

---

## FIX-03 — Concurrent first joins leave the silence timer disarmed (R15.02 / R15.05b)

**Severity:** High

### Problem

`app/api/ws/chatroom.py` `on_open` (lines 111-127) publishes the empty→occupied transition
only when `len(await presence.list_room(chatroom_id)) == 1` *after* this user's join. Two
users joining an empty room concurrently interleave as: A roster-SADD, B roster-SADD, A
list_room→2, B list_room→2 — neither calls `_notify_presence(has_live_users=True)`, so
`wakeup:silence_active` is never set and `evaluate_silence_trigger`
(`wakeup_service.py:200`) returns False for every bound agent until the room fully empties
and someone re-enters alone. Multiple people opening a room at meeting start is a common
case, silently disabling silence wakeups for the whole session.

### Change sites

1. **`contexts/conversation/infrastructure/presence.py`**
   Make the roster mutation and its cardinality read atomic, exactly like the existing
   per-user `_JOIN_LUA`/`_LEAVE_LUA` pattern:

   ```python
   _ROSTER_JOIN_LUA = (
       "redis.call('SADD', KEYS[1], ARGV[1]) "
       "redis.call('EXPIRE', KEYS[1], ARGV[2]) "
       "return redis.call('SCARD', KEYS[1])"
   )
   _ROSTER_LEAVE_LUA = (
       "redis.call('SREM', KEYS[1], ARGV[1]) "
       "return redis.call('SCARD', KEYS[1])"
   )
   ```

   - `join(...)` return type changes from `bool` to a small result object (or
     `tuple[bool, int]`): `(first_connection_of_user, roster_size_after)`. Use the Lua for
     the roster SADD instead of the current pipelined `sadd`; the user-rooms back-reference
     stays in the pipeline.
     Guarantee: Redis serializes the Lua calls, so across N concurrent first-joins exactly
     one caller observes `roster_size_after == 1`.
   - `leave(...)`: analogous — return `(last_connection_of_user, roster_size_after)`; use
     `_ROSTER_LEAVE_LUA` for the roster SREM (only executed when the conns refcount hit 0).
     Exactly one concurrent last-leaver observes `0`.

2. **`app/api/ws/chatroom.py`**
   - `on_open`: `added, roster_size = await presence.join(...)`; emit `presence.joined`
     when `added`; call `_notify_presence(chatroom_id, has_live_users=True)` when
     `roster_size == 1`. Delete the `list_room` re-read.
   - `on_close`: `left, roster_size = await presence.leave(...)`; emit `presence.left` when
     `left`; call `_notify_presence(chatroom_id, has_live_users=False)` when
     `roster_size == 0`. Delete the `list_room` re-read.

3. **Callers (verified):** the only production call sites of `PresenceTracker.join` /
   `PresenceTracker.leave` are `app/api/ws/chatroom.py:112` and `:130`; `wakeup_service`
   only calls `list_room`. Update those two call sites plus any unit tests that construct
   the tracker directly.

### Edge cases

- The roster key carries `_SET_TTL_SECONDS = 300` refreshed by heartbeats; the Lua must keep
  applying `EXPIRE` on join (it does above). Heartbeat path unchanged.
- Keep `on_presence_changed` semantics unchanged (sets `silence_active` and touches the
  timestamp) — with the atomic transition detection, it is again called exactly once per
  empty→occupied edge, so the original "don't re-touch on every joiner" intent is preserved.

### Acceptance criteria / tests

- Unit (fakeredis or the integration Redis tier): fire `join` for two different users
  concurrently (`asyncio.gather`) against an empty roster → exactly one result has
  `roster_size_after == 1`.
- Unit: two tabs of the same user → `first_connection` True exactly once and
  `roster_size_after == 1` exactly once.
- Unit: concurrent leaves of the two last users → exactly one observes `0`.
- WS-level test: simulate two concurrent `on_open` → `on_presence_changed` called once with
  `has_live_users=True`; `wakeup_state.is_silence_active` is True afterwards.

---

## FIX-04 — Frontend message cache: sliding window drops messages and creates an unfillable gap

**Severity:** High
**Files:** `frontend/src/slices/conversation/composables/useChatroomMessages.ts`,
`useChatroomSocket.ts` (+ small store/API touch-ups)

### Problem

The "recent" pane is `useQuery({ queryFn: () => listMessages(chatroomId, { limit: 100 }) })`
(`useChatroomMessages.ts:78-81`) — a snapshot of the latest 100. Every `message.created`
WS event blind-invalidates it (`useChatroomSocket.ts:132`), and TanStack also refetches on
window focus. Each refetch REPLACES the cache with the newest 100, so:

- Once the user has clicked "Load earlier" (`olderMessages` non-empty), messages that fall
  between the older pane's tail and the refetched window's head are in neither pane, and the
  load-earlier cursor (`messages.value[0]` = oldest of the older pane,
  `useChatroomMessages.ts:130`) can never paginate the gap back in.
- Even without an older pane, messages silently vanish from view mid-read whenever >0 new
  messages arrive while scrolled up.

Two adjacent defects are fixed together because the delta path depends on them:

- **Dead cursor-seed watch** (`useChatroomSocket.ts:286-294`): `qc.getQueryData(...)` inside
  `watch(() => …)` has no reactive dependency (verified against @tanstack/vue-query — it is
  a plain query-core read), so it fires exactly once while the initial fetch is still in
  flight and `lastSeenMessageId` is never seeded from fetched history. The designed
  `GET /messages?since=` replay never runs for the initial-history case.
- **Streamed-draft flicker** (`useChatroomSocket.ts:130-142`): `clearAgentStream` runs
  synchronously on `message.created`, but the persisted bubble only appears after the
  refetch completes — the agent's whole answer blinks out for a round-trip.

### Target design

Make the message cache **append-only within a session** and drive live updates through the
existing `since`-delta machinery instead of blind invalidation:

1. **Additive merge in the query.** In `useChatroomMessages.ts`, replace the raw `queryFn`
   with a merging one:

   ```ts
   queryFn: async () => {
     const page = await listMessages(chatroomId, { limit: PAGE_SIZE })
     const prev = qc.getQueryData<Message[]>(convKeys.messages(chatroomId)) ?? []
     return mergeMessages(prev, page)   // dedupe by id, prefer fetched rows
   },
   ```

   `mergeMessages(prev, next)`: union by `id` (rows present in `next` win — they carry fresh
   `version`/`edited_at`), sorted by `created_at` then `id` for a stable tiebreak. Export it
   from a small util so tests can target it. Memory grows with the session's live traffic
   only; acceptable (documented decision).

   Ordering note (verified): the backend returns messages NEWEST-first and the
   `listMessages` wrapper passes the array through unchanged
   (`slices/conversation/api/index.ts:144-153`), while `applyMessageCreated` appends at the
   tail — the raw cache order is therefore mixed today and only the `messages` computed
   sorts it. `mergeMessages` must canonicalize to ascending `created_at` so the cursor
   logic and window-boundary math below are well-defined.

   Deletion handling with an additive merge: on refetch, compute
   `windowStart = min(created_at of fetchedPage)` (do NOT use index 0 — the page arrives
   newest-first); any cached message with `created_at >= windowStart` that is absent from
   `fetchedPage` was hard-deleted — drop it during the merge. Messages older than the
   fetched window are left untouched (their deletions arrive via the `message.deleted`
   event, or at worst on full reload).

2. **`message.created` → delta append, not invalidation.** In `useChatroomSocket.ts`
   `handleEvent`, replace the `invalidateQueries` in the `message.created` branch with
   `void replayDelta()`. `replayDelta` already: fetches from the `since` cursor, applies
   `applyMessageCreated` per row, and falls back to invalidation when there is no cursor
   (`:68-74`) or on a 422 (`:80-87`) — with (1) in place, even the fallback invalidation is
   now non-destructive.

   Cursor-ordering rule (load-bearing): DELETE the current `lastSeenMessageId.value = mid`
   assignment in that branch. The cursor must advance only from `applyMessageCreated`
   (already done at `:97`) / the cache subscription in (3) — i.e. only after the row is in
   the cache. Pre-writing the event's own id would make the delta fetch
   `since=<that id>` skip the very message the event announced.

3. **Fix the cursor seeding (dead watch).** Replace the `watch` at `:286-294` with a
   QueryCache subscription:

   ```ts
   const messagesKey = convKeys.messages(roomId)
   const unsubCache = qc.getQueryCache().subscribe((event) => {
     const k = event.query.queryKey
     if (k[0] !== messagesKey[0] || k[1] !== messagesKey[1] || k[2] !== messagesKey[2]) return
     const data = event.query.state.data as Message[] | undefined
     const newest = newestId(data)          // row with max created_at, not data.at(-1)
     if (newest) lastSeenMessageId.value = newest
   })
   ```

   Compare the queryKey structurally as above (it is a 3-tuple,
   `['conversation','messages',roomId]` — `frontend/src/slices/conversation/queries/index.ts:20`).
   `hashKey` from `@tanstack/vue-query` (re-exported from `@tanstack/query-core` via
   `export *`) is an acceptable alternative. Dispose the subscription in `onBeforeUnmount`
   alongside the other unsubscribes. Guard against regressions: the cursor must only move
   forward — `newestId` must select the row with the max `created_at` (raw cache order is
   not guaranteed; see the ordering note in step 1) and the assignment should be skipped
   when the candidate is not newer than the current cursor's row.

4. **`message.updated` / `message.deleted` → targeted cache surgery in the recent pane**
   (replaces the blanket invalidation at `useChatroomSocket.ts:144-146`). The OLDER pane is
   already handled: `ChatroomView.vue:415-416` subscribes the same WS channel and calls
   `refreshOlderMessage` / `dropOlderMessage` (verified) — leave that wiring as is. In the
   socket composable only:
   - `message.updated`: fetch the single row (`getMessage(mid)`, already exported from
     `../api`) and map-replace it by id in the recent cache via `setQueryData`.
   - `message.deleted`: remove the id from the recent cache via `setQueryData` filter.
   Rationale for dropping the invalidation: with the additive merge it can no longer lose
   rows, but a refetch cannot express a deletion of an out-of-window row, and it is a full
   page round-trip where a one-row operation suffices.

5. **Streamed-draft flicker (was audit finding F5).** In `applyMessageCreated`
   (`useChatroomSocket.ts:90-103`), when `m.sender_type === 'agent' && m.sender_id`, call
   `store.clearAgentStream(roomId, m.sender_id)` AFTER the `setQueryData` append — the
   persisted bubble and the draft clear now land in the same tick. In `handleEvent`'s
   `message.created` branch, remove the eager `clearAgentStream` (keep `clearAgentError`
   there or move it too — either is fine as long as both run post-append). The
   unconditional clear in `agent.finished` (`:187`) stays as the safety net; note it fires
   AFTER `message.created` on the backend (`turn_engine.py:939-948` publishes
   `message.created` then `agent.finished`), so with the same-tick append the visible gap is
   closed in the normal path.

6. **Reconnect path** (`onStatus`, `:240-252`): unchanged — `replayDelta()` now actually has
   a cursor thanks to (3). The degraded 10 s poll (`startPolling`) also becomes a true delta
   poll instead of a full-page refetch.

### Acceptance criteria / tests (Vitest)

- `mergeMessages`: union/dedup/prefer-fetched/version-bump/sort; in-window deletion dropped;
  out-of-window rows preserved.
- Socket handler: `message.created` triggers a since-fetch and appends without touching rows
  older than the window; no `invalidateQueries` call in that branch.
- Cursor: after the initial query resolves, `lastSeenMessageId` equals the newest row's id;
  after a delta append it advances; it never moves backward.
- Flicker: with a fake store, the agent's stream is cleared only after its persisted message
  is present in the cache.
- Scenario test (component level): older pane loaded (1-100), recent 101-200; 10 new
  messages arrive via WS → all of 101-210 remain renderable, `loadEarlier` cursor unchanged.

---

## FIX-05 — Presence roster: no snapshot on join, no resync on reconnect (ghost/missing users)

**Severity:** High
**Files:** backend `app/api/v1/chatrooms.py` (+ conversation interfaces), frontend
`useChatroomSocket.ts`, `stores/conversation.ts`, `slices/conversation/api`

### Problem

The backend only emits `presence.joined` / `presence.left` deltas (`app/api/ws/chatroom.py`),
and the frontend reconstructs the roster purely from deltas observed while its own socket is
open (`useChatroomSocket.ts:148-154`; view builds `onlineUsers` from the store). There is no
snapshot on join and no resync on reconnect (`onStatus` clears thinking/streams but not
presence/typing). Results: users already present before you joined never appear; users who
left while your socket was down remain "online" (and "typing…") forever.

### Change sites — backend

1. **New endpoint** in `app/api/v1/chatrooms.py`:

   ```
   GET /api/chatrooms/{chatroom_id}/presence
   → 200 {"user_ids": ["<uuid>", ...]}
   ```

   (Verified: no presence endpoint currently exists anywhere under `app/api/v1/`.)

   - Auth: `resolve_room_access` + `ensure_can_read` (identical to `list_messages` in
     `app/api/v1/messages.py:153-167`).
   - Implementation: `PresenceTracker` is already exported from
     `contexts.conversation.interfaces` (the WS route imports it from there); the route may
     use it directly, same as `app/api/ws/chatroom.py` does. Response is a Pydantic model
     (`PresenceOut`), not a raw dict.
   - This endpoint reads Redis only; no DB write, no audit.

2. Regenerate the frontend client: `pnpm run gen:api`; `pnpm run check:openapi-drift`.

### Change sites — frontend

3. **Store (`stores/conversation.ts`)**: add
   - `setPresence(roomId: string, userIds: string[])` — replace the room's presence set
     immutably (same style as `joinPresence` at `:45-49`).
   - `clearTyping(roomId: string)` — drop the room's whole typing set.

4. **API wrapper** (`slices/conversation/api`): `getChatroomPresence(roomId): Promise<string[]>`.

5. **Socket composable (`useChatroomSocket.ts`)**: add

   ```ts
   async function resyncPresence(): Promise<void> {
     try {
       const ids = await getChatroomPresence(roomId)
       store.setPresence(roomId, ids)
       store.clearTyping(roomId)
     } catch { /* best-effort; deltas keep flowing */ }
   }
   ```

   Call `void resyncPresence()` inside `onStatus` when `isConnected` becomes true (i.e. on
   first connect AND every reconnect), next to the existing `replayDelta()` call at `:251`.
   Delta events continue to apply on top.

### Edge cases

- Race between snapshot fetch and a concurrent `presence.joined` delta: harmless — deltas
  apply to the replaced set; a user who joined between snapshot read and response is added
  by the delta; one who left is removed. Fetch-and-replace is idempotent on the next
  reconnect anyway.
- Guests: `PresenceTracker.list_room` returns user ids including guest users — the view
  already resolves labels for guests; no change.
- Backend roster contains only WS-connected users by design (see APP-3 for the REST-sender
  nuance; out of scope here).

### Acceptance criteria / tests

- Backend unit/route test: 200 with roster for a member; 403/404 semantics identical to
  `list_messages` for non-members.
- Frontend: on simulated reconnect, `setPresence` replaces a stale roster and typing is
  cleared; a `presence.joined` delta arriving after the snapshot is additive.
- Manual E2E: open room with another session already present → roster shows both.

---

## FIX-06 — Coalesced-trigger TOCTOU: a message arriving mid-turn can be dropped

**Severity:** Medium
**File:** `contexts/agents/application/runtime/turn_engine.py`

### Problem

`run_turn` (`turn_engine.py:253-311`): a contender that fails the lock marks
`turn:queued:{agent}:{room}` (SETNX) *after* its failed acquire; the holder pops the flag
*after* releasing the lock. Interleaving "contender acquire-fails → holder releases → holder
pops (empty) → contender marks" strands the flag: nobody re-enqueues, and if no further
trigger arrives the user's mid-turn message never gets a reply. The flag also carries
`ex=DEFAULT_TURN_TTL_S` (300 s, `turn_lock.py:23` → `distributed_lock.py:25`) while the lock
itself is heartbeat-extended indefinitely — a turn longer than 5 minutes expires the parked
trigger even in the happy path.

### Change sites

1. **Close the race with a bounded acquire-retry on the contender side.** Restructure
   `run_turn`'s locking section:

   ```python
   async def run_turn(self, *, agent_id, chatroom_id, trigger, ...) -> TurnResult:
       started = time.monotonic()
       result: TurnResult | None = None
       for attempt in range(2):
           async with turn_lock(agent_id, chatroom_id) as acquired:
               if acquired:
                   if attempt > 0:
                       # We acquired on the retry, which means our own mark from
                       # attempt 0 (or an earlier stranded one) is still parked.
                       # Consume it NOW and fold it into this turn's arguments —
                       # otherwise the post-release pop below would re-enqueue a
                       # redundant follow-up turn for the trigger we are about to
                       # serve. SETNX means the parked trigger string may belong
                       # to an EARLIER contender; prefer the popped values.
                       parked = await _pop_queued_trigger(agent_id, chatroom_id)
                       if parked is not None:
                           trigger, parked_mid = parked
                           trigger_message_id = parked_mid or trigger_message_id
                   result = await self._run_locked(...)
                   break
               await _mark_trigger_queued(agent_id, chatroom_id, trigger, trigger_message_id)
               # Re-check once: the holder may have released AND popped before our
               # mark landed, in which case nobody will ever drain it. If the lock
               # is now free, we take it ourselves and the loop runs the turn; if it
               # is still held, the holder's post-release pop will see our mark.
           if result is None and attempt == 0:
               continue
       if result is None:
           AGENT_TURNS_TOTAL.labels(result="skipped").inc()
           return TurnResult(status="skipped", reason="locked")
       # existing post-release coalesced-pop + enqueue + metrics, unchanged
       ...
   ```

   Semantics: at most one extra non-blocking acquire attempt. If attempt 1 also fails, the
   lock is genuinely held by someone who acquired after our mark — that holder's
   post-release `_pop_queued_trigger` drains it. The orphan window is closed because the
   only way our mark can be missed is if the holder popped before the mark landed, which
   implies the lock was free at our second attempt.

   Note the second attempt runs a FULL turn including its own guards — no special-casing.
   Duplicate-reply risk is unchanged from today's semantics (coalescing is at-least-once by
   design).

2. **Decouple the parked-trigger TTL from the lock TTL.** In `_mark_trigger_queued`
   (`turn_engine.py:143-179`), replace `ex=DEFAULT_TURN_TTL_S` with a dedicated
   `_QUEUED_TRIGGER_TTL_S = 3600` (module constant, comment: must exceed any realistic
   heartbeat-extended turn; the flag is popped after every turn so it never lingers under
   normal operation). Same TTL for the companion message-id key.

### Acceptance criteria / tests

- Unit: with a fake lock that reports "held" on the first attempt and "free" on the second,
  `run_turn` runs the turn (not skipped) and no orphan flag remains.
- Unit: with a lock held on both attempts, `run_turn` returns `skipped/locked` and the flag
  is set for the holder to drain (existing behavior).
- Existing coalescing tests unchanged: holder pops and re-enqueues exactly one follow-up.

---

## FIX-07 — `approval.resolved` published from inside an uncommitted transaction

**Severity:** Medium
**File:** `contexts/orchestration/application/approval_service.py`

### Problem

`cast_vote` (`approval_service.py:197-225`) runs on the caller's session — for agent votes
that is the turn engine's session, mid-turn (the tool is invoked from `_stream_with_tools`).
`_try_resolve` (`:298-349`) performs the DB state CAS and then immediately emits the
`approval.resolved` WS events and enqueues `workflow_resume_approval` — both Redis side
effects that cannot be rolled back. If the turn later fails (provider error in a subsequent
round), `_run_locked` rolls back: the vote and the state flip are undone (DB back to
PENDING), but clients already rendered "resolved", and the eventual timeout emits a second —
possibly contradictory — resolution. `handle_timeout` (`:231-292`) has the same
publish-before-commit shape relative to the worker task's commit
(`app/workers/tasks/orchestration.py:163-186`).

### Change sites

Precedent for a service-level commit exists in this codebase
(`WakeupService._notify_wakeup_gated` commits per owner; `TurnEngine._persist_artifacts`
commits its own unit). Apply the same pattern:

1. **Split `_try_resolve`** into:
   - `_resolve_state(approval, votes) -> ApprovalState | None` — pure DB writes: the
     `update_state` CAS and the `approval.resolved` audit row. No metrics, no publish, no
     enqueue.
   - `_emit_resolution_effects(approval, state, *, chatroom_id)` — `APPROVAL_RESOLUTIONS`
     metric, `_publish_resolved`, `_enqueue_workflow_resume`.

2. **`cast_vote`**: after `self._votes.cast(...)` and `state = _resolve_state(...)`:

   ```python
   await self._db.commit()          # vote + (optional) resolution are now durable
   if state is not None:
       await self._emit_resolution_effects(approval, state, chatroom_id=chatroom_id)
   ```

   Commit unconditionally (also when `state is None`): the ballot itself must survive a
   later turn failure — today a rolled-back vote is silently lost and only recovered
   because the drained notification is requeued and the agent re-votes; after this change a
   requeued approval notification leading to a second `cast_approval_vote` call will hit the
   `already resolved` / duplicate-ballot path and surface as a tool error, which is correct
   and harmless (document this in the tool's docstring).

3. **`handle_timeout`**: same restructure — perform `update_state` CAS + audit, then
   `await self._db.commit()`, then effects. The worker task's trailing `db.commit()` becomes
   a no-op; leave it (it also covers the `approval gone` early return path).

4. **Document the mid-turn commit implication** at the `cast_vote` call site
   (`tool_registry.build_cast_approval_vote_tool`): committing inside a room turn also
   commits any writes flushed earlier in that transaction segment (router usage events,
   tool audits). Those are all post-pre-stream-commit writes that would have committed with
   the reply anyway; reply persistence atomicity is unaffected because
   `MessageService.send_agent` happens later in its own segment.

### Acceptance criteria / tests

- Unit: `cast_vote` that resolves the gate → publish/enqueue observed only after the session
  reports committed (use a session spy or transaction hook).
- Unit: turn failure after a vote → vote row still present; approval state still resolved;
  exactly one `approval.resolved` emission (no phantom-then-timeout double).
- Unit: CAS loser (`update_state` returns False) emits nothing (unchanged).
- Existing approval-mode resolution tests (single/majority/consensus) unchanged.

---

## FIX-08 — A2A CALL timeout vs. turn duration mismatch; no cancellation

**Severity:** Medium
**Files:** `contexts/orchestration/application/a2a_service.py`,
`contexts/orchestration/infrastructure/a2a_rendezvous.py`,
`contexts/orchestration/application/a2a_handler.py`,
`contexts/agents/application/runtime/turn_engine.py`,
`contexts/workflow/application/executors/agent_invocation.py`,
`docs/workflow.schema.json:245` + `docs/workflow.schema.md` (documented default)

### Problem

`_DEFAULT_CALL_TIMEOUT = 60.0` (`a2a_service.py:42`) and the workflow node default of 120 s
(`agent_invocation.py:46`) are routinely shorter than a real callee turn: up to
`MAX_TOOL_ROUNDS = 8` provider rounds plus a final no-tools round
(`turn_engine.py:70,1242-1346`), each potentially with web_search/code_exec. On timeout the
caller raises `A2ATimeout` and the workflow takes the failure port, **but the callee turn
keeps running to completion** — full provider spend on the user's own keys (BYO-key) — and
the reply lands in a rendezvous list nobody reads (TTL 900 s).

### Change sites

1. **Raise the defaults** to fit the worst realistic turn:
   - `a2a_service.py`: `_DEFAULT_CALL_TIMEOUT = 300.0`.
   - `agent_invocation.py:46`: `config.get("timeout_seconds", 300)`.
   - `docs/workflow.schema.json:245` (verified anchor): the `agent_invocation` node's
     `timeout_seconds` is `{minimum: 1, maximum: 600, default: 120}` — change `default` to
     `300` (the existing `maximum: 600` already accommodates it). Mirror the change in
     `docs/workflow.schema.md` and any frontend node-editor default (grep the workflow
     slice for `timeout_seconds` defaults).
   - Note: the caller blocks on `BLPOP` inside a worker task; a longer default increases
     worker occupancy for that job — acceptable, but mention in the commit message.

2. **Cancellation flag** so a timed-out CALL stops burning tokens at the next round
   boundary:
   - `a2a_rendezvous.py`: add

     ```python
     def _cancel_key(correlation_id): return f"a2a:cancel:{correlation_id}"

     async def mark_call_cancelled(correlation_id) -> None:
         await get_redis().set(_cancel_key(correlation_id), "1", ex=_REPLY_TTL_SECONDS)

     async def is_call_cancelled(correlation_id) -> bool:
         return await get_redis().get(_cancel_key(correlation_id)) == "1"
     ```

   - `a2a_service.call`: on `reply is None` (timeout), call
     `await a2a_rendezvous.mark_call_cancelled(correlation_id)` before raising `A2ATimeout`.
   - `turn_engine.run_input_turn`: new optional keyword
     `cancel_check: Callable[[], Awaitable[bool]] | None = None`, forwarded to
     `_stream_with_tools`.
   - `_stream_with_tools`: at the TOP of each `for rounds in range(...)` iteration (and once
     before the final no-tools fallback call):

     ```python
     if cancel_check is not None and await cancel_check():
         raise _TurnCancelled()
     ```

     where `_TurnCancelled` is a module-private exception. `run_input_turn` catches it
     BEFORE the generic handler and returns
     `TurnResult(status="skipped", reason="cancelled")`; on this path COMMIT (do not roll
     back) the session — provider usage events for rounds that already ran represent real
     spend and must survive (mirror the audit+commit shape of the failure path, minus the
     rollback). Notification requeue rule: requeue the drained notes only if cancellation
     happened before the first provider round (`rounds == 0` — the model never saw them);
     after round 1 the model has seen them, do not requeue. Track the round count in the
     exception or a nonlocal.
     The room-turn path (`_run_locked`) does NOT get a cancel_check — only headless CALL
     turns are cancellable.
   - `a2a_handler._run_turn_with_db`: pass
     `cancel_check=lambda: a2a_rendezvous.is_call_cancelled(envelope.correlation_id)`
     (only when `envelope.type is CALL`; the INSTRUCT path keeps `None` — instructs have
     their own deadline machinery).
   - `a2a_handler._handle_call`: when `result.status == "skipped"` and
     `result.reason == "cancelled"`, return WITHOUT delivering an error reply (the caller
     already timed out and is gone; delivering would only populate an expiring list). All
     other non-completed results keep the existing `_deliver_error` fail-fast.

3. **Audit**: in `run_input_turn`, emit `agent.turn_cancelled` (audit action) on the
   cancelled path so operators can see spend truncated by caller timeouts.

### Acceptance criteria / tests

- Unit: `call()` timeout marks the cancel key and raises `A2ATimeout`.
- Unit: `_stream_with_tools` with a cancel_check returning True after round 1 stops before
  round 2's provider call; `run_input_turn` returns `skipped/cancelled`; notifications NOT
  requeued (round ≥ 1) / requeued (round 0).
- Unit: `_handle_call` ACKs (returns normally) on a cancelled result without calling
  `deliver_reply`.
- Config: workflow `agent_invocation` without `timeout_seconds` uses 300.

---

## FIX-09 — Edit-conflict (412) recovery reopens the editor with the stale version

**Severity:** Medium
**File:** `frontend/src/slices/conversation/composables/useChatroomMessageEditing.ts`

### Problem

`saveEdit`'s catch (`:54-63`) restores `editVersion.value = version` — the exact version
that just failed `If-Match` — and reopens the editor. Every subsequent Save resends the
stale version and fails again; the only escape is cancel + re-enter.

### Change

In the catch block, refresh the version from the server before reopening:

```ts
} catch {
  if (prevRecent) qc.setQueryData(key, prevRecent)
  editingId.value = id
  editDraft.value = text            // never lose the user's rewrite
  try {
    const fresh = await getMessage(id)        // slices/conversation/api (already exists)
    editVersion.value = fresh.version
    // also reconcile the cache so the underlying bubble shows the concurrent edit:
    qc.setQueryData<Message[]>(key, (prev) =>
      prev?.map((m) => (m.id === fresh.id ? fresh : m)),
    )
  } catch {
    editVersion.value = version     // offline/deleted: keep old behavior
  }
  toast.error(t('conversation.chatroom.editFailed'))
}
```

Notes:
- Do not attempt to distinguish 412 from network errors in the first pass — the
  unconditional refresh is correct for both (a network failure keeps the old version via the
  inner catch).
- If `getMessage` 404s (message deleted concurrently), the inner catch leaves the stale
  version; the next Save fails again and the delete will arrive via `message.deleted` —
  acceptable. Optionally close the editor on 404 if the API error is distinguishable.

### Acceptance criteria / tests

- Vitest: mock `apiEditMessage` to reject once (simulating 412) and `getMessage` to return
  `version: 5` → editor reopens with the draft text, `editVersion === 5`, and a retried
  save calls the API with version 5.
- Network-failure path: `getMessage` rejects → `editVersion` unchanged, editor reopen intact.

---

## FIX-10 — Delete/edit failure rollback clobbers concurrent WS updates

**Severity:** Medium
**Files:** `frontend/src/slices/conversation/composables/useChatroomMessages.ts`
(`confirmDelete`, `:253-275`), `useChatroomMessageEditing.ts` (`saveEdit`, `:39-58`)

### Problem

Both failure paths restore a WHOLE-LIST snapshot (`prevRecent`) captured before the
mutation. Any message that arrived (via WS-driven cache writes) while the request was in
flight is erased by the restore and stays missing until the next refetch.

### Change

Replace snapshot-restore with targeted, id-scoped rollback:

- **`confirmDelete`**: drop the `prevRecent`/`prevOlder` snapshots. On failure, re-insert
  the single removed message:

  ```ts
  } catch {
    qc.setQueryData<Message[]>(key, (prev) => {
      if (!prev) return [m]
      if (prev.some((x) => x.id === m.id)) return prev
      return mergeMessages(prev, [m])      // FIX-04's util keeps ordering canonical
    })
    // if it lived in the older pane, put it back there instead:
    // (detect via prevOlder.some(x => x.id === m.id) captured as a boolean, not a snapshot)
    toast.error(t('conversation.chatroom.deleteFailed'))
  }
  ```

  Capture only `const wasInOlder = olderMessages.value.some((x) => x.id === m.id)` before
  the optimistic removal, and restore into the correct pane.

- **`saveEdit`**: drop `prevRecent`. Capture the original row
  (`const original = qc.getQueryData<Message[]>(key)?.find((x) => x.id === id)`) before the
  optimistic swap; on failure, map-replace only that id back to `original` (when found).
  Combined with FIX-09, the subsequent `getMessage` refresh supersedes this anyway.

Sequencing: implement after FIX-04 so `mergeMessages` exists.

### Acceptance criteria / tests

- Vitest: message C arrives (cache append) while a delete of message B is in flight; the
  delete fails → both B and C are present afterwards.
- Same shape for edit failure: concurrent append survives the rollback; only the edited
  row's content reverts.

---

## FIX-11 — Concurrent compaction in multi-agent rooms writes duplicate summaries

**Severity:** Medium
**File:** `contexts/agents/application/runtime/turn_engine.py` (`_assemble_history`,
`:1108-1176`)

### Problem

Compaction has no per-room mutual exclusion. Two agents' turns in the same room can both
cross the cap concurrently, both summarise the same oldest range, and insert two
`compact_summary` rows listing overlapping `compacted_ids`. `load_model_history`
(`contexts/agents/application/runtime/transcript.py:174-181`) keeps EVERY summary row
forever, so the duplicate summary text is re-sent to the model on every future turn in that
room — permanent context bloat plus a wasted summariser call (user-billed).

### Change

Wrap the compaction execution (not the decision) in a non-blocking per-room distributed
lock, reusing `shared_kernel.realtime.distributed_lock`:

```python
from shared_kernel.realtime.distributed_lock import distributed_lock

# inside _assemble_history, after `cap` is decided (forced or mode-driven):
async with distributed_lock(f"compact:lock:{chatroom_id}", ttl_s=300) as acquired:
    if not acquired:
        # Another turn in this room is compacting right now. Skip — this turn
        # proceeds with the uncompacted history; the next turn sees the result.
        if forced:
            await self._restore_compact_flag(chatroom_id)   # preserve POST /compact intent
        return history
    try:
        did = await ctxmod.run_compact(...)
    except ctxmod.CompactFailed as exc:
        ...existing handling...
```

Details:

- Lock key is room-scoped (not agent-scoped) — the transcript is a room-level resource.
- `distributed_lock` already heartbeats (ttl/3), covering slow summariser calls.
- **Re-check staleness after acquiring:** between the `projected` computation and lock
  acquisition another turn may have compacted. After acquiring, reload
  `history = await tx.load_model_history(...)`, recompute `projected`, and re-run the
  `should_compact` / forced-cap decision; if no longer needed, release (context manager
  exit) and return the reloaded history. This makes the operation idempotent rather than
  merely serialized.
- `run_compaction` (headless `POST /compact` worker path, `:1178-1197`) goes through
  `_assemble_history` and inherits the lock automatically.
- The `_compact_forced_rooms` re-arm bookkeeping is unchanged; note the skip path above
  restores the flag because `_consume_compact_flag` already consumed it.

### Acceptance criteria / tests

- Unit: two concurrent `_assemble_history` calls (same room, both over cap, fake summariser
  with a latch) → exactly one summary row inserted; the loser returns a history and did not
  call the summariser.
- Unit: loser of a FORCED compaction restores `compact:pending:{room}`.
- Unit: winner path unchanged (existing compaction tests).

---

## Appendix — Optional low-severity items (recommended, not in the mandatory 11)

### APP-1 — `pending_notify.requeue` trims the wrong end

`contexts/orchestration/infrastructure/pending_notify.py:84`: `ltrim(key, 0, _MAX_PENDING-1)`
keeps the HEAD (the restored, oldest notes) and drops the newest — opposite of `push()`
(`ltrim(key, -_MAX_PENDING, -1)`, drops oldest) and of its own comment. Fix: use
`pipe.ltrim(key, -_MAX_PENDING, -1)` and correct the comment. Consequence of the bug: under
notify pressure, fresh approval requests are silently dropped and their gates fall to the
timeout port.

### APP-2 — `scrub_stale_presence` does not close the presence loop

`contexts/conversation/infrastructure/presence.py:141` removes crashed users from rosters but
never emits `presence.left` nor pauses silence timers, so `wakeup:silence_active` stays "1"
(7-day TTL) for a room that is actually empty; an `allow_self_open=true` agent keeps firing
silence wakeups into it. Fix: make `scrub_stale_presence` return the removed
`(room_id, user_id)` pairs; in the retention task (`app/workers/tasks/retention.py:427-438`)
publish `presence.left` per pair on the room channel and, for each room whose roster is now
empty, call `evaluate_presence_change(db, chatroom_id=room, has_live_users=False)`.

### APP-3 — Presence gate ignores the message sender

`wakeup_service.on_message_created` (`:99-106`): the `allow_self_open=False` gate checks WS
presence only. A user sending via REST with no live WS connection is "absent", so the wake is
suppressed and owners get a misleading notification — while the sender is obviously present.
Fix: skip the presence gate when `sender_is_user=True` (the triggering message IS evidence of
presence). Agent-sourced evaluations (after FIX-01) keep the gate. One-line condition:
`if not cfg.allow_self_open and not sender_is_user:`.

### APP-4 — "Load earlier" spuriously triggers the new-messages pill

`useChatroomScroll.ts:60-67` treats any `messageCount` growth as new traffic. Expose
`suppressNextGrowth(n: number)` (or a boolean latch set by `captureBeforePrepend` and cleared
by `restoreAfterPrepend`) so prepends don't increment `newCount`. Wire it in
`ChatroomView.vue`'s `onLoadEarlier`.

### APP-5 — Optimistic-echo dedup can hide an old identical message

`useChatroomMessages.ts:107-125` matches persisted twins by `sender_id + content_md` with no
recency bound; while a send is in flight, an older identical message is hidden. Fix: only
consider persisted candidates with `created_at >= optimistic.created_at - 60s` (clock-skew
tolerant), i.e. thread each pending's timestamp into the matching loop.

### APP-6 — `/compact` command has no error handling

`useChatroomMessages.ts:190-194`: wrap `compactChatroom` in try/catch; on failure restore
`draft.value = text`, `toast.error(...)` (add an i18n key, e.g.
`conversation.chatroom.compactFailed` — remember the vue-i18n literal-`@` escaping rule if
the message ever contains `@`), and return false.

---

## Test matrix summary

| Fix | Unit | Integration/WS | Frontend (Vitest) | Manual/E2E |
|-----|------|----------------|-------------------|------------|
| 01 | wakeup semantics, ping-pong bound, worker guard | 2-agent relay | — | multi-agent room relay |
| 02 | engine head-guard, adapter tool_result merge | — | — | compacted Claude room |
| 03 | atomic roster Lua races | double-join WS | — | two browsers join |
| 04 | mergeMessages, cursor, deletions | — | socket handler, scenario | scrollback + live traffic |
| 05 | route auth | — | resync on reconnect | pre-existing occupants |
| 06 | lock retry paths | — | — | — |
| 07 | commit-before-publish, turn-failure survival | — | — | — |
| 08 | cancel flag, round-boundary abort | — | — | long-tool-call workflow node |
| 09 | — | — | 412 retry with fresh version | concurrent edit |
| 10 | — | — | rollback preserves concurrent rows | — |
| 11 | concurrent compaction latch | — | — | — |

## Out of scope (explicitly)

- Multi-replica A2A consumer partitioning (two workers legitimately share one agent's inbox
  consumer group today; serialization per agent is not guaranteed across replicas).
- Instruct `mark_delivered` racing the workflow executor's commit (window is milliseconds
  and self-heals at `mark_completed`; revisit only if `instruct.delivered` audit gaps show
  up in production).
- Provider-adapter behavior for Gemini first-message constraints beyond the engine-level
  guard in FIX-02.
