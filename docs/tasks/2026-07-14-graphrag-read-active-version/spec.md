---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.04]
---

# F-10: Reads can observe Phase-1 graph mutations before atomic build completion

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-10).

## 1. Summary

The 2PC builder commits Neo4j in Phase 1 and sets `neo4j_committed` *before* the Qdrant
Phase 2, and compensation for a Phase-2 failure runs later still. Retrieval, however, loads
any live config and queries current Neo4j (and all Qdrant points) with **no check of build
state** — so an Agent turn that lands between Phase 1 and Phase 2, or during
`failed_compensating`, can read edges from a build that may still roll back. Because the graph
is the union of many builds' surviving rows (additive `MERGE` for Knowledge Maps, delta
re-embed for Concept Maps), the per-row `build_id` is "last build that touched this row," not
a version — so a single active-version filter cannot cheaply serve the last good graph. The
fix instead **gates reads on build state** at a single chokepoint: while a config is in a
transient/uncommitted state (`running`, `neo4j_committed`, `failed_compensating`),
`GraphRagRetrieveService.query` returns an empty bundle so the caller skips that graph block
for the turn; in steady `idle`/terminal states it reads normally. Because both Concept Map
and Knowledge Map retrieval funnel through that one service, the single gate covers both
products. This never serves half-committed or later-reverted knowledge, at the cost of a
degraded graph block only during the (short) build window.

## 2. Observed vs Expected

- **Observed** — the builder applies Neo4j triples, sets `NEO4J_COMMITTED`, and durably
  commits that transition *before* Phase 2
  (`backend/contexts/knowledge/application/graphrag_builder.py:320-347`), then embeds and
  upserts Qdrant (`:349-369`); a Phase-2 failure moves to `FAILED_COMPENSATING` and leaves the
  snapshot for later compensation (`:372-399`). Retrieval's `query` loads the config via
  `_load` (`backend/contexts/knowledge/application/graphrag_retrieve.py:83-87,98`) and
  proceeds directly to vector search and Neo4j `traverse` with **no build-state or
  active-version check** (`:98-133`); the code comment confirms it "deliberately do[es] NOT
  filter by `build_id`" (`:108-116`). The Knowledge Map read path funnels through the *same*
  service: `KnowmapContextProvider._retrieve_relations` constructs a
  `GraphRagRetrieveService` (knowmap-prefixed vector store, `KnowmapConfigRepository`) and
  calls `svc.query(...)` per query
  (`backend/contexts/knowledge/application/knowmap_context_provider.py:252-266`), so it
  inherits the same missing state check.
- **Expected** — [R11.04] / §11.2a: each build is transactional across Neo4j and Qdrant with
  compensation so that "a failure does not leave inconsistent state." A read that surfaces
  edges committed in Phase 1 but not yet durable in Phase 2 — or edges from a build that
  subsequently rolls back — exposes exactly the inconsistent, non-atomic intermediate state
  the 2PC exists to hide. Reads must observe only committed builds.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Active-version marker (serve last good version mid-build) vs read-gate on transient states? | **Read-gate on transient states.** While `last_build_state` is in-flight (`running`/`neo4j_committed`/`failed_compensating`) skip the graph block; read normally otherwise. | Code analysis showed per-row `build_id` marks the last build to *touch* a row, and the committed graph is the union across many builds (additive `MERGE` `neo4j_driver.py`, delta re-embed for Concept Maps `graphrag_retrieve.py:108-116`). A single `active_build_id` filter would hide still-valid rows from earlier builds, so "serve the last good version" is not achievable without a full per-row generation epoch (large: migration + dual-write on every MERGE + compensation changes). The read-gate is correct-always and small; the generation-epoch design is recorded as FU-1. |
| Q-2 | Which states are read-unsafe? | `running`, `neo4j_committed`, `failed_compensating`. Safe: `idle`, `qdrant_committed`, `failed`. | `qdrant_committed` is a transient success immediately followed by `idle` — both stores committed, safe to read. `failed` is terminal after a rollback and (once F-7 lands) means the graph is genuinely restored, so safe. The unsafe trio is exactly the reconciler's `_STUCK_STATES` (`graphrag_reconciler.py:61-65`) — the in-flight/uncommitted set. |

## 4. Reproduction

Preconditions: a config with a prior committed build; a new delta build in flight that has
reached `neo4j_committed` but not `qdrant_committed`.

1. Start a build that updates an existing edge's confidence/evidence; let it reach
   `neo4j_committed` (`graphrag_builder.py:337-347`) and pause before Phase 2.
2. Invoke an Agent turn whose retrieval traverses that edge
   (`graphrag_retrieve.py:98-133`): the turn observes the *new* edge value even though Phase 2
   has not committed.
3. Force Phase-2 failure → `failed_compensating` → compensation rolls Neo4j back
   (`graphrag_reconciler.py:350-397`). The turn in step 2 served knowledge that no longer
   exists — a non-atomic read.

Deterministic given the paused build state.

## 5. Root Cause Analysis

The causal chain:

1. The 2PC commits Neo4j and flips `NEO4J_COMMITTED` before Phase 2
   (`graphrag_builder.py:337-347`); Neo4j is the live read graph, mutated in place by `MERGE`,
   so between phases and during compensation it holds uncommitted-from-the-2PC's-perspective
   data.
2. Retrieval performs no build-state check before reading — `GraphRagRetrieveService.query`
   (`graphrag_retrieve.py:98-133`), the single service both products call (Knowledge Map via
   `knowmap_context_provider.py:252-266`). **This is the root cause** — the earliest link
   whose correction (skip the graph block while
   the config is in an in-flight build state) prevents reads from observing partial or
   later-reverted state, for both products.
3. The additive/delta storage model (per-row `build_id` ≠ version) is a structural constraint,
   not a defect: it is why the fix must gate on *state* rather than filter on a version. A true
   generation epoch would allow non-blocking last-good reads but is a separate, larger design
   (FU-1).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — both graph products expose partially-committed or eventually-reverted
  knowledge to Agents during any build window; frequency rises with build cadence and
  Phase-2/compensation latency.
- **Sibling suspects:**
  - **Knowledge Map read path — covered by the same chokepoint, no separate edit.**
    `KnowmapContextProvider._retrieve_relations` funnels through the same
    `GraphRagRetrieveService.query` (`knowmap_context_provider.py:252-266`) with a
    knowmap-prefixed vector store and `KnowmapConfigRepository` as its `configs` port, so the
    single gate in `query` (§7.2) gates it automatically. Verified: `KnowmapConfigRepository`
    satisfies `GraphRagConfigRepositoryPort` (`knowmap_repositories.py:84`) and its `get`
    returns a config exposing `last_build_state` (`:49-50,127`). No config-provider edit is
    needed.
  - **F-7 interaction — composes, cross-linked.** The gate treats `failed` as safe on the
    premise that a rollback truly restored the graph; F-7's fix makes `failed` mean a
    *successful* rollback (a false rollback stays `failed_compensating`, which the gate already
    treats as unsafe). The two fixes reinforce each other; neither depends on the other to be
    correct.
  - **F-9 interaction — complementary.** With reads gated to committed state, stale in-flight
    Qdrant points cannot be read mid-build; combined with F-9's deterministic IDs and the
    supersede sweep this further bounds duplicate-slot waste. Separate finding.
  - **`_STUCK_STATES` duplication — cleared, dedup opportunity.** The unsafe set equals the
    reconciler's `_STUCK_STATES` tuple (`graphrag_reconciler.py:61-65`); introducing one shared
    domain constant avoids two divergent copies (see §7.1). This is a reuse improvement, not a
    behavior change.

## 7. Fix Design

1. **Define the in-flight state set once in the domain layer.** Add
   `IN_FLIGHT_BUILD_STATES: frozenset[BuildState] = frozenset({BuildState.RUNNING,
   BuildState.NEO4J_COMMITTED, BuildState.FAILED_COMPENSATING})` to
   `backend/contexts/knowledge/domain/graphrag.py`, with a docstring stating it is the set of
   states in which the graph is mid-2PC and must not be read. Optionally refactor the
   reconciler's `_STUCK_STATES` (`graphrag_reconciler.py:61-65`) to reference it, noting the
   two are semantically distinct today but coincide (a divergence must be a deliberate edit).
2. **Gate both products at the single retrieval chokepoint.** In
   `GraphRagRetrieveService.query`, right after `_load` (`graphrag_retrieve.py:98`), if
   `cfg.last_build_state in IN_FLIGHT_BUILD_STATES` return an empty
   `GraphRagBundle(entities=(), relations=(), evidence_excerpts=())` — the service's declared
   return type (`:97`). Do not raise — an in-flight build is a normal condition, not an error.
   One gate covers **both** products because both retrieval paths construct a
   `GraphRagRetrieveService` and call `query`:
   - Concept Map — `GraphRagContextProvider._graphrag_query`
     (`graphrag_context_provider.py:279-298`, `configs=GraphRagConfigRepository`).
   - Knowledge Map — `KnowmapContextProvider._retrieve_relations`
     (`knowmap_context_provider.py:252-266`, `configs=KnowmapConfigRepository`,
     `prefix="knowmap"`).
   Both config repos satisfy `ConfigLike`, which declares `last_build_state`
   (`graphrag_ports.py:211`; knowmap row mapping `knowmap_repositories.py:49-50`), so the gate
   compiles and runs for both.
3. **Empty-bundle propagation (no further change).** An empty bundle omits the block end to
   end: Concept Map — `_merge_bundles` yields empty entities/relations and `query`/
   `query_layers` drop it via `not (bundle.entities or bundle.relations)`
   (`graphrag_context_provider.py:95-96,154-158`), and the turn engine only appends truthy
   blocks (`turn_engine.py:945-950`); Knowledge Map — `_retrieve_relations` extends nothing
   (`knowmap_context_provider.py:263-266`), so `kept` is empty and `query` returns `None`
   (`:167-169`). **Per-config granularity is preserved:** in a multi-layer Concept Map turn,
   only the in-flight layer's `query` returns empty while idle layers still contribute
   (`query_layers` loops per config, `graphrag_context_provider.py:139-156`).
4. **Update the misleading comment.** The retrieval docstring claiming traversal is "tagged
   with the config's current active build" (`graphrag_retrieve.py:6-8`) is currently false;
   make it accurate to the state-gate or remove it.

No schema change, no build-path change. The builder's state transitions
(`graphrag_builder.py`) already provide the signal; the fix only makes the shared read
service honor it.

**Data repair:** none. This is a read-path guard; no persisted data is wrong. Turns that
already read partial state are historical and cannot be repaired.

## 8. Regression Test Plan

Unit tests:

1. **Service gated in transient states** (primary red-first test,
   `backend/tests/unit/test_graphrag_retrieve.py`): with a config stub whose
   `last_build_state` is `neo4j_committed` (and again `running`, `failed_compensating`), assert
   `GraphRagRetrieveService.query` returns an empty bundle and does **not** call
   `search_entities`/`traverse`. Fails today — retrieval ignores build state and traverses
   (`graphrag_retrieve.py:98-133`).
2. **Service allowed in committed states** (guard): with `idle` (and `qdrant_committed`,
   `failed`), assert normal retrieval proceeds and returns results.
3. **Knowledge Map coverage via the shared gate** (`test_knowmap_context_provider.py` or
   equivalent): with the knowmap config in `neo4j_committed`, assert
   `KnowledgeMapContextProvider.query` returns `None` without seeding Qdrant/Neo4j — proving
   the single service gate flows through the knowmap provider; with `idle`, assert it retrieves
   normally.

## 9. Risks and Rollback

- **Recall gap during builds** — a turn during an in-flight build loses the graph block. This
  is the intended trade (correctness over stale/uncommitted recall) and is bounded by the
  build window; the generation-epoch alternative (FU-1) would remove it at high cost.
- **State staleness** — `last_build_state` is read from the config row; ensure retrieval reads
  a fresh row (it already loads per query via `_load`, `:83-87`), so the gate reflects the
  current build.
- **Over-gating `failed`** — treating `failed` as safe depends on F-7's corrected rollback
  semantics; before F-7, a false `failed` could still expose partial data. Documented as a
  cross-link, not a blocker (the unsafe trio already covers the live failure window).
- **Rollback** — remove the gate (and the shared constant if introduced); code-only, no schema
  change.

## 10. Acceptance Criteria

- [ ] AC-1: The transient-state gate regression test (§8.1) fails before the fix and passes
  after.
- [ ] AC-2: `GraphRagRetrieveService.query` returns an empty bundle (no Neo4j/Qdrant reads)
  when the config is `running`, `neo4j_committed`, or `failed_compensating`, and reads normally
  in `idle`/`qdrant_committed`/`failed` — covering Concept Maps and Knowledge Maps through the
  one chokepoint.
- [ ] AC-3: Knowledge Map retrieval (`KnowledgeMapContextProvider.query`) returns `None` in the
  same in-flight states via the shared service gate and retrieves normally otherwise (§8.3).
- [ ] AC-4: `IN_FLIGHT_BUILD_STATES` is defined once in the domain layer and used by the read
  gate (and, if refactored, referenced by the reconciler's `_STUCK_STATES`).
- [ ] AC-5: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`.

## 11. SRS Delta

None. This enforces the [R11.04] / §11.2a atomicity guarantee ("a failure does not leave
inconsistent state") on the read path. If the product later wants non-blocking last-good reads
mid-build, that is a new requirement (generation epoch, FU-1) to draft separately.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (generation-epoch read isolation):** a monotonic per-config committed generation
  stamped on every touched Neo4j row and Qdrant payload would let reads serve the last fully
  committed version *without* skipping the graph block during builds. Large change (migration,
  dual-write on MERGE, compensation-aware generation rollback); scope separately if the recall
  gap proves material.
- **FU-2 (`_STUCK_STATES` dedup):** if not folded into §7.1, the reconciler's `_STUCK_STATES`
  and this gate's state set remain two copies of the same tuple — consolidate to the shared
  domain constant.
- **FU-3 (F-7 dependency note):** the safety of reading in `failed` assumes F-7's corrected
  rollback semantics; sequence or land F-7 alongside this fix.
