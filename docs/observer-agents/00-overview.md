# Observer Agents — Overview

A post-v1 feature that adds a **hidden observer role** for agents bound to a
chatroom. An observer agent wakes on room activity like any bound agent and
reads the full transcript, but its output is **never a room message**: it is
persisted out-of-band as an *observation* and delivered privately to the room
**creator**. The creator can then *release* an observation — either publish it
into the room as a visible message, or inject it privately into the context of
selected agents without any human-visible trace.

The plan is sequenced as two phases (A backend, B frontend), each in its own
file with numbered sub-steps, exact file targets, deliverables, and exit
criteria — directly followable by an engineer. It layers on top of the
completed A–N build (`docs/implement/`) and the Agent Tools restructure
(`docs/agent-tools/`).

## 0.1 Problem statement

Room delivery today is pure broadcast with no per-recipient visibility
anywhere in the stack:

- All room events fan out on a single Redis channel `ws:room:{id}`
  (`shared_kernel\realtime\connection.py::_pubsub_fanin`) with no
  per-connection filtering.
- `GET /chatrooms/{id}/messages` returns every non-deleted row to anyone who
  passes `ensure_can_read`; the client replays deltas via `?since=` and
  renders whatever comes back (`useChatroomSocket.ts::replayDelta`).
- Every bound agent's LLM context is the full transcript
  (`TurnEngine._assemble_history` → `transcript.load_model_history`).
- `chatrooms` has no creator column; the closest concept is
  `RoomAccess.is_moderator` (project/org owner).

Use cases that need a privileged, non-participating analyst — meeting
minute-taking, conversation quality monitoring, compliance review, negotiation
support — cannot be built on the room message path without per-viewer
filtering in at least three independent read paths, each of which would be
fail-open (one missed filter = privacy leak).

## 0.2 Architecture decision: out-of-band observations (fail-closed)

**Rejected — Option A: visibility column on `messages`.** Would require
per-viewer filtering in the WS fan-out (shared channel, not supported), the
REST list endpoint, the delta cursor (`lastSeenMessageId` assumes one linear
stream per room), and the model-history assembly — plus every future read
path (search, export, retention) forever. Fail-open by construction.

**Chosen — Option B: observations never enter `messages`.** Observer output
is written to a new table `agent_observations` and delivered on the existing
per-user channel `ws:user:{creator_id}` (`contexts\identity\infrastructure\
channels.py::user_channel`, WS endpoint `app\api\ws\user.py`). Every existing
read path — REST list, WS room fan-out, model history, full-text search
(`content_tsv`), compaction, retention — is untouched and *structurally
incapable* of leaking an observation. Release is an explicit, audited act
that copies content into a normal room message or an A2A notification.

Precedents already in production: headless turns
(`TurnEngine.run_input_turn`), the A2A `pending_notify` queue
(`contexts\orchestration\infrastructure\pending_notify.py`), and the
workflow `agent_invocation` executor.

## 0.3 Locked decisions (from the design Q&A, 2026-07-02)

1. **Creator identity.** New nullable column `chatrooms.created_by_user_id`,
   backfilled from `audit_logs` rows with `action='chatroom.created'`.
   Rooms whose creator cannot be recovered fall back to **moderator
   semantics** (project/org owner acts as creator). `principal.is_admin`
   bypasses, consistent with the rest of the platform.
2. **Disclosure is a room-level setting.** `chatrooms.disclose_observers`
   (boolean, **default true** — transparent by default, stealth is opt-in).
   When true, non-creators see a neutral indicator ("observers are enabled
   in this room") — never which agent, never any output. When false,
   nothing is shown. Only the creator can change the flag.
3. **Both release semantics.**
   - *Publish to room*: creates a normal `sender_type=system` message with
     `metadata.type='released_observation'`; broadcast and agent wake-ups
     then follow the standard path.
   - *Private inject*: pushes the observation into selected agents'
     `pending_notify` queues via a direct `pending_notify.push` (verified:
     `A2AService.notify` is gated on **both** parties' `a2a_enabled` flags,
     and agent-level A2A policy must not veto a creator-authorized release;
     direct-push precedents exist in `approval_service.py` and the turn
     engine). The room never sees it. Default does **not** wake the target
     (avoids reply storms when injecting several agents); the release UI
     offers an immediate-wake option.
4. **Released room messages are system-presented.** They are attributed to
   "analysis released by the room creator", not to the observer agent —
   otherwise a release would instantly deanonymize an undisclosed observer.
   The observer's name is included in metadata only when
   `disclose_observers=true` at release time.
5. **Creator may edit before release.** The release call accepts a content
   override; the stored observation keeps the original text.
6. **No token streaming in v1.** Observer turns emit no room events at all
   (`agent.thinking`/`agent.token`/`agent.finished` included). The creator
   gets `observation.started` / `observation.created` / `observation.failed`
   on the user channel. Streaming to the creator panel is a possible v2.
7. **Observers are invisible in shared surfaces.** Excluded from the
   @mention candidate set, the shared agent sidebar, and the bound-agent
   list returned to non-creators. The creator manages them in a dedicated
   panel, not in the shared sidebar.
8. **Observer memory.** An observer's own prior observations (bounded
   window) are folded into its next turn's context so successive analyses
   are cumulative, not amnesiac.
9. **Scope generality.** `agent_observations` keys on `chatroom_id` in v1
   but the release/read API is namespaced so the table can later grow a
   polymorphic scope (workflow runs, cross-room digests) without breaking
   consumers.

## 0.4 The data model (authoritative)

```
-- chatrooms: two new columns
ALTER TABLE chatrooms
  ADD COLUMN created_by_user_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN disclose_observers boolean NOT NULL DEFAULT true;

-- binding role
CREATE TYPE chatroom_agent_role AS ENUM ('normal', 'observer');
ALTER TABLE chatroom_agents
  ADD COLUMN role chatroom_agent_role NOT NULL DEFAULT 'normal';

-- observations (never joined with messages)
CREATE TABLE agent_observations (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chatroom_id          uuid NOT NULL REFERENCES chatrooms(id) ON DELETE CASCADE,
  agent_id             uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  content_md           text NOT NULL,
  metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
  trigger              text NOT NULL,                -- wakeup literal: every_n_messages | silence_minutes
  trigger_message_id   uuid NULL,                    -- no FK; anchor only
  released_at          timestamptz NULL,
  release_target       jsonb NULL,                   -- {"kind":"room","message_id":...}
                                                     -- | {"kind":"agents","agent_ids":[...],"woken":bool}
  released_by_user_id  uuid NULL REFERENCES users(id) ON DELETE SET NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  deleted_at           timestamptz NULL
);
CREATE INDEX ix_agent_observations_room
  ON agent_observations (chatroom_id, created_at DESC) WHERE deleted_at IS NULL;
```

Per the ORM/enum rule (`tables.py` comment on `message_sender_type`), the
SQLAlchemy column for `role` must be declared as the matching PG ENUM
(`create_type=False`), never `sa.Text` — a mismatch 500s under asyncpg.

## 0.5 Event and API surface (summary)

New events on `ws:user:{creator_id}` (existing channel, existing WS endpoint,
existing frontend consumption precedent in
`slices/notifications/composables/useNotificationsSocket.ts`):

| Event | Payload | When |
|---|---|---|
| `observation.started` | `{chatroom_id, agent_id}` | observer turn begins |
| `observation.created` | `{chatroom_id, agent_id, observation_id, created_at}` | persisted |
| `observation.failed` | `{chatroom_id, agent_id, kind}` | turn error (kind mirrors `agent.finished` error kinds) |
| `observation.released` | `{chatroom_id, observation_id, target}` | release committed |

New/changed REST (all under the existing chatrooms router; bodies fetched via
REST, events are notify-only — same pattern as `message.created`):

| Method + path | AuthZ | Purpose |
|---|---|---|
| `GET /chatrooms/{id}/observations?before=&limit=` | creator | cursor-paginated list |
| `POST /chatrooms/{id}/observations/{obs_id}/release` | creator | body `{target: "room"} \| {target: "agents", agent_ids: [...], wake: bool}`, optional `content_override` |
| `DELETE /chatrooms/{id}/observations/{obs_id}` | creator | soft-delete |
| `POST /chatrooms/{id}/agents` | moderator; `role=observer` requires creator | body gains optional `role` (default `normal`) |
| `PATCH /chatrooms/{id}/agents/{agent_id}` | creator | `{role}` — flip normal/observer |
| `GET /chatrooms/{id}/agents` | member | non-creators receive **only normal bindings**; creator receives all with `role` |
| `PATCH /chatrooms/{id}` | creator for this field | gains `disclose_observers` (rides the existing `If-Match` versioned patch) |
| `GET /chatrooms/{id}` | member | DTO gains `created_by_user_id`, `disclose_observers`, computed `observers_present` (true only when disclosed AND at least one observer bound) |

"Creator" everywhere means: `created_by_user_id` matches; or the column is
NULL and the principal `is_moderator`; or `principal.is_admin`.

## 0.6 Phase map

| Phase | Title | Backend | Frontend | Migrations |
|---|---|---|---|---|
| **A** | Observer backend | migration + backfill, domain/repo, access rule, trigger gating, observer turn variant, observations API, release paths, audit, leak-proof test suite | — | `0041_observer_agents` |
| **B** | Observer frontend | — | api-client regen, types, `useObservations`, creator observation panel, release dialog, settings (role + disclosure), disclosure indicator, i18n, tests | — |

```
A ──► B
```

B consumes A's OpenAPI surface; no parallelism between phases, but B.2–B.6
are parallelizable once A is merged.

## 0.7 Conventions (inherited)

- **Layer boundaries.** `app/api/v1/` → `contexts/*/interfaces/facade.py` →
  `application/` → `infrastructure/`. The turn variant lives in the agents
  context; observation storage in the conversation context; cross-context
  calls only via facades. Frontend: `app/ → slices/ → shared/`.
- **AuthZ tap.** Every new endpoint resolves room access first
  (`resolve_room_access`) and then the creator rule (§0.5). Fail-closed:
  ambiguity denies.
- **Audit tap.** `observation.released`, `chatroom.observer_bound`,
  `chatroom.observer_role_changed`, `chatroom.disclosure_changed` via
  `shared_kernel.audit.emit`. Observation *content* is never written to
  audit metadata (it may contain user conversation excerpts).
- **RFC 7807** problems under `https://smap.local/problems/…`.
- **Migration policy.** N-1 compatible: `0041` only adds columns/table with
  defaults; old code ignores them.
- **Secrets/logging.** Observation content is user data: never logged, never
  in audit metadata, excluded from error reports.
- **No emojis. i18n via `$t()`. Comments only where WHY is non-obvious.**

## 0.8 SRS addendum

**Done (2026-07-02):** `REQUIREMENTS.md` §28 Observer Agents now carries the
requirement IDs used throughout this plan (`R28.01`–`R28.14`, mirrored in
`A-backend.md` §A.0), and `docs/traceability.csv` has the 14 new rows. These
phase files are derived from the SRS; if a requirement changes, update §28
first.

## 0.9 Acceptance levels

Same ladder as the parent plan: **CODE** (impl + unit tests), **CONTRACT**
(OpenAPI regenerated and frozen, `pnpm run gen:api` clean), **E2E**
(integration/wiring green). Phase A's leak-proof suite (§A.11) is a hard gate
for phase B — the frontend must never be the enforcement point.

## 0.10 Risk register

| Risk | Mitigation |
|---|---|
| Observation leaks through an existing read path | Structural: observations are not `messages` rows; §A.11 leak checklist tests every path (REST list, delta, search, compaction, model history, room WS). |
| Release-to-room deanonymizes an undisclosed observer | System-presented release message; observer name in metadata only when disclosed (locked decision 4). |
| Observer turn accidentally emits room events | Turn variant asserts a null room publisher; unit test spies the room channel and requires zero emissions (§A.6, §A.11). |
| `created_by` backfill misses legacy rooms | NULL → moderator-fallback semantics; no room becomes orphaned (locked decision 1). |
| Private inject triggers reply storms | `wake=false` default; wake dispatches at most one turn per target via the existing per-(agent,room) turn lock + coalescing. |
| A2A policy blocks a legitimate release | Private inject bypasses the A2A scope evaluator deliberately (direct `pending_notify.push`); release is creator-authorized, not agent-to-agent. |
| Unwoken private release silently expires | `pending_notify` TTL is 24h / 50-note cap (verified); release UI copy states it, `wake=true` avoids it entirely. |
| Mention path wakes an observer and reveals it | `filter_mentioned_bound_agents` intersects with **normal-role** bindings only (§A.5). |
| Enum/ORM mismatch 500s under asyncpg | `role` declared as PG ENUM `create_type=False` in `tables.py`, mirroring `message_sender_type`. |
| Observer analyses go stale/unbounded | Soft-delete endpoint; own-memory window bounded (§A.6); retention hook listed as v2. |

## 0.11 How to use this plan

1. Add SRS §28, land **A** (single migration `0041`), run the §A.11 leak
   suite plus the full backend gate (`pytest -q`, `ruff`, `mypy`).
2. Regenerate the API client, land **B**, run the frontend gate
   (`pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`).
3. E2E: the two wiring scenarios in §A.12/§B.10 (observe → private release,
   observe → room release) must pass before the feature ships.
