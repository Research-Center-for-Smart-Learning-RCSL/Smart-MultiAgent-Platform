---
type: feature
status: implemented
created: 2026-07-10
requirements: [R11.14, R11.15, R11.24]
---

# GraphRAG Phase 3β — Knowledge Map graph read + live build status

## 1. Summary

Adds the two backend capabilities Phase 4β's frontend needs that Phase 3 (Knowledge Map
CRUD/upload/build) shipped without: a bounded graph-read endpoint
(`GET /api/knowmap-configs/{config_id}/graph`) and a live WebSocket build-status channel
(`GET /ws/knowmap/{config_id}`), both mirroring the existing Concept Map (`graphrag`)
equivalents. It also fixes a latent cross-wire: the shared `GraphRagBuilder`/
`ReconciliationLoop` engine hardcodes `graphrag_channel()` for every build-state publish,
so today's Knowledge Map builds already call `publish_build_state` but broadcast onto
`ws:graphrag:{knowmap_config_id}` — a channel no client can validly subscribe to (the
`graphrag` WS route 404s on a knowmap config id). This was discovered while starting
`/build` on the Phase 4β frontend spec
(`docs/tasks/2026-07-07-graphrag-phase4b-knowledge-map-ui/spec.md`), which assumed both
endpoints existed; they don't, and Phase 4β is paused pending this task.

## 2. Goals and Non-goals

**Goals**
- G1 — `GET /api/knowmap-configs/{config_id}/graph`: bounded node/edge read of a Knowledge
  Map's Neo4j subgraph, response-shape-compatible with the Concept Map graph endpoint
  (R11.14, R11.24).
- G2 — `GET /ws/knowmap/{config_id}`: a WebSocket route streaming build-state transitions
  for a Knowledge Map config, mirroring `/ws/graphrag/{config_id}` (R11.24).
- G3 — Correct the channel the shared build engine publishes to for Knowledge Map builds
  (today: `graphrag_channel`, always) by giving `GraphRagBuilder` and `ReconciliationLoop`
  an injectable channel selector, defaulting to today's `graphrag_channel` for existing
  Concept Map call sites (bit-for-bit unchanged there) and passing a new `knowmap_channel`
  at the Knowledge Map call sites.
- G4 — No fork of the shared engine (R11.15): the injection point is a single optional
  constructor parameter, not a duplicated builder/reconciler.

**Non-goals**
- No frontend changes — this is the backend half Phase 4β's frontend consumes; Phase 4β
  resumes separately once this ships.
- No change to the Neo4j data model, Cypher, or the `apply_triples`/`fetch_graph`
  driver methods — they are already domain-agnostic (scoped by opaque config UUID) and
  need no changes (confirmed in §4).
- No new Postgres migration — the graph lives entirely in Neo4j; `knowmap_configs`
  already carries `last_build_state`/`last_build_at`/`last_build_error` (migration
  `0048_knowmap`).
- No poll-based `/status` endpoint for Knowledge Map — `GET /api/knowmap-configs/{id}`
  already inlines the three status fields; a separate `/status` route (as `graphrag.py`
  has) would be pure duplication and is not requested by Phase 4β.
- No change to `graphrag_events.publish_build_state`'s existing default behavior for
  Concept Map callers — the new `channel` override is optional and unused there.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does the shared builder learn which channel to publish build-state to? | Add an optional `channel: str \| None = None` kwarg to `publish_build_state` (default preserved: `graphrag_channel(config_id)`), and an optional `channel_fn: Callable[[uuid.UUID], str] \| None = None` constructor param on `GraphRagBuilder` and `ReconciliationLoop`, threaded into every internal `publish_build_state(...)` call as `channel=self._channel_fn(cfg.id) if self._channel_fn else None`. | The channel is resolved inside `publish_build_state` itself today (`graphrag_events.py:44`), and the builder/reconciler never receive one — the two viable options were "override at the call site" (chosen: minimal diff, backward compatible) vs. a bigger event-publisher Protocol seam (over-engineered for one string). |
| Q-2 | Should the Knowledge Map graph-read logic reuse `GraphRagGraphService` (parameterized repo) or duplicate it? | Duplicate as a new `KnowmapGraphService` in its own module, reading `KnowmapConfigRepository`, with its own response models in `knowmap.py`. | Zero risk of touching the Concept Map read path; the service is ~70 lines and entirely mechanical (repo swap + identical `Neo4jAsyncDriver.fetch_graph` call) — duplication cost is low and isolation is worth it over a generalized service both domains depend on. |
| Q-3 | Fix the existing cross-wire (knowmap builds publishing to `graphrag_channel`) in this task, or leave Phase 3's shipped behavior untouched? | Fix it — it is inseparable from Q-1's channel-injection change; recorded here as a bugfix note rather than a separate dossier. | The cross-wire is currently invisible (nothing subscribes with a knowmap id) but is the reason a `knowmap_channel` WS route wouldn't receive anything without this fix — shipping G2 without G3 would add a route that never fires. |

## 4. Current State

- **Concept Map graph endpoint (exemplar).** `backend/app/api/v1/graphrag.py:373-413`
  (`read_graph`, on `config_router = APIRouter(prefix="/api/graphrag", ...)`,
  `graphrag.py:289`): loads `cfg` via `KnowledgeFacade(db).get_graphrag_config(config_id)`
  (`:387`), 404s via `GraphRagConfigNotFound` if missing (`:389-391`), asserts project
  membership via the local `_assert_project_membership` helper (`:292-309`, `:392-396`),
  then `facade.get_graphrag_graph(config_id, limit=limit)` (`:397`) with
  `limit: int = Query(DEFAULT_GRAPH_LIMIT, ge=1, le=MAX_GRAPH_LIMIT)` (`:376`). Response is
  `GraphOut{config_id, nodes: [GraphNodeOut{id,degree,build_id,type}], edges:
  [GraphEdgeOut{source,relation,target,confidence}], truncated}` (`graphrag.py:162-183`).
- **Concept Map graph service.** `contexts/knowledge/application/graphrag_graph_service.py`
  — `GraphRagGraphService.__init__(db)` builds `GraphRagConfigRepository(db)` (`:61-63`);
  `get_graph(config_id, limit)` loads the config for existence + `project_id` (`:71-73`),
  clamps `limit` to `[1, MAX_GRAPH_LIMIT]` (`:75`), opens a fresh `Neo4jAsyncDriver` from
  `settings.neo4j` (`:80-83`), calls `driver.fetch_graph(config_id=config_id,
  limit=bounded)` (`:85`), closes the driver in a `finally` (`:86-87`), then assembles a
  self-consistent `GraphView` — any edge endpoint outside the degree-capped node window is
  synthesized as a zero-degree node (`:89-121`). `DEFAULT_GRAPH_LIMIT = 500`,
  `MAX_GRAPH_LIMIT = 2000` (`:29-30`).
- **`Neo4jAsyncDriver.fetch_graph`** —
  `contexts/knowledge/infrastructure/neo4j_driver.py:336-378` — Cypher scopes every clause
  by `{graphrag_config_id: $cid}` where `$cid` is the caller-supplied config UUID (any
  domain's UUID works; the label name is a legacy generic property, not a domain
  discriminator). Node query orders by `degree DESC, name ASC LIMIT $node_limit`; edge
  query orders by `confidence DESC, subject ASC LIMIT $edge_limit`; `truncated` is true if
  either cap was hit. **Domain-agnostic already — no changes needed for Knowledge Map.**
- **Knowledge Map already writes real graph data into this same Neo4j space.**
  `backend/app/workers/tasks/knowmap.py:228-293` (`knowmap_build`) constructs a
  `GraphRagBuilder` (`:263-273`) — the identical 2PC engine used by Concept Map — with
  `configs=KnowmapConfigRepository(db)` (`:257`) and
  `vector_store=GraphRagVectorStore(qclient, prefix="knowmap")` (`:252-254`), then
  `builder.run(config_id=cfg_id, ...)` (`:275`) writes triples into Neo4j via the same
  `apply_triples` Cypher, `Entity`/`REL` labels, scoped by `knowmap_configs.id` instead of
  `graphrag_configs.id`. `KnowmapConfigRepository`
  (`contexts/knowledge/infrastructure/knowmap_repositories.py:84-89`) implements
  `GraphRagConfigRepositoryPort` explicitly to satisfy the shared engine (docstring
  `:85-86`, R11.15). Delete-side teardown confirms the same subgraph exists:
  `KnowmapConfigService.cascade_external_stores`
  (`contexts/knowledge/application/knowmap_config_service.py:308-353`) calls
  `Neo4jAsyncDriver.delete_all(config_id=config_id)` (`:338`) exactly as the Concept Map
  service does. **So a Knowledge Map graph is already fully materialized and readable
  today — this task only adds a read path, not new build-pipeline work.**
- **`KnowmapConfigRepository.get`/`.require`.**
  `contexts/knowledge/infrastructure/knowmap_repositories.py:127-138` —
  `get(config_id, *, include_deleted=False) -> KnowmapConfig | None` (`:127-132`);
  `require(config_id) -> KnowmapConfig` raises `KnowmapConfigNotFound` if missing
  (`:134-138`, error class already defined at `contexts/knowledge/domain/errors.py:140-141`,
  code `knowledge/knowmap-config-not-found`).
- **`knowmap.py`'s existing `_assert_project_membership`.**
  `backend/app/api/v1/knowmap.py:184-195` — structurally identical to
  `graphrag.py:292-309` (admin bypass, else `TenancyRoleResolver.roles_for(principal,
  Scope(project_id=...))`, empty roles → 403). The new graph route reuses this local
  copy directly; no shared import needed. `KnowmapConfigService(db).get(config_id)`
  (used by `read_knowmap_config`, `:270-278`) already raises `KnowmapConfigNotFound`
  internally on a missing config — the new graph route follows the same idiom rather than
  the inline `if cfg is None: raise ...` style `graphrag.py` uses.
- **Channels.** `contexts/knowledge/infrastructure/channels.py` (17 lines, full file) —
  `rag_channel(config_id) -> f"ws:rag:{config_id}"` and
  `graphrag_channel(config_id) -> f"ws:graphrag:{config_id}"`. **No `knowmap_channel`
  exists.**
- **`publish_build_state`.** `contexts/knowledge/application/graphrag_events.py:24-46` —
  resolves the channel internally via the hardcoded module-level import
  `graphrag_channel` (`:18`) and the call `graphrag_channel(config_id)` (`:44`); the
  builder never passes a channel today. Emits `Publisher(channel).emit("build.state",
  payload)` where `payload = {"state": ..., "build_id"?: ..., **extra}`; all publish
  failures are swallowed (best-effort, `:43-46`).
- **Builder publish call sites (need the `channel=` override threaded through).**
  `contexts/knowledge/application/graphrag_builder.py`: `:213` (`RUNNING`), `:332`
  (`NEO4J_COMMITTED`), `:378` (`FAILED_COMPENSATING`), `:459-465` (terminal `IDLE` with
  `triples`/`entities` extras), `:504` (`FAILED`, inside `_fail_phase1`). Constructor
  signature (`:136-157`): `db` positional, then keyword-only `neo4j`, `vector_store`,
  `extractor`, `lock_store`, `snapshot_store`, `delta_loader`, `embedder_factory`,
  `configs` — no channel-related param today.
- **Reconciler publish call sites.**
  `contexts/knowledge/application/graphrag_reconciler.py:267` (`FAILED`), `:328` (`IDLE`
  with `build_id`), `:362` (`FAILED` with `build_id`), inside `ReconciliationLoop`
  (`:85-116` for the constructor — `session_factory`, `repo_factory`, `neo4j`,
  `vector_store`, `snapshot_store`, `phase2_retry`, `sleeper`, `lock_store`,
  `sweep_consumers` — no channel param today). **Both** the Concept Map loop
  (`backend/app/workers/graphrag_reconciler.py:142-162`, `_loop()`) and the Knowledge Map
  loop (`:171-202`, `_knowmap_loop()`, `repo_factory=KnowmapConfigRepository`) construct
  `ReconciliationLoop` — the knowmap loop needs the new `channel_fn` too, or a build
  recovered by the reconciler after a crash would still cross-wire.
- **Concept Map WS route (exemplar).** `backend/app/api/ws/graphrag.py` (71 lines, full
  file): `GET /ws/graphrag/{config_id}` — `authenticate_subprotocol(ws)` (`:32`, closes
  4401 on `WsAuthError`); if not admin, loads the config via
  `KnowledgeFacade(session).get_graphrag_config(config_id)` on a raw session (`:42-45`,
  closes 4404 if missing), then `TenancyRoleResolver(session).roles_for(...)` (`:46-53`,
  closes 4403 on empty roles; any exception in this block conservatively closes 4403,
  `:54-58`); finally `connection_loop(ws=ws, principal=..., subprotocol=...,
  channels=[graphrag_channel(config_id)], token_expires_at=..., token_jti=...)`
  (`:60-67`). `backend/app/api/ws/rag_configs.py` (70 lines) is the byte-for-byte
  document-ingest analogue.
- **WS route wiring.** `backend/app/api/v1/__init__.py:142-150` imports each WS module
  directly (`from app.api.ws import (graphrag as ws_graphrag,)` etc. — no re-export
  barrel); `:235-242` registers each as `RouterEntry(ws_graphrag.router)` etc. in a
  `# WebSockets` block. `backend/app/api/ws/__init__.py` is a 1-line docstring
  (`"""WebSocket routes — five dedicated paths (no multiplexed /ws)."""`) — already stale
  today (7 route modules exist: `admin_tail.py`, `chatroom.py`, `graphrag.py`,
  `prompt_assistant.py`, `rag_configs.py`, `user.py`, `workflow_runs.py`, not five/six).
  `backend/CLAUDE.md:33` says "(6 files)", also already stale.
- **`shared_kernel` realtime primitives (unchanged, reused).**
  `shared_kernel/realtime/pubsub.py:44-` (`Publisher.emit`),
  `shared_kernel/realtime/connection.py:175-` (`connection_loop`) — both domain-agnostic,
  no changes needed.
- **Test coverage today.** No test exists for the Concept Map `/graph` endpoint or
  `GraphRagGraphService` (grepped `backend/tests/` for `read_graph`/`GraphOut`/
  `get_graphrag_graph`/`graph_service` — zero hits) — this task's endpoint tests are new
  coverage, not a mirrored test. No test exists for `app/api/ws/graphrag.py`'s route body
  either — WS-layer tests in this codebase (`backend/tests/unit/test_ws_auth_watchdog.py`)
  drive `shared_kernel.realtime.connection.connection_loop` directly via a hand-rolled
  `_FakeWS` (`:59-77`) and a `_FakeRedis` monkeypatch of `get_redis` (`:97-100`), not
  `TestClient`'s websocket context manager and not the route function itself.
  `publish_build_state` is exercised only as a monkeypatched capture spy inside
  `backend/tests/unit/test_graphrag_builder.py:927-980` (asserts the sequence of
  `(state, build_id, extra)` tuples the builder calls it with) — no test of the actual
  Redis-publish or channel-naming behavior.

## 5. Design

### Options considered

**Channel injection (Q-1) — call-site override (chosen) vs. event-publisher Protocol.**
A `channel: str | None = None` kwarg on `publish_build_state` plus a `channel_fn` on the
two callers is a 2-line signature change with a fully backward-compatible default. A
Protocol-based `BuildEventPublisher` seam (inject an object with an `.emit(config_id,
state, ...)` method instead of a bare function) would match the codebase's existing port
style more ceremonially but is unwarranted for selecting one string; deferred unless a
third channel consumer appears.

**Graph service reuse (Q-2) — duplicate `KnowmapGraphService` (chosen) vs. generalize
`GraphRagGraphService`.** Generalizing would take one repo-port parameter and reuse the
exact same ~70 lines, and `KnowmapConfigRepository` already satisfies the port
`GraphRagGraphService` would need. Duplicating costs ~70 lines of near-identical code but
guarantees zero risk to the already-shipped, now-in-production Concept Map read path
while this task is implemented and reviewed — chosen for isolation over line count.

### Decision

Thread an optional `channel`/`channel_fn` override through `publish_build_state`,
`GraphRagBuilder`, and `ReconciliationLoop`, defaulting to today's `graphrag_channel`
behavior everywhere except the two Knowledge Map call sites
(`app/workers/tasks/knowmap.py`, `app/workers/graphrag_reconciler.py`'s
`_knowmap_loop()`), which pass the new `knowmap_channel`. Add a standalone
`KnowmapGraphService` (own module) and a standalone `GET /ws/knowmap/{config_id}` route
(own module), both structurally mirroring their Concept Map counterparts but with zero
shared code beyond the already-domain-agnostic `Neo4jAsyncDriver`/`connection_loop`/
`Publisher` primitives. Consciously given up: a single generalized graph service (Q-2)
and a heavier event-publisher Protocol (Q-1) — both are legitimate alternatives revisited
if a third graph domain is ever added.

## 6. Detailed Changes

- **Backend — `contexts/knowledge/infrastructure/channels.py`** — add
  `knowmap_channel(config_id: uuid.UUID) -> str: return f"ws:knowmap:{config_id}"`,
  mirroring `graphrag_channel`. No migration.
- **Backend — `contexts/knowledge/application/graphrag_events.py`** — add `channel: str |
  None = None` to `publish_build_state`'s signature; when `None`, preserve today's
  `graphrag_channel(config_id)` resolution; when given, use it verbatim as the
  `Publisher(...)` target.
- **Backend — `contexts/knowledge/application/graphrag_builder.py`** — add `channel_fn:
  Callable[[uuid.UUID], str] | None = None` to `GraphRagBuilder.__init__` (keyword-only,
  trailing); store as `self._channel_fn`; at each of the 5 call sites (`:213, :332, :378,
  :459-465, :504`) pass `channel=self._channel_fn(cfg.id) if self._channel_fn else None`
  (or `config_id` in place of `cfg.id` where that's the local name in scope).
- **Backend — `contexts/knowledge/application/graphrag_reconciler.py`** — same
  `channel_fn` addition to `ReconciliationLoop.__init__` (`:88-116`), threaded into the 3
  `publish_build_state` call sites (`:267, :328, :362`).
- **Backend — `app/workers/tasks/knowmap.py`** — pass `channel_fn=knowmap_channel`
  (import from `contexts.knowledge.infrastructure.channels`) at the `GraphRagBuilder(...)`
  construction (`:263-273`). The existing Concept Map construction in
  `app/workers/tasks/graphrag.py:332-342` is left with the new param unset (defaults to
  `None` → unchanged `graphrag_channel` behavior).
- **Backend — `app/workers/graphrag_reconciler.py`** — pass `channel_fn=knowmap_channel`
  at the `ReconciliationLoop(...)` construction inside `_knowmap_loop()` (`:189-197`); the
  Concept Map loop in `_loop()` (`:142-162`) is left unset.
- **Backend — new `contexts/knowledge/application/knowmap_graph_service.py`** —
  `KnowmapGraphService`, structurally mirroring `graphrag_graph_service.py`: `__init__(db)`
  builds `KnowmapConfigRepository(db)`; `get_graph(*, config_id, limit=DEFAULT_GRAPH_LIMIT)`
  loads the config via `.require(config_id)` (raises `KnowmapConfigNotFound` — matches
  `knowmap.py`'s existing idiom rather than `graphrag.py`'s inline None-check), clamps
  `limit` to `[1, MAX_GRAPH_LIMIT]`, opens a `Neo4jAsyncDriver`, calls the same
  `fetch_graph(config_id=config_id, limit=bounded)`, assembles the same self-consistent
  node/edge view. Own `GraphNode`/`GraphEdge`/`GraphView` dataclasses (or re-export the
  Concept Map ones if their shape is identical and importing a `contexts.knowledge.*`
  dataclass module doesn't cross a layer boundary — dataclasses are data, not behavior;
  confirm at implementation time whether re-export or duplicate reads cleaner, this is a
  local call, not a design fork).
- **Backend — `app/api/v1/knowmap.py`** — add `GET /{config_id}/graph` on
  `config_router` (after the existing `/rebuild` route, `:350-382`): `Query` `limit` param
  bounded the same way as `graphrag.py:376`; `KnowmapConfigService(db).get(config_id)` for
  existence (already raises `KnowmapConfigNotFound`); `_assert_project_membership` (local,
  `:184-195`); `KnowmapGraphService(db).get_graph(config_id=config_id, limit=limit)`;
  assemble a `KnowmapGraphOut{config_id, nodes: [KnowmapGraphNodeOut{...}], edges:
  [KnowmapGraphEdgeOut{...}], truncated}` response — field-for-field identical to
  `graphrag.py`'s `GraphOut`/`GraphNodeOut`/`GraphEdgeOut` but declared locally in
  `knowmap.py` (per Q-2, no cross-file model import).
- **Backend — new `app/api/ws/knowmap.py`** — `GET /ws/knowmap/{config_id}`, mirroring
  `app/api/ws/graphrag.py` line-for-line: `authenticate_subprotocol`, admin bypass else
  `KnowmapConfigService(session).get(config_id)` + `TenancyRoleResolver.roles_for`, then
  `connection_loop(..., channels=[knowmap_channel(config_id)], ...)`.
- **Backend — `app/api/v1/__init__.py`** — import `from app.api.ws import (knowmap as
  ws_knowmap,)` alongside the existing WS imports (`:142-150`); add
  `RouterEntry(ws_knowmap.router)` to the `# WebSockets` block (`:235-242`).
- **Docs** — update `app/api/ws/__init__.py`'s docstring and `backend/CLAUDE.md:33`'s
  route-file counts to the corrected current totals (8 files in `app/api/ws/` including
  `__init__.py`, i.e. 7 route modules after adding `knowmap.py` — the "(6 files)"/"five
  dedicated paths" figures are already stale today, independent of this change; fix both
  while touching this area rather than compounding the drift).
- **API contract** — two new endpoints (`GET /api/knowmap-configs/{config_id}/graph`,
  `GET /ws/knowmap/{config_id}`); `gen:api` rerun is N/A this task (backend-only, no
  frontend consumer yet — Phase 4β reruns it when it wires the client).

## 7. NFR Checklist

- [x] i18n — N/A, backend-only, no user-facing strings.
- [x] Audit log — N/A; build-state transitions are operational telemetry, not an audited
  domain event (matches the existing Concept Map precedent — `publish_build_state` is
  never audit-logged either).
- [x] Tenant isolation — both new routes gate on project membership via the existing
  local `_assert_project_membership`/`TenancyRoleResolver` pattern, identical to the
  Concept Map routes; no new AuthZ surface introduced.
- [x] Error handling UX — `KnowmapConfigNotFound` (already defined) on a missing config;
  WS route closes 4401/4404/4403 mirroring `graphrag.py`'s WS route exactly.
- [x] Performance — the graph endpoint reuses the existing degree/confidence-capped,
  `truncated`-flagged `fetch_graph` query (`DEFAULT_GRAPH_LIMIT=500`,
  `MAX_GRAPH_LIMIT=2000`) — no new unbounded query risk. The WS channel carries only
  build-state transitions (a handful of small JSON messages per build), not graph data.

## 8. Security Considerations

Touches WebSocket authentication and tenant boundaries — required.

- **WS AuthN/AuthZ mirrors the exemplar exactly.** Subprotocol-carried token via
  `authenticate_subprotocol`; admin bypass; else project-membership check before
  subscribing to the channel — no new authorization logic, just the existing pattern
  applied to a second config table. A caller cannot subscribe to another project's
  knowmap build events any more than they can today for graphrag's.
- **Channel key is not a capability token.** `ws:knowmap:{config_id}` is guessable
  (sequential-looking UUIDs aside) but grants nothing on its own — the WS route's
  membership check gates the *subscribe* action, not the channel name, matching the
  existing `graphrag_channel`/`rag_channel` threat model (no change in posture).
  Redis pub/sub is internal-network-only (not exposed to clients directly).
- **Cross-wire fix reduces surface, doesn't add any.** Correcting knowmap builds to
  publish on their own channel removes noise from `graphrag_channel` (which nothing
  currently reads, so this is not fixing an active leak) — no new exposure either way.
- **No transport bypass.** New route follows the same `connection_loop` primitive as
  every other WS route; no direct Redis exposure to clients.

## 9. Quality Notes

- **Existing debt (do not imitate further, but don't expand scope to fully fix either).**
  `app/api/ws/__init__.py`'s docstring and `backend/CLAUDE.md`'s route-file count are
  already stale (say "five"/"(6 files)" against 7 actual route modules pre-this-task) —
  this task corrects both to their new-correct totals as a byproduct of touching the area
  (§6), but a broader doc-accuracy sweep is out of scope.
- **Patterns to follow.** `app/api/v1/graphrag.py:373-413` (`read_graph`) for the endpoint
  shape; `app/api/ws/graphrag.py` (full file) for the WS route shape;
  `graphrag_graph_service.py` for the service shape;
  `contexts/knowledge/infrastructure/channels.py` for channel-function naming.
- **Reuse inventory (import, do not re-create).** `Neo4jAsyncDriver.fetch_graph` (fully
  domain-agnostic, zero changes); `shared_kernel.realtime.connection.connection_loop`;
  `shared_kernel.realtime.pubsub.Publisher`; `authenticate_subprotocol`;
  `TenancyRoleResolver`/`get_role_resolver`; `KnowmapConfigNotFound` (already defined,
  `errors.py:140-141`); `KnowmapConfigRepository.require` (already defined,
  `knowmap_repositories.py:134-138`).

## 10. Risks and Rollback

- **Shared-engine blast radius.** `GraphRagBuilder`/`ReconciliationLoop` are on the
  Concept Map's live build path. Mitigation: the new param is optional and
  additive-only, default preserves today's exact behavior for every existing call site;
  `test_graphrag_builder.py`'s existing `test_publishes_build_state_on_each_transition`/
  `test_publishes_failed_state_on_phase1_failure` (`:927-980`) must still pass unmodified
  as a regression guard that the default path is untouched.
- **Reconciler crash-recovery path is less exercised than the live builder.** Mitigation:
  add an explicit unit test asserting `_knowmap_loop()`-style construction publishes to
  `knowmap_channel`, not `graphrag_channel`, for at least one of the 3 reconciler publish
  sites (the `IDLE`-with-`build_id` recovery-success path, the one most likely to matter
  in practice).
- **Rollback** — backend-only, additive routes + one optional constructor param each on
  two shared classes; revert the commit(s). No migration, no data written that a rollback
  would need to reverse (the Neo4j graph data itself is untouched by this task).

## 11. Acceptance Criteria

- [x] AC-1: `GET /api/knowmap-configs/{config_id}/graph` returns a bounded node/edge view
  of an existing config's Neo4j subgraph, 404s via `KnowmapConfigNotFound` for a missing
  config, and 403s for a non-member principal. (`test_knowmap_graph_service.py`,
  `test_knowmap_graph_endpoint.py`.)
- [x] AC-2: `GET /ws/knowmap/{config_id}` accepts a subscription from a project member,
  closes 4401/4404/4403 respectively for an unauthenticated/missing-config/non-member
  connection attempt, mirroring `/ws/graphrag/{config_id}`'s codes exactly. (Generic
  AuthZ-branch coverage in `test_ws_config_route.py`; per-route wiring smoke tests in
  `test_ws_knowmap.py`/`test_ws_graphrag.py`/`test_ws_rag_configs.py` — see D-4.)
- [x] AC-3: a Knowledge Map build (`knowmap_build` task) publishes its state transitions
  (`RUNNING`, `NEO4J_COMMITTED`, `FAILED_COMPENSATING`, `IDLE`/`FAILED`) onto
  `ws:knowmap:{config_id}`, not `ws:graphrag:{config_id}` — verified by a spy test on
  `publish_build_state`'s resolved channel argument
  (`test_channel_override_used_when_channel_fn_set`).
- [x] AC-4: a reconciler-recovered Knowledge Map build (`_knowmap_loop()`) publishes onto
  `ws:knowmap:{config_id}` as well — same spy-test pattern as AC-3, applied to the
  `IDLE`-with-`build_id` `ReconciliationLoop` publish call site
  (`test_reconciler_recovery_publishes_to_overridden_channel`).
- [x] AC-5: existing Concept Map behavior is provably unchanged —
  `test_graphrag_builder.py`'s existing build-state-publish tests pass unmodified, and
  `test_default_test_channel_fn_matches_production_concept_map_wiring` confirms Concept
  Map's `GraphRagBuilder` still publishes onto `graphrag_channel(config_id)` (adjusted per
  D-1: `channel_fn` became required rather than defaulting to `None`, so the regression
  guard now asserts explicit `graphrag_channel` wiring instead of an implicit `None`
  fallback — same behavioral guarantee, stronger contract).
- [x] AC-6: `pytest -q` (1636 passed, `tests/wiring/` excluded — pre-existing environment
  dependency, see D-5), `ruff check . && ruff format --check .`, `mypy .` (no new errors
  vs. the pre-existing baseline) all pass in `backend/`.

## 12. Test Plan

- **Unit** (`backend/tests/unit/`): `KnowmapGraphService.get_graph` — happy path (nodes/
  edges assembled, self-consistency for out-of-window edge endpoints), missing config
  raises `KnowmapConfigNotFound`, limit clamping. New test file, no existing exemplar
  (per §4, the Concept Map equivalent is also untested today) — mirror the *shape* of
  `test_graphrag_builder.py`'s fake-driven style (`FakeNeo4j`-equivalent), not an existing
  graph-service test.
- **Unit**: `publish_build_state`'s new `channel` override — default preserved when
  unset, honored when passed. Extend `graphrag_builder.py`'s existing capture-spy tests
  (`:927-980`) with a `channel_fn`-set variant asserting the spy receives the overridden
  channel; add the Concept Map default-unset variant as the regression guard (AC-5).
- **Unit**: `ReconciliationLoop` channel threading — same capture-spy pattern applied to
  at least the `IDLE`-with-`build_id` call site (`:328`), for both `channel_fn` unset
  (Concept Map, AC-5) and set (Knowledge Map, AC-4).
- **Integration/manual (`verify`)**: N/A for a full live-turn check (no frontend consumer
  yet); a manual `curl`/`websocat` smoke test against a running dev stack — create a
  Knowledge Map config, upload a document, trigger rebuild, confirm
  `GET .../graph` returns the built subgraph and a WS client on `/ws/knowmap/{id}`
  receives the `build.state` sequence — is the closest available check and should be
  run before closing this task, documented in the Deviation/Follow-up log if skipped.

## 13. SRS Delta

- **[R11.24]** A Knowledge Map's graph is readable via a bounded node/edge query
  (`GET /api/knowmap-configs/{config_id}/graph`, same degree/confidence capping and
  `truncated` semantics as the Concept Map graph read) and its build-state transitions
  are subscribable over a dedicated WebSocket channel
  (`GET /ws/knowmap/{config_id}`), both gated by project membership — mirroring [R11.17]'s
  Concept Map graph/build-status read grant, adapted to Knowledge Map's project-membership
  (not room-ACL) gating since a Knowledge Map has no chatroom owner.

## 14. Open Questions

None blocking.

## 15. Deviation Log

- **D-1 — `channel_fn` made required, not optional-with-default.** §5's Q-1 decision
  (`channel: str | None = None` on `publish_build_state`, silent fallback to
  `graphrag_channel`) shipped initially as designed, but a `/code-review` pass (run
  immediately after the spec's ACs were first green) flagged it as a latent footgun: a
  future third consumer of the shared `GraphRagBuilder`/`ReconciliationLoop` engine that
  forgets to wire `channel_fn` would silently misdirect its build-state events onto the
  Concept Map channel — the exact failure class this task exists to fix for Knowledge
  Map, reintroduced generically. Changed `channel_fn` to a required keyword-only
  constructor parameter (no default) and `publish_build_state`'s `channel` to a required
  parameter (no default, `graphrag_channel` import removed from `graphrag_events.py`
  entirely) — a missing wiring is now a `TypeError` at construction time instead of a
  silent misroute. Both existing call sites (`app/workers/tasks/graphrag.py`,
  `app/workers/graphrag_reconciler.py`'s `_loop()`) updated to pass
  `channel_fn=graphrag_channel` explicitly.
- **D-2 — the graph endpoint routes through `KnowledgeFacade`, not raw services.** §6
  originally had `read_knowmap_graph` call `KnowmapConfigService`/`KnowmapGraphService`
  directly (matching `knowmap.py`'s pre-existing file-wide convention, itself a
  `backend/CLAUDE.md` "app/api/v1/ calls only the facade" violation extended, not
  introduced, by the original draft). Code review flagged this as inconsistent with the
  `graphrag.py` sibling it was written to mirror. Added `KnowledgeFacade.get_knowmap_graph`
  (mirroring `get_graphrag_graph`) and rewired the route through it — matches `graphrag.py`
  structurally now, while `knowmap.py`'s other ~12 routes keep their pre-existing
  facade-bypass pattern (out of scope; not worsened, see FU-3).
- **D-3 — reused `Neo4jAsyncDriver`/service-level double config-lookup accepted, not
  eliminated.** The graph endpoint fetches the config once for AuthZ then
  `KnowmapGraphService.get_graph` fetches it again internally — flagged in review as an
  avoidable inefficiency. Investigated eliminating it (passing the pre-fetched config
  into the service) but that would mean touching `graphrag_graph_service.py`'s shipped,
  in-production Concept Map path too (against §5 Q-2's explicit risk-averse rationale) or
  diverging `knowmap.py`'s route from the `graphrag.py` pattern D-2 just aligned it to.
  Left as-is: after D-2, the double-fetch now precisely matches `graphrag.py`'s own
  shipped behavior rather than being a Knowledge-Map-specific regression — same cost,
  same place, already accepted for Concept Map.
- **D-4 — `/ws/graphrag`, `/ws/rag-configs`, and `/ws/knowmap` collapsed into a shared
  factory.** Not in the original §6 plan, which had `app/api/ws/knowmap.py` as a
  standalone hand-copy of `app/api/ws/graphrag.py` (per Q-1/exemplar-mirroring). Review
  flagged this as a third copy of the same untested ~40-line auth/close-code scaffold.
  Added `contexts/knowledge/interfaces/ws_config_route.py`
  (`make_config_scoped_ws_router`), migrated all three route files onto it, and added
  `test_ws_config_route.py` (generic AuthZ-branch coverage, all 5 branches) plus thin
  per-route wiring tests (`test_ws_knowmap.py`, `test_ws_graphrag.py`,
  `test_ws_rag_configs.py`) — the two pre-existing routes had zero test coverage of their
  route bodies before this change; they have it now.
- **D-5 — the manual `verify` smoke test (§12) was not run.** Attempted per user request;
  blocked by five unrelated pre-existing environment issues in sequence (no local docker
  stack running; a missing `smap_test` database; `alembic_version.version_num` too narrow
  for a truly-fresh migration chain — pre-existing, unrelated to this task; a real bug in
  `app/bootstrap/seed.py` — see D-6; and finally an OS-level `Cannot allocate memory`
  error from Docker Desktop unrelated to any code). User elected to stop after fixing D-6
  and accept the automated test suite as sufficient evidence. See FU-4/FU-5 for the
  undiagnosed issues left behind.
- **D-6 — unrelated `app/bootstrap/seed.py` bug fixed, out of scope, per explicit user
  request.** While attempting the D-5 smoke test, `seed_test_users()` crashed with
  `AgentRepository.create() missing 1 required keyword-only argument: 'effort'` — a
  pre-existing bug (also flagged independently by `mypy`, previously dismissed as routine
  debt) blocking the E2E test-overlay stack from starting at all. Added `effort=None` to
  the `agents_repo.create(...)` call. Unrelated to R11.24; fixed only because it blocked
  D-5 and the user explicitly asked for the one-line fix.
- **D-7 through D-10 — `/code-review` findings fixed post-hoc, out of the original §6
  scope.** A full `/code-review` pass (committed range + working tree) surfaced 10
  findings; the user asked to fix all. Four are pre-existing bugs/debt in already-shipped
  code, unrelated to R11.24 but fixed in this session at the user's request:
  - **D-7** — `purge_config_external_stores` (`graphrag_config_service.py`) and its sibling
    `KnowmapConfigService.cascade_external_stores` reported `neo4j_purged`/`qdrant_purged`
    as `True` by default *before* checking whether the Neo4j/Qdrant client was even
    present, so a missing client (e.g. `settings.neo4j` unset) silently reported a purge
    that never ran. Both flags now start `False` and flip `True` only on confirmed
    success. New `test_graph_config_purge.py` (6 tests, both functions).
  - **D-8** — `agent_service.py`'s module docstring claimed an R11.01 key-group
    billing-separation guardrail is enforced for attached knowledge configs; the
    enforcing code (`_assert_graphrag_config_compatible`) was removed in an earlier phase
    and never replaced for the newer `knowmap_config_id` attach path. Corrected the
    docstring to describe what's actually enforced (SEC-H1 project-membership only) and
    flagged the open product question (should R11.01 apply to Knowledge Map, unlike the
    R11.11-exempted Concept Map?) rather than silently inventing new validation logic —
    see FU-6.
  - **D-9** — four instances of hand-copied logic collapsed into shared helpers, each with
    new or extended test coverage: `_assert_project_membership` (graphrag.py, knowmap.py,
    agent_groups.py → `app/api/v1/deps.py:assert_project_membership`,
    `test_deps_assert_project_membership.py`); `_normalise_mime` (`ingest_service.py`,
    `knowmap_ingest_service.py` → `shared_kernel/text_extraction/parsers.py:normalise_mime`,
    tests moved into `test_text_extraction_parsers.py`); `_normalise_queries`/
    `_compact_excerpt` (rag/graphrag/knowmap context providers, up to 3-way duplicated →
    new `contexts/knowledge/application/context_provider_text.py`,
    `test_context_provider_text.py`); `validate_agent_allowlist`/
    `validate_knowmap_agent_allowlist` (rag.py, knowmap.py →
    `app/api/v1/deps.py:validate_agent_allowlist(config_id_attr=...)`, thin per-module
    wrappers preserved for backward compatibility with existing importers in `tus.py` and
    `test_knowmap_authz.py`; new `test_deps_validate_agent_allowlist.py`,
    `test_rag_allowlist_validation.py`).
  - **D-10** — two call sites (`knowmap_tus_finalizer.py`, `rag_tus_finalizer.py`) that
    imported the old private `_normalise_mime` names directly were missed in the first
    pass of D-9's consolidation (found by `mypy`, not by the original grep); fixed to
    import `normalise_mime` from the new shared location.

## 16. Follow-ups

- FU-1 — `app/api/ws/__init__.py`'s "five dedicated paths" docstring and
  `backend/CLAUDE.md`'s "(6 files)" note are stale even before this task (7 route modules
  already exist); this task corrects both to their new-correct post-knowmap totals, but a
  wider audit of doc-vs-code drift elsewhere is out of scope.
- FU-2 — Phase 4β frontend (`docs/tasks/2026-07-07-graphrag-phase4b-knowledge-map-ui/spec.md`)
  resumes once this ships: it wires `agentsApi.getKnowmapGraph`/a `useKnowmapSocket`
  composable against the two endpoints this task adds, and reruns `check:openapi-drift`.
- FU-3 — `app/api/v1/knowmap.py`'s other ~12 routes (config CRUD, document upload/list/
  delete, allowlist set, rebuild) still instantiate `KnowmapConfigService`/
  `KnowmapDocumentRepository` directly instead of routing through `KnowledgeFacade`, same
  as `app/api/v1/rag.py`'s and `app/api/v1/graphrag.py`'s non-graph routes (a systemic,
  file-wide pattern, not unique to this task — see D-2). A full facade-routing sweep
  across the RAG/GraphRAG/Knowledge-Map API surface is out of scope here.
- FU-4 — the manual live smoke test (§12, D-5) was never completed. Two concrete backend
  gaps surfaced along the way and remain undiagnosed: (a) `alembic upgrade head` against a
  genuinely fresh database fails partway (`StringDataRightTruncation` on
  `alembic_version.version_num`, a `VARCHAR(32)` default vs. a revision id like
  `0032_audit_retention_delete_grant` at 34 chars) — the already-running dev `smap`
  database has this column pre-widened to `VARCHAR(255)` by some undocumented means, so
  this has been silently masked in every environment that matters until a truly fresh
  install is attempted; (b) the E2E test-overlay stack (`compose.test.yml` +
  `docker-compose.override.yml`) hit an OS-level `Cannot allocate memory` error under
  Docker Desktop on the machine this session ran on, immediately after the D-6 fix, cause
  undiagnosed (plausibly a Windows/WSL2 resource-limit interaction with the bind-mounted
  `--reload` dev image, not confirmed).
- FU-5 — once FU-4(a) is fixed (e.g. a migration that explicitly widens
  `alembic_version.version_num`, or documenting the expected manual step), the manual
  smoke test from §12 should actually be run against a genuinely fresh stack before the
  Phase 4β frontend starts driving these two endpoints for real.
- FU-6 — D-8 surfaced an open product question: should the R11.01 builder-vs-consumer
  Key Group distinctness rule apply to a Knowledge Map's `builder_key_group_id`? [R11.11]
  explicitly exempts Concept Map (no single consumer agent), but a Knowledge Map *does*
  have a well-defined consumer agent (via `agents.knowmap_config_id`), so R11.11's stated
  rationale for the exemption doesn't obviously extend to it. Needs a product decision,
  then either an SRS amendment confirming the exemption extends to Knowledge Map too, or
  a new `_assert_knowmap_builder_key_group_distinct` check in `agent_service.py`.
