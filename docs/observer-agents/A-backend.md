# Phase A — Observer Agents Backend

Everything server-side: schema, domain, repositories, access rule, trigger
gating, the observer turn variant, the observations API, both release paths,
audit, and the leak-proof test suite. One migration (`0041_observer_agents`).

File references use `path::symbol`; line numbers were verified against the
tree as of 2026-07-02 and are close anchors, not gospel — symbols are
authoritative.

## A.0 Requirement IDs (to be merged into SRS §28)

| ID | Requirement |
|---|---|
| R28.01 | A chatroom agent binding carries a role `normal \| observer`. Observer output is never persisted as a room message and never emitted on the room WS channel. |
| R28.02 | `chatrooms.created_by_user_id` records the creating user; legacy rooms with NULL fall back to moderator semantics. Admin bypass applies. Guests are always denied observer surfaces. |
| R28.03 | Observations are persisted in `agent_observations` and readable only by the room creator (per R28.02 resolution). |
| R28.04 | Observers wake via the existing `every_n_messages` and `silence_minutes` triggers; they are excluded from the @mention candidate set. |
| R28.05 | Observer turns read the full room transcript plus a bounded window of the observer's own prior observations. |
| R28.06 | The creator can release an observation to the room; the result is a `sender_type=system` message with `metadata.type='released_observation'`, attributed to the creator, that then follows the standard broadcast and wake-up paths. |
| R28.07 | The creator can release an observation privately to one or more normal-role agents via the `pending_notify` queue; the room sees nothing. Waking the targets is opt-in per release. |
| R28.08 | Release accepts an optional content override; the stored observation is immutable. |
| R28.09 | `chatrooms.disclose_observers` (default true) controls whether non-creators see a neutral "observers enabled" indicator. Only the creator can change it. Which agent observes is never disclosed. |
| R28.10 | Non-creators receive only normal-role bindings from `GET /chatrooms/{id}/agents`. |
| R28.11 | Releases, observer bind/role changes, and disclosure changes are audited. Observation content never appears in audit metadata or logs. |
| R28.12 | Observer turns reuse the per-(agent, chatroom) turn lock, trigger coalescing, and the turn rate limit (`_TURN_RATE_MAX_TURNS=30` / `_TURN_RATE_WINDOW_S=300`). |
| R28.13 | The creator receives `observation.started/created/failed/released` on `ws:user:{creator_id}`; payloads carry ids only, bodies are fetched over REST. |
| R28.14 | The creator can soft-delete an observation. |

## A.1 Migration `0041_observer_agents`

**File:** `backend/alembic/versions/0041_observer_agents.py` (follows 0040).

Forward (`upgrade`):

1. `ALTER TABLE chatrooms ADD COLUMN created_by_user_id uuid NULL REFERENCES
   users(id) ON DELETE SET NULL` and `ADD COLUMN disclose_observers boolean
   NOT NULL DEFAULT true`.
2. `CREATE TYPE chatroom_agent_role AS ENUM ('normal','observer')`; add
   `chatroom_agents.role` with `NOT NULL DEFAULT 'normal'`.
3. Create `agent_observations` exactly as in `00-overview.md` §0.4, including
   the partial index `ix_agent_observations_room`.
4. **Backfill `created_by_user_id`** from the audit trail. Verified:
   `shared_kernel/audit.py::audit_logs` is append-only with timestamp column
   **`created_at`**; producers are `chatroom_service.py::create` (~L99),
   the auto-recreate path in `soft_delete` (~L197), and
   `workspace_service.py::create` (~L76) — all with `resource_type="chatroom"`
   and `actor_user_id` set:

   ```sql
   UPDATE chatrooms c
   SET created_by_user_id = a.actor_user_id
   FROM (
     SELECT DISTINCT ON (resource_id) resource_id, actor_user_id
     FROM audit_logs
     WHERE action = 'chatroom.created' AND resource_type = 'chatroom'
           AND actor_user_id IS NOT NULL
     ORDER BY resource_id, created_at ASC
   ) a
   WHERE c.id = a.resource_id AND c.created_by_user_id IS NULL;
   ```

   Rooms not matched stay NULL (moderator fallback, R28.02).

Downgrade: drop table, drop column + enum, drop the two chatroom columns.
N-1 compatibility holds: all additions have defaults; 0040-era code never
reads them.

**Row-count assertion test**: an integration test seeding two
`chatroom.created` audit rows for one room asserting the earliest actor wins
(`DISTINCT ON … ORDER BY created_at ASC`).

## A.2 Domain models and tables

**`backend/contexts/conversation/domain/models.py`:**

- New `class ChatroomAgentRole(str, Enum): NORMAL = "normal"; OBSERVER =
  "observer"` next to `SenderType`.
- `Chatroom` dataclass gains `created_by_user_id: uuid.UUID | None` and
  `disclose_observers: bool`.
- `ChatroomAgent` gains `role: ChatroomAgentRole`.
- New frozen dataclass `AgentObservation` mirroring the table (id,
  chatroom_id, agent_id, content_md, metadata, trigger, trigger_message_id,
  released_at, release_target, released_by_user_id, created_at, deleted_at).

**`backend/contexts/conversation/infrastructure/tables.py`:**

- `chatrooms`: add the two columns (`server_default=sa.text("true")` for
  `disclose_observers`).
- `chatroom_agents`: add
  `sa.Column("role", pg.ENUM("normal", "observer",
  name="chatroom_agent_role", create_type=False), nullable=False,
  server_default=sa.text("'normal'::chatroom_agent_role"))`.
  This MUST be the PG ENUM type, not `sa.Text` — mirror the
  `message_sender_type` declaration and its warning comment; a Text/ENUM
  mismatch 500s under asyncpg.
- New `agent_observations` table object; export via `__all__`.

## A.3 Repositories

Verified layout: `backend/contexts/conversation/infrastructure/repositories/`
groups repos per domain — `chatroom_repo.py` holds `ChatroomRepository`,
**`ChatroomAgentRepository`**, `ChatroomGuestRepository`; `message_repo.py`
holds `MessageRepository`; all re-exported by the package `__init__.py`.

1. **`ChatroomAgentRepository`** (existing methods, verified: `add` (upsert
   `on_conflict_do_nothing`, ~L205), `remove` (~L216), `list(chatroom_id) ->
   Sequence[ChatroomAgent]` (~L226), `is_registered(*, chatroom_id,
   agent_id)` (~L236), `list_live_bindings` (~L256)):
   - `add(*, chatroom_id, agent_id)` gains keyword `role:
     ChatroomAgentRole = NORMAL`; the upsert's conflict branch must NOT
     overwrite an existing role (`on_conflict_do_nothing` already
     guarantees this).
   - `list(...)` now maps the `role` column into `ChatroomAgent` — all
     existing callers get roles for free.
   - New `role_of(*, chatroom_id, agent_id) -> ChatroomAgentRole | None`
     (returns None when unbound — replaces `is_registered` in the turn
     engine, A.6).
   - New `set_role(*, chatroom_id, agent_id, role) -> bool` (rowcount).
2. **New `ObservationRepository`** (`observation_repo.py`, style reference
   `message_repo.py`):
   - `create(...) -> AgentObservation`
   - `get(*, chatroom_id, observation_id) -> AgentObservation | None`
     (room-scoped lookup — never fetch by id alone; the room id is part of
     the AuthZ boundary)
   - `list(*, chatroom_id, before: uuid | None, limit: int)` — newest-first
     `(created_at DESC, id DESC)` keyset pagination; copy the verified
     anchor mechanism from `MessageRepository.list` (~L79-137): resolve the
     `before` id to its `(created_at, id)` tuple, then filter
     `created_at < anchor OR (created_at = anchor AND id < anchor_id)`.
     Excludes soft-deleted rows.
   - `list_recent_for_agent(*, chatroom_id, agent_id, limit)` — for
     observer self-memory (A.6), ascending chronological return.
   - `mark_released(*, chatroom_id, observation_id, released_by_user_id,
     release_target) -> bool` — single `UPDATE … WHERE released_at IS NULL`
     returning rowcount, so double-release races resolve to exactly one
     winner (409 for the loser).
   - `soft_delete(*, chatroom_id, observation_id) -> bool`.
3. **`ChatroomRepository`** — two verified constraints:
   - `create(...)` (~L58) does NOT receive an actor today; add
     `created_by_user_id: uuid.UUID | None = None` and insert it. Update
     ALL internal callers: `ChatroomService.create` (has `actor_user_id`),
     the auto-recreate inside `soft_delete` (~L193, has `actor_user_id`),
     and `WorkspaceService.create`'s default-room call (~L56, has
     `actor_user_id`). Map the new columns in `_row_to_chatroom` (~L25-38).
   - There is NO per-flag setter and none should be added: flag updates go
     through the generic optimistic-concurrency
     `update(*, chatroom_id, expected_version, values: dict)` (~L171,
     raises `VersionMismatch`, version bumped by the `smap_bump_version`
     trigger). `disclose_observers` rides this existing path via
     `ChatroomFlagsPatch` (see A.8.7).

## A.4 The creator access rule

**File:** `backend/contexts/conversation/application/access.py`.

Verified shapes: `RoomAccess` is frozen/slots with `chatroom`, `project_id`,
`roles: frozenset[Role]`, `is_guest: bool`; `is_moderator` = PROJECT_OWNER or
ORG_OWNER **and deliberately excludes admin** (docstring: admin handled
outside via `principal.is_admin`, which routers pass explicitly into the
`ensure_*` functions). `Principal` (`shared_kernel/auth/permissions.py`
~L98) has exactly `user_id`, `is_admin`, `email_verified` — there is no
guest marker on the principal; guest-ness lives on `RoomAccess.is_guest`
(populated from `ChatroomGuestRepository.is_guest`).

Add one function beside `resolve_room_access`:

```python
def is_room_creator(access: RoomAccess, *, principal: Principal) -> bool:
    if principal.is_admin:
        return True
    if access.is_guest and not access.roles:
        return False          # pure guests are never creators (R28.02)
    room = access.chatroom
    if room.created_by_user_id is not None:
        return principal.user_id == room.created_by_user_id
    return access.is_moderator  # legacy rooms: NULL -> moderator fallback
```

Caution from verification: `access.can_read` is True for guests when
`allow_guest_links` is set, so `ensure_can_read` alone does NOT exclude
guests — the explicit `is_guest` branch above is load-bearing for the
"guest → 403 always" rule.

Every observer surface (observations list/release/delete, role PATCH,
disclosure PATCH, unfiltered bound-agent list) calls `resolve_room_access` +
`ensure_can_read(access, is_admin=principal.is_admin)` first, then this
rule. Deny is the default on any ambiguity. `ChatroomService.create` and
both other creation sites must now persist `created_by_user_id` (A.3.3) so
new rooms never rely on the fallback.

## A.5 Trigger gating

**File:** `backend/contexts/conversation/application/triggers.py`.

Verified: the trigger is a **bare string with no enum/whitelist anywhere**;
the literal set in use is `"every_n_messages"` (default), `"silence_minutes"`,
`"mention"`. Bound agents are fetched ids-only via the module helper
`list_bound_agent_ids(db, chatroom_id)` (~L35), which wraps
`ChatroomAgentRepository.list`.

| Path | Change |
|---|---|
| `evaluate_message_wakeups(db, *, chatroom_id, sender_is_user, sender_agent_id=None, bound_agent_ids=None)` | None. Observers wake like any bound agent — this is the whole point. |
| `filter_mentioned_bound_agents(db, *, chatroom_id, mention_agent_ids, bound_agent_ids=None)` | Intersect with **normal-role** bindings only: fetch `ChatroomAgentRepository.list` (now role-aware) instead of the ids-only helper, filter `role == NORMAL`. An observer id smuggled into the mention payload must be dropped silently (R28.04) — this is both a wake-gate and an existence-oracle fix. |
| `evaluate_silence` / `evaluate_presence_change` | None (observers may use `silence_minutes`). |
| `TurnEngine._dispatch_agent_reply_wakeups` (~L1063) | None. As-source: an observer turn produces no room message, so it never reaches this dispatch. As-target: it uses `list_bound_agent_ids` (role-blind), so normal agents' replies re-wake observers — keep. |

The mention *candidate list* served to clients is the bound-agents endpoint
(A.8.6), which already filters observers for non-creators (R28.10), so the
composer cannot even offer one.

## A.6 The observer turn variant

**File:** `backend/contexts/agents/application/runtime/turn_engine.py`.

Do not fork a parallel engine. `run_turn` (keyword-only signature, verified:
`*, agent_id, chatroom_id, trigger, parent_agent_id=None, input_text=None,
request_id=None, trigger_message_id=None`) → `_run_locked` stays the single
entry; branch the **output side** only. The lock/coalescing wrapper
(`turn_lock`, `_mark_trigger_queued` SETNX + GETDEL drain,
`_QUEUED_TRIGGER_TTL_S=3600`) is untouched (R28.12).

1. **Role lookup.** `_run_locked` re-validates the binding at ~L767 via a
   **direct infrastructure import** (verified: `from
   contexts.conversation.infrastructure.repositories import
   ChatroomAgentRepository` at L44 — this direct-import precedent is how
   the agents context consumes conversation internals; follow it). Replace
   `is_registered(...)` with `role_of(...)`: `None` → the existing
   `not_bound` skip path (~L770-773); `OBSERVER` → `is_observer = True`.
   One round-trip, no new query.
2. **Suppress room emissions when `is_observer`.** Two verified mechanisms
   to combine:
   - `_stream_with_tools` already gates every `agent.token` emit on
     `room is not None` (~L1393, L1468) — the headless `run_input_turn`
     passes `room=None` today. Observer turns pass `room=None` into
     `_stream_with_tools` and token suppression costs zero new code.
   - The remaining emits are scattered inline `Publisher(room).emit(...)`
     sites (`agent.thinking` ~L814; post-commit `message.created` +
     `agent.finished` ~L996-1006; error variants via
     `emit_agent_finished_error` at ~L763/772/787/800/1024). Centralize:
     compute `room = None if is_observer else room_channel(chatroom_id)`
     once at the top of `_run_locked` and guard each emit on
     `room is not None` (most already implicitly assume `room`; the change
     makes a future added emit fail closed instead of open).
3. **Creator-channel emissions when `is_observer`.** Resolve the recipient
   once per turn via a new conversation-application helper
   `resolve_observation_recipient(db, chatroom_id) -> uuid.UUID | None`
   (created_by, else None for legacy NULL-creator rooms — observations are
   still persisted and readable by moderators over REST, but push events
   are skipped rather than fanned out to every moderator in v1). Emit
   `observation.started` before the provider stream and
   `observation.created` / `observation.failed` at the end, using
   `Publisher(user_channel(creator_id))` —
   `from contexts.identity.interfaces import user_channel` (the identity
   context already exports it; `admin_service.py` and the notification
   context are precedents).
   For `observation.failed`, reuse the verified error-kind vocabulary of
   `agent.finished`: `agent_gone`, `not_bound`, `key_group_scope`,
   `rate_limited`, `provider_exhausted:<reason>`,
   `provider_stream_failed`, or the exception class name from
   `_err_kind` (~L1514). Keep the same `error`-key-means-toast /
   `reason`-key-means-benign-skip contract documented in
   `agents/interfaces/channels.py`.
4. **History assembly** (`_assemble_history` → `transcript.
   load_model_history`, `DEFAULT_HISTORY_WINDOW=500`) is unchanged —
   observers read the full transcript. Context folding is the verified
   `system_parts: list[str]` joined with `"\n\n"` (~L824-909, order: base
   system → compact summaries → RAG → GraphRAG → staged-files note → A2A
   notify block → participant-label note). Insert two observer-only parts:
   - the observer framing suffix (step 6) — append right after
     `base_system`;
   - the self-memory block: fold
     `ObservationRepository.list_recent_for_agent(chatroom_id, agent_id,
     limit=OBSERVER_MEMORY_WINDOW)` (constant, start at 10) as
     `[Your previous observations]\n…`, gated on `is_observer` so normal
     agents never pay the query (R28.05).
5. **Persistence.** Instead of `MessageService.send_agent` (verified direct
   application-service call at ~L981, not via facade), call the new
   `ObservationService.record(...)` (A.7) the same way — direct import,
   matching the `MessageService` precedent. Skip
   `_dispatch_agent_reply_wakeups` and `_dispatch_agent_message_signal`
   entirely — an observation is not a message. Return a normal
   `TurnResult(status="completed", message_id=None, text=...)`; the worker's
   status→audit mapping (`wakeup.fired/skipped/failed`) and
   completed-only autostop bump then behave correctly unchanged.
6. **Prompt framing.** Verified that `_resolve_prompt` only builds the base
   text; composition happens via `system_parts` in `_run_locked` — so
   append the fixed observer suffix to `system_parts`, not inside
   `_resolve_prompt`: "You are a silent observer. Your reply is delivered
   privately to the room owner as an analysis; participants cannot see
   it." Keep it code-side, not in user-editable prompt text.
7. **Unchanged for observers:** turn lock, coalescing, key-group
   re-validation, rate limit (`ratelimit.check_raw`, key
   `rl:agent-turn:{agent}:{room}` — note it fails open by design), tool
   rounds (`MAX_TOOL_ROUNDS=8`), and the A2A `pending_notify` drain
   (~L693) / `_requeue_notifications` on failure — observers are full
   agents in every respect except output routing (R28.12).

The worker entry (`app/workers/tasks/orchestration.py::wakeup_agent(ctx,
agent_id: str, room_id: str, trigger: str = "every_n_messages",
trigger_message_id: str | None = None)` — note the arq `ctx` first arg and
`room_id` param name) needs no signature change — role is resolved inside
the turn, so jobs queued before a role flip do the right thing at execution
time.

## A.7 Conversation-context service

**New file:** `backend/contexts/conversation/application/observation_service.py`
— owns creation, listing, release, delete; constructor-injected db session,
mirroring `message_service.py`. Methods:

```
record(*, chatroom_id, agent_id, content_md, trigger, trigger_message_id, metadata)
list(*, chatroom_id, before, limit)
release(*, chatroom_id, observation_id, principal, actor_ip, target, content_override, wake, request_id)
delete(*, chatroom_id, observation_id, principal, actor_ip, request_id)
```

Consumption pattern, verified against how the codebase actually wires:

- The **turn engine** (agents context) imports `ObservationService`
  directly, exactly as it already imports `MessageService` (turn_engine
  L43) and `ChatroomAgentRepository` (L44).
- The **route handlers** construct `ObservationService` directly, matching
  the sibling conversation routes (`messages.py` constructs
  `MessageService`; `chatrooms.py` constructs `ChatroomService`).
- `ConversationFacade` (verified: a read-only surface whose only writer is
  `create_message`, used by transcript compaction) gains nothing in v1 —
  no other context needs observation reads.

New domain errors in `contexts/conversation/domain/errors.py` +
registrations in `contexts/conversation/interfaces/error_mapping.py::_MAP`
(the verified RFC 7807 mechanism — slug → `problem_type()` URL):

| Error | Mapping |
|---|---|
| `ObservationNotFound` | `("conversation/observation-not-found", 404, …)` |
| `NotRoomCreator` | `("conversation/not-room-creator", 403, …)` |
| `ObservationAlreadyReleased` | `("conversation/observation-already-released", 409, …)` |

## A.8 API endpoints

**Files:** `backend/app/api/v1/chatrooms.py` (bindings, disclosure) and a
new `backend/app/api/v1/observations.py`. Wiring (verified):
`app/api/v1/__init__.py::_build_registry` — add a lazy import and append
`RouterEntry(observation_routes.observation_router)` in the Conversation
section; no other bootstrap file. Reusable helpers already in
`chatrooms.py`: `_project_id_for_chatroom`, `_require_project_cap`,
`_parse_if_match`, `_raise_forbidden`.

1. `GET /chatrooms/{id}/observations` — query `before` (uuid), `limit`
   (default 50, max 100). `resolve_room_access` + `ensure_can_read` +
   `is_room_creator` else `NotRoomCreator`. Response items: full
   observation minus `deleted_at`.
2. `POST /chatrooms/{id}/observations/{obs_id}/release` — body:

   ```json
   { "target": "room" }
   { "target": "agents", "agent_ids": ["…"], "wake": false }
   ```

   plus optional `"content_override"` — `Field(min_length=1,
   max_length=100_000)`, matching the verified `_MAX_CONTENT_MD` in
   `messages.py`. Responses: 200 with the updated observation; 404
   room-scoped miss; 409 `observation-already-released`; 422 empty
   `agent_ids` or any target that is not a **normal-role binding of this
   room** (releasing to another observer or an unbound agent is rejected —
   no cross-room exfiltration channel).
3. `DELETE /chatrooms/{id}/observations/{obs_id}` — 204; released
   observations may still be deleted (release already copied content out).
4. `POST /chatrooms/{id}/agents` — verified current shape: body `AgentRef
   {agent_id}`, 204, `_require_project_cap(RESOURCE_CREATE_EDIT)` plus a
   cross-project 422 guard via `AgentsFacade.get_agent`. Body gains
   optional `role` (default `normal`); **`role=observer` additionally
   requires `is_room_creator`** (403 otherwise) — a non-creator moderator
   must not plant observers whose output only the creator reads.
5. `PATCH /chatrooms/{id}/agents/{agent_id}` — new; `{role}`;
   creator-only. (Verified: unbind already exists as
   `DELETE /{chatroom_id}/agents/{agent_id}`.) A normal→observer flip
   takes effect on the agent's next turn (queued jobs re-resolve role,
   A.6); an observer→normal flip does NOT retroactively publish past
   observations.
6. `GET /chatrooms/{id}/agents` — verified current response: paginated
   `list[AgentRef]` of `{agent_id}` objects (not bare ids). Additive
   change: creator/admin receive `{agent_id, role}` for all bindings;
   non-creators receive normal bindings only, `role` omitted — existing
   consumers keep working (R28.10).
7. `PATCH /chatrooms/{id}` — verified mechanics: `If-Match` version header
   (`_parse_if_match`) + single `_require_project_cap(RESOURCE_CREATE_EDIT)`
   gate + `ChatroomFlagsPatch(**body.model_dump(exclude_unset=True))` →
   `ChatroomService.patch` → generic `ChatroomRepository.update(values=…)`.
   Add `disclose_observers` to `ChatroomPatchIn` and `ChatroomFlagsPatch`;
   because the existing gate is capability-wide (any project/org owner),
   add a **per-field check in the handler**: if `disclose_observers` is in
   the patch and `is_room_creator` is false → 403. Other fields keep their
   current permission.
8. `GET /chatrooms/{id}` and room-list DTOs — add `created_by_user_id`,
   `disclose_observers`, and computed `observers_present` =
   `disclose_observers AND EXISTS(observer binding)`. When
   `disclose_observers=false`, `observers_present` is `false` for everyone
   — absence of signal, not a lie about bindings (the field means "you are
   notified of observers"; document it as such in the OpenAPI description).

## A.9 Release semantics

Inside `ObservationService.release`, after `is_room_creator` +
`mark_released` (the CAS — everything below runs only for the winner):

**Target `room` (R28.06):**

1. Compose content = `content_override or observation.content_md`.
2. Insert by mirroring the verified compaction insert
   (`MessagesTranscriptStore.replace_range_with_summary` →
   `create_message(sender_type=SenderType.SYSTEM, sender_id=None, …)`):
   same-context, so call `MessageRepository.create` directly with
   `metadata = {"type": "released_observation", "observation_id": …,
   "released_by_user_id": …, "observer_agent_id": <only when
   disclose_observers is true at release time>}`. Client-supplied metadata
   is rejected at the HTTP boundary (verified, `messages.py` ~L80), so
   this tag is unforgeable.
3. `release_target = {"kind":"room","message_id": …}`; commit the CAS +
   message in one transaction.
4. Post-commit: emit `message.created` on the room channel, then run the
   **standard** message wake-up dispatch — reuse the verified pattern from
   `messages.py::_dispatch_message_wakeups` (`evaluate_message_wakeups(…,
   sender_is_user=True)` then `enqueue("wakeup_agent", str(agent_id),
   str(chatroom_id), "every_n_messages", str(message_id))`). A released
   room message is an ordinary message from the wake-up system's point of
   view; no new trigger literal on this path. Observers count and wake
   here too — by design, they observe releases.

**Target `agents` (R28.07):**

1. Do NOT go through `A2AService.notify` / `OrchestrationFacade.a2a_notify`
   — verified: its scope evaluator denies when **either** caller's or
   callee's `a2a_enabled` is false, and agent-level A2A policy must not
   veto a creator-authorized release. Instead push directly to the queue,
   for which two in-tree precedents exist (`approval_service.py` ~L169 and
   the turn engine's drain): `from contexts.orchestration.infrastructure
   import pending_notify` then, per validated target:

   ```python
   await pending_notify.push(target_agent_id, {
       "kind": "released_observation",
       "chatroom_id": str(chatroom_id),
       "content": effective_content,
   })
   ```

   Rendering: `_pending_context_and_tools` (turn_engine ~L704-722) renders
   unknown kinds as a raw JSON line under `[Incoming notifications]` —
   functional but ugly for long analyses. Add one renderer branch:
   `kind == "released_observation"` → `- The room owner shared an
   analysis with you:\n<content>`. Queue characteristics (verified):
   `_MAX_PENDING=50`, TTL 86400s, drain is LRANGE+DELETE with requeue on
   turn failure — an unwoken agent that never wakes within 24h loses the
   note; surface this in the release UI copy ("agents read it the next
   time they act").
2. If `wake=true`: `enqueue("wakeup_agent", str(target), str(chatroom_id),
   "release", None)` per target. Verified consequences of the new literal:
   no validation to update (bare string), `reply_meta["trigger"]` and the
   `wakeup.*` audit metadata pick it up automatically; but the **autostop
   bypass** at `orchestration.py` ~L98 currently reads `trigger !=
   "mention"` — extend to `trigger not in ("mention", "release")` so an
   autostopped agent still honors an explicit creator wake, mirroring
   mention semantics. Leave the `agent_gone`/`not_bound` mention-only
   error emits as-is (a silent skip is acceptable for release wakes). The
   per-(agent,room) turn lock + coalescing bound the blast radius to one
   turn per target.
3. `release_target = {"kind":"agents","agent_ids":[…],"woken": wake}`.

Both paths end with `observation.released` on the creator channel and the
audit event (A.10). DB work commits in one transaction; arq enqueues and WS
emits are post-commit only — copy the ordering discipline from
`messages.py::send_message` (~L212 commit, ~L227-238 dispatch).

## A.10 Audit events

Via `shared_kernel.audit.emit` — verified: `emit` **auto-redacts** metadata
(callers pass the raw dict) and runs in the caller's open transaction; call
`flush_tail_events(session)` post-commit so the admin audit tail
(`ws:audit:tail`) fires, matching existing emitters.

| Action | resource_type | Metadata (allowlist — nothing else) |
|---|---|---|
| `observation.released` | `observation` | `chatroom_id`, `observation_id`, `target.kind`, `agent_ids`/`message_id`, `woken`, `content_overridden: bool` |
| `chatroom.observer_bound` | `chatroom` | `agent_id`, `role` |
| `chatroom.observer_role_changed` | `chatroom` | `agent_id`, `old_role`, `new_role` |
| `chatroom.disclosure_changed` | `chatroom` | `old`, `new` |

Unbinding keeps whatever action `ChatroomService.remove_agent` already
emits — no rename. R28.11: never include `content_md` or
`content_override` in audit metadata.

## A.11 Leak-proof checklist (hard gate)

Structural guarantees to assert with tests — each row is one or more unit /
integration tests:

| Path | Assertion |
|---|---|
| Room WS channel | A full observer turn (thinking→stream→persist) publishes **zero** events on `ws:room:{id}` (spy `Publisher`; assert `_stream_with_tools` received `room=None`). |
| `GET /messages` + `?since=` delta | Message count unchanged after an observer turn. |
| Full-text search (`content_tsv`) | Observation text is not findable via message search. |
| Model history of other agents | A normal agent's assembled history after an observer turn contains no observation text. |
| Observer's own history | Contains its own prior observations (system block), not other observers'. |
| Compaction | The compaction input set never includes observation rows (trivially true — different table — but pin it so a future "unify" refactor trips the test). |
| Bound agents endpoint | Non-creator response contains no observer ids; mention intersection (`filter_mentioned_bound_agents`) drops observer ids. |
| Observations REST | Non-creator member → 403; creator → 200; NULL-creator room: moderator → 200, member → 403; admin → 200; **guest → 403 even when `allow_guest_links` grants room read** (the A.4 caution). |
| Release to agents | Content reaches only the targeted agents' next-turn `[Incoming notifications]` block; no room message, no room event; A2A scope evaluator is NOT consulted (works with `a2a_enabled=false` on both sides). |
| Release to room, undisclosed | Serialized message + WS payload contain no `observer_agent_id`. |
| Double release | Exactly one 200, second call 409, exactly one system message. |
| Audit | No observation content in any audit row emitted by the above. |

## A.12 Test plan

- **Unit** (`backend/tests/unit/`): observation repo CRUD + keyset
  pagination + `mark_released` CAS; `is_room_creator` truth table
  (creator / NULL+moderator / NULL+member / admin / guest-with-roles /
  pure-guest); trigger gating (mention filter drops observers, every_n
  wakes them); turn-engine observer branch using the harness style of
  `tests/unit/test_a2a_turn_dispatch.py` (publisher spy, `pending_notify`
  monkeypatching, facade mocks); release service both targets + validation
  matrix + autostop-bypass literal; API handler AuthZ/422/409/If-Match
  tests; the A.11 rows that don't need PG.
- **Integration**: migration 0041 up/down + backfill earliest-actor
  assertion; ENUM/ORM round-trip on `chatroom_agents.role` (regression
  class: asyncpg Text-vs-ENUM 500s); observation pagination against real
  PG.
- **Wiring** (`tests/wiring/`, style of `test_a2a_call_round_trip`):
  scenario 1 — bind observer, post user message, observer turn produces an
  observation, creator lists it, releases privately with `wake=true`, the
  target's reply quotes the analysis; scenario 2 — release to room, all
  members see the system message, non-creator never saw the observation.
  Both scenarios assert the room-channel spy stayed silent for observer
  activity.

## A.13 Deliverables and exit criteria

Deliverables: migration 0041; domain/table/repo changes (incl.
`ChatroomRepository.create` actor plumbing); access rule; trigger gating;
turn-engine observer branch + `pending_notify` renderer branch + autostop
bypass literal; `ObservationService` + domain errors + error-map entries;
`observations.py` route file + chatrooms route changes + registry entry;
audit events; the full test suite above; OpenAPI regenerated.

Exit criteria (all required before Phase B starts):

1. `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` clean.
2. Every A.11 checklist row has at least one passing test.
3. `alembic upgrade head` + `downgrade -1` round-trips on a seeded DB;
   backfill assertion test green.
4. OpenAPI diff reviewed: no existing response shape narrowed except the
   documented bound-agents filtering (R28.10).
5. Manual smoke on the dev stack: observer produces observations while a
   second browser session (non-creator member) sees no trace in UI,
   network tab, or WS frames.
