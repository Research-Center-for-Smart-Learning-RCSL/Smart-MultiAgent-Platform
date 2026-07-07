---
type: refactor
status: draft
created: 2026-07-07
requirements: [R11.15, R11.20]
supersedes:
---

# GraphRAG Phase 0 — Engine de-concreting & graph-data cleanup

Parent blueprint: `docs/tasks/2026-07-07-graphrag-two-axis-redesign/spec.md` (Phase 0).
This is the foundational, mostly-behavior-preserving phase that de-risks every later phase:
it makes the graph engine reusable through Protocol seams, retires a drifted (and
security-relevant) duplication, neutralizes the evidence type at the Python layer, and
closes a live graph-data leak.

## 1. Summary

Four independent workstreams, each landing as its own green milestone:

1. **Engine de-concreting** — inject a repo Protocol + a `ConfigLike` Protocol into
   `GraphRagBuilder` / `GraphRagRetrieveService` / `ReconciliationLoop` so they no longer
   hard-depend on the concrete `GraphRagConfigRepository` / `GraphRagConfig`; parameterize
   the Qdrant collection prefix. (behavior-preserving)
2. **`embed_resolution` helper** — collapse the two drifted embed-model/`_resolve_embed_key`
   copies into one shared helper. (behavior change: the worker path is corrected onto the
   secure `list_ordered_carried` listing — a deliberate SEC-H3 fix.)
3. **Neutral evidence type** — rename the Python evidence field to opaque
   `evidence_refs: tuple[str, ...]` and drop the UUID coercion; keep the Neo4j property key
   `evidence_msg_ids` (the driver is the translation seam). No stored-data migration.
   (behavior-preserving)
4. **Graph-data cleanup contract** — expose `cascade_external_stores` via the facade, fix
   the agent-delete leak inline, and add a reconciler orphan-sweep backstop. (behavior
   change: deleting an agent now purges its Neo4j/Qdrant graph — a deliberate leak fix.)

## 2. Motivation

Named debt, cited:

- **Abstraction leak / untestable coupling.** `GraphRagBuilder` (`graphrag_builder.py:122`),
  `GraphRagRetrieveService` (`graphrag_retrieve.py:59`), and `ReconciliationLoop`
  (`graphrag_reconciler.py:102,155,192,251`) each construct `GraphRagConfigRepository(db)`
  inline and are typed to the concrete `GraphRagConfig`, so the engine cannot be reused by a
  second subsystem (Knowledge Map) and tests can only substitute the repo by attribute-poking
  (`test_graphrag_builder.py:314` `builder._configs = store`) or module monkeypatching
  (`:484,:541`).
- **Drifted duplication with a security consequence.** The embed-model map + resolver is
  duplicated in `graphrag_context_provider.py:29-33,158-184` and
  `app/workers/tasks/graphrag.py:30-34,110-133`; the copies drifted on the key listing —
  the provider uses `list_ordered_carried` (enforces the SEC-H3 active-carry invariant), the
  worker uses `list_ordered` (no carry check), so **the builder can embed with a key whose
  project carry was revoked** (`group_repository.py:137-181`).
- **Type-safety erosion waiting to happen.** Evidence is `tuple[uuid.UUID, ...]` on `Triple`
  (`domain/graphrag.py:69`) / `RelationEdge` (`:91`), with UUID coercion that silently drops
  non-UUID tokens (`triple_extractor.py:163`, `graphrag_retrieve.py:119`) — a latent trap for
  the future document-evidence source.
- **Orphaned external state (live bug).** Deleting an agent never purges its graph:
  `AgentService.soft_delete` (`agent_service.py:502-525`) has zero graph awareness, and the
  60-day retention hard-delete (`retention.py:141-159`) fires the
  `graphrag_configs.agent_id ON DELETE CASCADE` (`graphrag_tables.py:21-27`) which cannot
  reach Neo4j/Qdrant — leaking the subgraph + vectors forever.

## 3. Non-goals

- **No externally observable behavior change, EXCEPT the two documented fixes**: (a) the
  builder's embed key selection is corrected onto `list_ordered_carried` (WS2); (b) deleting
  an agent now purges its graph (WS4). Both are intentional and covered by regression tests.
- **No Neo4j/Qdrant stored-data migration.** The physical property-key rename
  `evidence_msg_ids -> evidence_refs` is explicitly deferred to the phase that introduces
  document evidence (values stay UUID strings until then; a migration harness does not exist,
  §6/Q-2). Cypher keys are untouched.
- **No new owner model.** Typed-FK owner columns, `agent_group`, layered retrieval, temporal,
  and windowing are Phases 1-3, not here. WS4's owner-delete hooks are added only for the
  owners that exist today (agent); chatroom/workspace/agent_group hooks arrive with their
  ownership phases.
- **No API contract change** beyond one additive `KnowledgeFacade` method (WS4). `gen:api` is
  unaffected (the facade is server-internal; no route DTO changes).

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Consolidate embed resolution onto which listing method? | `list_ordered_carried`. | It enforces the SEC-H3 active-carry invariant and filters soft-deleted keys; `list_ordered` is the drifted, insecure copy. This makes WS2 a security fix, not just DRY. |
| Q-2 | Rename the Neo4j `evidence_msg_ids` property physically in Phase 0? | No — Python-only rename; keep the Cypher key. | The driver is the sole translation seam; values are already strings; the visualizer never reads the property; no Neo4j migration harness exists. Zero-risk, byte-identical. Physical rename rides with document evidence later. |
| Q-3 | Is the reconciler orphan-sweep in Phase 0 scope? | Yes, as milestone 4b, with a small prerequisite (project_id self-describing on Neo4j nodes). | The inline purge (4a) closes the leak; the sweep is the backstop for inline-purge failures and legacy orphans. Split out if it grows. |
| Q-4 | Refactor or feature/bugfix dossier? | Refactor base, with two labeled behavior fixes. | The bulk is structural; the two fixes are small, cited, and regression-tested. |

## 5. Current vs Target Structure

### WS1 — engine de-concreting

Add to `contexts/knowledge/application/graphrag_ports.py` (which already hosts
`BuildLockStore`, `SnapshotStore`, `Neo4jDriver`, `DeltaMessage`, `TripleExtractor` — all
`typing.Protocol`):

```
ConfigLike(Protocol):                 # structural — GraphRagConfig already satisfies it
    id: uuid.UUID
    project_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    last_build_state: BuildState
    last_build_at: datetime | None

GraphRagConfigRepositoryPort(Protocol):
    async def get(config_id, *, include_deleted=False) -> ConfigLike | None
    async def set_state(*, config_id, state, error=None, stamp_built_at=False) -> None
    async def list_in_state(state) -> Sequence[ConfigLike]
    async def list_all_ids(*, include_deleted=False) -> set[uuid.UUID]   # new, for WS4b sweep
```

Dependency change: `GraphRagBuilder` / `GraphRagRetrieveService` / `ReconciliationLoop`
take the repo Port via `__init__` instead of building `GraphRagConfigRepository(db)` inline.
`GraphRagConfig` (frozen dataclass) structurally satisfies `ConfigLike` unchanged, so the
domain is untouched. Layer order preserved (`application` depends on a Protocol declared in
`application`; the concrete repo in `infrastructure` is injected at the wiring edge).

Attributes actually used (verified): builder — `id, project_id, builder_key_group_id,
last_build_state, last_build_at`; retrieve — `id, project_id` (+ whole cfg to the embedder
factory -> `builder_key_group_id`); reconciler — `id, last_build_state`.

Qdrant prefix: `graphrag_collection_name(project_id)` -> `graphrag_collection_name(project_id,
*, prefix="graphrag")`; `GraphRagVectorStore.__init__(client, *, prefix="graphrag")` with a
private `self._name(project_id)`; the 7 internal call sites
(`graphrag_vector_store.py:56,97,128,166,207,251,268`) route through it. Default keeps all 6
current constructions behavior-identical.

### WS2 — `embed_resolution` helper

New `contexts/knowledge/application/embed_resolution.py` (no `app.*` import; consumed by both
the worker and the provider): owns the model map `{openai:text-embedding-3-small,
gemini:text-embedding-004, voyage:voyage-3}` and `resolve_embed_key(db, builder_key_group_id)`
built on `KeyGroupMemberRepository.list_ordered_carried` + `ApiKeyRepository.get_active`.
`graphrag_context_provider.py:29-33,158-184` and `app/workers/tasks/graphrag.py:30-34,110-133`
both delete their copy and call the helper. Net: one source of truth; the worker gains the
carry check.

### WS3 — neutral evidence type

`domain/graphrag.py`: `Triple.evidence_msg_ids` / `RelationEdge.evidence_msg_ids` ->
`evidence_refs: tuple[str, ...]`. Drop UUID coercion at `triple_extractor.py:158-165` and
`graphrag_retrieve.py:114-131` (keep the values as opaque `str`). `EvidenceFetcher` becomes
`Callable[[list[str]], Awaitable[list[str]]]`; the conversation fetcher
(`graphrag_context_provider.py:203-224`, `build_evidence_fetcher`) parses each ref back to a
message UUID at its own boundary before `get_message`. Neo4j driver keeps writing/reading the
property key `evidence_msg_ids` (`neo4j_driver.py:53,109-111,119,171,217,250`) — it maps
`domain.evidence_refs <-> neo4j 'evidence_msg_ids'`. Qdrant unaffected (payload carries no
evidence field, `graphrag_vector_store.py:85-90`).

### WS4 — cleanup contract

`cascade_external_stores(*, config_id, project_id)` (`graphrag_config_service.py:277-346`,
static, session-free) is the reusable primitive. Target:
- Expose `KnowledgeFacade.purge_graph_config_external_stores(config_id, project_id)` +
  `KnowledgeFacade.list_graph_configs_for_agent(agent_id)` (currently the facade exposes no
  delete/cascade/list-for-owner method, `facade.py:48-85`) so cross-context callers stay
  SoC-clean.
- **4a (leak fix):** `delete_agent` route (`app/api/v1/agents.py:338-369`) grows a
  commit-then-purge tail mirroring RAG (`rag.py:335-395`): after `service.soft_delete`,
  enumerate the agent's config(s) (0-or-1 today via the `unique=True` FK), soft-delete each
  config row, `db.commit()`, loop `cascade_external_stores`, emit `graphrag.infra_purged`.
- **4b (backstop):** `ReconciliationLoop.run_once` (`graphrag_reconciler.py:89`) gains an
  orphan-sweep after the `_STUCK_STATES` loop: diff graph-store config ids against
  `repo.list_all_ids(include_deleted=True)` and purge the difference via `self._neo4j.delete_all`
  + `self._vectors.delete_by_config`. Prerequisite: Qdrant deletion needs `project_id`, which
  an orphan (no PG row) lacks — add `project_id` as a Neo4j `:Entity` property so orphans are
  self-describing (cheap; write it in `apply_triples`). Enumerate live graph config ids from
  Neo4j (distinct `graphrag_config_id`).

## 6. Characterization Test Plan

Pin behavior before moving code (existing tests, gaps noted):

- **WS1:** `test_graphrag_builder.py` (builder + reconciler), `test_graphrag_retrieve.py`,
  `test_graphrag_vector_store.py` already exercise these. They substitute the repo by
  `_configs =` poke / module monkeypatch; after injection they pass the fake through the new
  `__init__` param. `GraphRagConfig` still satisfies `ConfigLike`, so no test *data* changes.
  Add one `test_graphrag_vector_store` case asserting a non-default `prefix` routes to a
  differently-named collection.
- **WS2:** characterization test first — assert the builder's embed key selection currently
  differs from the provider's when a member's carry is revoked (pins the bug), then after
  consolidation assert both select the carried key and skip the revoked one. Reuse
  `group_repository` fixtures.
- **WS3:** round-trip test — a triple with message-UUID-string refs survives
  extract -> Neo4j write (key `evidence_msg_ids`) -> retrieve read -> `RelationEdge.evidence_refs`
  -> conversation fetcher resolves the message. Assert byte-identical Neo4j property key and
  values vs pre-change (golden Cypher param).
- **WS4:** regression test — after `delete_agent`, assert `cascade_external_stores` ran
  (Neo4j `delete_all` + Qdrant `delete_by_config` invoked) and `graphrag.infra_purged` audit
  emitted; and an orphan config id absent from Postgres is purged by `run_once`.

## 7. Migration Steps

Each step leaves the tree green (`ruff`, `mypy`, `pytest`, and — WS4 only if DTOs touched,
which they are not — `pnpm` unaffected).

1. **WS1a — Protocols.** Add `ConfigLike` + `GraphRagConfigRepositoryPort` (+ `list_all_ids`)
   to `graphrag_ports.py`; make `GraphRagConfigRepository` explicitly implement the Port
   (add `list_all_ids`). Green: no behavior change, new code unused.
2. **WS1b — inject repo.** Change the three services to accept the Port via `__init__`
   (default `None` -> build the concrete repo internally as a transition shim, so existing
   call sites stay green), then update the 3 production construction sites
   (`app/workers/tasks/graphrag.py:185`, `graphrag_context_provider.py:146`,
   `graphrag_reconciler.py:152`) and the reconciler's 4 inline builds to use the injected
   Port. Then remove the shim default. Update tests to inject the fake via param. Green each
   sub-step.
3. **WS1c — Qdrant prefix.** Add the defaulted `prefix` param + `self._name`; route the 7
   calls. Green (default preserves names).
4. **WS2 — embed helper.** Add `embed_resolution.py` (using `list_ordered_carried`); write the
   WS2 characterization test; swap both call sites; delete both copies. Green (the one
   intended behavior delta is asserted by the new test).
5. **WS3 — evidence rename.** Rename the field + drop coercion + update `EvidenceFetcher`
   signature + the conversation fetcher's boundary parse; keep Cypher keys. Update the WS3
   round-trip test. Green.
6. **WS4a — leak fix.** Add the two facade methods; grow the `delete_agent` commit-then-purge
   tail; add the regression test. Green.
7. **WS4b — orphan sweep.** Add `project_id` to `:Entity` in `apply_triples`; add
   `list_all_ids`; add the sweep to `run_once`; add the sweep test. Green. *(If 4b grows,
   split it into its own task — the leak is already closed by 4a.)*

## 8. Risks and Rollback

- **WS1b reconciler inline-build removal** is the fiddliest (4 sites + 2 monkeypatch tests);
  keep the transition shim until every site is migrated. Rollback: `git revert` the step.
- **WS2 behavior delta** — the worker now excludes revoked-carry keys; if a project relied on
  a revoked-carry key (misconfiguration) builds that "worked" will now fail-loud with no
  usable key. This is correct (SEC-H3) but note it in the changelog. Rollback per step.
- **WS3** touches the shared domain type used by the whole pipeline; the round-trip
  characterization test is the safety net. Neo4j/Qdrant untouched, so no data risk.
- **WS4a** changes a deletion path; ensure the purge runs *after* `db.commit()` (DOM-4) so a
  purge failure never blocks the soft-delete, exactly as RAG does.
- **WS4b** the `project_id`-on-node addition only affects newly-written edges; pre-existing
  orphans without `project_id` cannot be Qdrant-swept — acceptable (Neo4j sweep still runs;
  document the residue), or a one-off backfill can set it. Note in the Deviation Log if run.
- Rollback is `git revert` per milestone; no schema/data migration to unwind (WS4b's Neo4j
  property add is additive and idempotent).

## 9. Acceptance Criteria

- [ ] AC-1: All pre-existing characterization tests pass unmodified except where they
  substituted the repo by poke/monkeypatch (updated to constructor injection) — no other
  behavior change in WS1/WS3. [WS1, WS3]
- [ ] AC-2: `GraphRagBuilder` / `GraphRagRetrieveService` / `ReconciliationLoop` depend only
  on the repo Port + `ConfigLike`; no inline `GraphRagConfigRepository(db)` remains in the
  three services (grep-verified). [WS1]
- [ ] AC-3: `GraphRagVectorStore(client, prefix="knowmap")` routes to `knowmap_{project_id}`;
  the default keeps `graphrag_{project_id}`. [WS1]
- [ ] AC-4: One `embed_resolution` helper is the single source; both former copies are
  deleted; the resolver uses `list_ordered_carried`; a revoked-carry key is not selected by
  the builder path. (SEC-H3 regression test) [WS2]
- [ ] AC-5: Evidence is `evidence_refs: tuple[str, ...]`; no `uuid.UUID` coercion remains in
  extract/retrieve; the Neo4j property key stays `evidence_msg_ids`; conversation evidence
  still resolves end-to-end (round-trip test). No stored-data change. [WS3]
- [ ] AC-6: Deleting an agent purges its Neo4j subgraph + Qdrant points inline (after commit,
  best-effort, `graphrag.infra_purged` audit); the retention CASCADE is no longer the sole
  teardown. (regression test) [WS4a]
- [ ] AC-7: The reconciler purges graph data for config ids absent from Postgres
  (`list_all_ids(include_deleted=True)` diff); `:Entity` nodes carry `project_id`. [WS4b]
- [ ] AC-8: `ruff`/`mypy`/`pytest` green after every milestone; FU-1 (embed drift) closed.

## 10. SRS Delta

None. WS4 realizes existing [R11.20] (added by the parent blueprint); no new or amended
requirements. WS1-3 are structural.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1 (parent) is closed here by WS2.
- FU-A: Physical Neo4j property-key rename `evidence_msg_ids -> evidence_refs` — deferred to
  the document-evidence phase (Q-2); requires a batched, lock-coordinated, snapshot-aware
  one-off Cypher script (no migration harness exists).
- FU-B: If WS4b's `project_id`-on-node backfill for pre-existing edges is wanted (to make
  legacy orphans Qdrant-sweepable), run a one-off `SET n.project_id` script.
- FU-C: Owner-delete teardown hooks for chatroom/workspace/agent_group (WS4 pattern) land
  with their ownership phases (1/2b), reusing the facade method added here.
