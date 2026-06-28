# GraphRAG / Neo4j Audit

Date: 2026-06-28
Scope: the `knowledge` bounded context (GraphRAG), its Arq workers and
reconciler, the `/api/graphrag` surface, the `/ws/graphrag` channel, and the
frontend `agents` slice GraphRAG views. Conducted ahead of building a
knowledge-graph visualizer.

Method: four parallel deep reviews (backend correctness, security/tenancy,
frontend, visualization readiness) followed by line-level verification of every
Critical/High finding against source.

## Summary

| Area | Verdict |
| --- | --- |
| Security & multi-tenancy | Strong. AuthZ enforced on every endpoint, Cypher fully parameterized, Neo4j network-isolated and authenticated, prod insecure-default guard present. No exploitable Critical/High. |
| Correctness & consistency | Real defects. The 2PC state machine is not durably persisted per transition, the Redis build lock can be stolen, the reconciler resolves the build id non-deterministically, and the Neo4j schema/indexes do not match the runtime data model. |
| Visualization readiness | Not ready. Topology exists in Neo4j but there is no api/facade read path, no pagination-safe full-graph query, and no entity type for rich rendering. |

Severity legend: Critical (data loss / corruption under realistic conditions),
High (correctness or availability defect), Medium (robustness / DoS / operability),
Low (hardening / hygiene).

---

## Critical / High

### C1 - 2PC state machine is not committed per transition (Critical)

`app/workers/tasks/graphrag.py:209`, `contexts/knowledge/application/graphrag_builder.py:139-320`

The builder issues `set_state(RUNNING) -> NEO4J_COMMITTED -> (FAILED_COMPENSATING |
QDRANT_COMMITTED -> IDLE)` on the shared `AsyncSession`, but the only
`db.commit()` happens in the worker after `builder.run()` returns. Every
intermediate Postgres state collapses into one terminal commit. Neo4j writes,
the Redis snapshot, and the Redis lock are durable immediately, so Postgres and
the external stores diverge for the whole build window. `RUNNING` and
`NEO4J_COMMITTED` are never observable in the database.

### C2 - Crash between Neo4j commit and worker commit is invisible to the reconciler (High)

`graphrag_builder.py:195-216`, `contexts/knowledge/application/graphrag_reconciler.py:81`

Direct consequence of C1. If the worker is hard-killed (OOM/SIGKILL) after
`apply_triples` commits in Neo4j but before `db.commit()`, Postgres rolls back to
the pre-run state. Neo4j keeps orphan triples tagged with `build_id`; the
reconciler only scans `FAILED_COMPENSATING`, so it never heals this row.
Retrieval then finds no Qdrant vectors and silently returns empty GraphRAG
context while the row looks healthy.

### C3 - Redis build lock has no fencing token; lock can be stolen (High)

`contexts/knowledge/infrastructure/redis_lock.py:45-51`

`acquire` writes a constant value (`SET key "1" NX EX`); `release` is an
unconditional `DELETE`. A build whose extraction + embedding exceeds the 10-minute
TTL loses the lock, a second build acquires it, and when the first build finishes
its `release` deletes the second build's lock - allowing a third concurrent
build. Two concurrent builds interleave MERGE/SET on the same subgraph and
corrupt Neo4j + Qdrant.

### C4 - Reconciler resolves the build id via a non-deterministic SCAN (High)

`redis_lock.py:97-114`, `graphrag_reconciler.py:224-237`

`scan_current` returns the first key from `scan_iter` and breaks. `SCAN` has no
ordering, so "most recent" is false. With more than one snapshot for a config
(e.g. a prior crashed build), the reconciler may resolve the wrong `build_id`,
then either finalize without re-embedding the real build's entities (silent data
loss) or roll back the wrong build.

### C5 - Neo4j bootstrap schema does not match the runtime data model (High)

`smap/bootstrap/neo4j_init.py:26-33` vs `infrastructure/neo4j_driver.py:73-78`

Bootstrap creates a uniqueness constraint on `:Entity(id)`, an index on
`:Entity(canonical_name)`, and a relationship index on `:REL(type)`. The driver
never writes `id`, `canonical_name`, or `type`; entities carry
`{graphrag_config_id, name, build_id}` and relationships carry
`{graphrag_config_id, relation, ...}`. The constraint is inert and both indexes
are dead. There is no index on the real MERGE key `(graphrag_config_id, name)`,
so every apply/snapshot/traverse/delete does a full label scan, and concurrent
MERGE can create duplicate nodes. This is also a direct scale blocker for a
full-graph visualization query.

### C6 (frontend) - Poll fallback is dead when the socket never connects (High)

`frontend/src/slices/agents/composables/useGraphragSocket.ts:32-100`

`liveState` is only ever seeded by a WS event or by `syncStatus`, and
`syncStatus` runs only after a successful connect (`onStatus(connected=true)`).
The backstop poll skips any config whose `liveState[id]` is `undefined`. So for a
build started elsewhere or a reload mid-build where the socket fails to connect -
the exact scenario the poll exists to cover - the card sticks on `running`
forever. The fallback depends on the channel it is meant to back up.

---

## Medium

| # | Location | Issue |
| --- | --- | --- |
| M1 | `graphrag_reconciler.py`, `graphrag_config_service.py:228` | Reconciler Phase-2 retry / rollback and `admin_reset` take no build lock - no mutual exclusion with an in-flight build. |
| M2 | `workers/tasks/graphrag.py:199-210` | Metric `_set_state` uses labels `building/ready/failed`; the enum has no `ready`, so a success and a `failed_compensating` build both report `idle`. Operators get no "stuck compensating" signal. |
| M3 | `neo4j_driver.py:77-82` | `evidence_msg_ids` / `confidence` are overwritten (last-write) on every MERGE; cross-build restatements lose prior evidence. |
| M4 | `neo4j_driver.py:109-118` | `delete_by_build`: when a build has no relationships, the second `MATCH` after `WITH` never runs, so isolated nodes are not deleted on rollback. |
| M5 | `neo4j_driver.py:185-196`, `graphrag_retrieve.py:112` | `traverse` and the retrieval bundle have no `ORDER BY`; `LIMIT 50` + 2 KB cap drop arbitrary edges, so high-confidence relations can be lost. |
| M6 | `graphrag_builder.py:184` | `mode="full"` still passes `since=last_build_at`; a full rebuild only loads the delta and never re-extracts history. |
| M7 | `graphrag_builder.py:367` | Per-entity description concatenates one fragment per triple with no cap; a hot entity can exceed the embedder input limit and fail Phase-2 repeatedly. |
| M8 | `app/api/v1/graphrag.py:332` | `trigger_build` has no rate limit and does not short-circuit when a build is already in progress; the queue can be flooded. |
| M9 | `workers/tasks/graphrag.py:44-106` | Delta load accumulates every matching message into memory with no overall cap - memory-exhaustion risk for large chatrooms. |
| M10 | `infrastructure/triple_extractor.py:62-107` | The whole delta is concatenated into a single extraction prompt with no chunking - can exceed the context window and explode BYO-key cost. |
| M11 | `app/api/v1/graphrag.py:52,59` | `trigger_config: dict[str, object]` is accepted unvalidated and persisted as JSON - the one real API-boundary input gap. |
| M12 (fe) | `GraphragConfigListView.vue:251-261,528` | Status drawer shows a blank body while loading and on fetch error. |
| M13 (fe) | `AgentDetailView.vue:517-537` | Knowledge-tab GraphRAG status is a plain query, never live, never invalidated on terminal - shows stale state after a build elsewhere. |
| M14 (fe) | `useGraphragSocket.ts`, `GraphragConfigListView.vue:105` | `IN_PROGRESS` set duplicated across files (drift risk); poll timer never stops after all builds finish; shared-channel teardown clobbers co-watchers. |

## Low / data-model gaps

- L1 - Neo4j entities store no type, id, timestamp, degree, or description
  (description lives only in Qdrant). Relations store a single overwritten
  `confidence`. Entity resolution is exact-string `name` match (casing/aliases
  fork nodes). `neo4j_driver.py:71-82`.
- L2 - `traverse` scopes only the start/end nodes by `graphrag_config_id`;
  intermediate nodes and `REL` are not scoped. Correct today only by an implicit
  write invariant - defense-in-depth gap. `neo4j_driver.py:185-196`.
- L3 - No Qdrant payload indexes on filtered fields; 404/403 tenant-existence
  oracle on config ids; dead i18n keys; build progress conveyed only by badge
  color with no live-region announcement.
- L4 - SoC: `app/api/v1/graphrag.py` instantiates `GraphRagConfigService`
  directly and imports private auth helpers instead of going through
  `interfaces/facade.py`.
- L5 - Tests: `useGraphragSocket.test.ts` covers only the happy path; the
  riskiest teardown/poll/reconnect logic is untested. E2E `10-graphrag.spec.ts`
  selectors do not match the rendered localized labels.

---

## Remediation status

This audit was followed by a fix pass. See the companion section below and the
commit history (`fix(backend,frontend): GraphRAG audit remediation`).

Fixed in this pass: C1, C2, C3, C4, C5, C6, M1-M8, M11, M12-M14, L1 (node degree
surfaced for visualization sizing), L2.

Deferred (tracked, larger scope): M9/M10 (delta memory cap + extractor chunking -
requires an extractor windowing redesign), L1 entity *type* (needs the extractor
to emit categories + a schema/migration to persist them, for node coloring),
L3/L4/L5 (Qdrant payload indexes, 404/403 oracle, dead i18n keys, a11y
live-region, full facade SoC refactor of the legacy endpoints, e2e selector fix).

## Visualization readiness - minimum backend chain

To unblock a frontend knowledge-graph viewer, respecting `api -> facade ->
application -> infrastructure`:

1. Driver `fetch_graph(config_id, *, limit)` returning both nodes (including
   isolated ones) and a bounded, ordered edge set, with node degree.
2. Application `get_graph(config_id, limit)` that loads the config (project
   scope) and assembles domain dataclasses.
3. Facade `get_graphrag_graph(...)` so the api layer never reaches into
   application/infrastructure.
4. `GET /api/graphrag/{config_id}/graph?limit=` with the same membership AuthZ
   as `read_config`, returning `{nodes, edges, truncated}`.

All four were implemented in the remediation pass.
