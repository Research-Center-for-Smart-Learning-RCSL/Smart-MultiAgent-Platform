---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.20]
---

# F-8: Qdrant-only deletion orphans cannot be discovered by reconciliation

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-8).

## 1. Summary

Config/owner teardown purges Neo4j then Qdrant best-effort, isolating and swallowing each
store's failure and recording the result only as audit-metadata booleans — there is no
durable retry queue. The reconciler's orphan sweep then enumerates candidate config IDs
**exclusively from Neo4j** (`list_config_ids()`, a `MATCH (n:Entity) RETURN DISTINCT
config_id`). So when a teardown deletes the Neo4j subgraph successfully but the Qdrant purge
fails (transient Qdrant outage), the config has zero `:Entity` nodes, is absent from Neo4j
enumeration, and its retained Qdrant points are never rediscovered — a permanent per-tenant
vector leak across both graph products. This contradicts [R11.20], which requires the
reconciler to sweep **the external stores** (plural) for graph ids **no longer present in
Postgres**. The fix adds Qdrant config-id enumeration, unions it with the Neo4j-sourced
candidates against the Postgres live set, and purges both stores for every orphan.

## 2. Observed vs Expected

- **Observed** — teardown (`purge_config_external_stores`,
  `backend/contexts/knowledge/application/graphrag_config_service.py:493-523`) deletes Neo4j
  first (`:511-516`, on failure `_log.exception` and swallow) then Qdrant (`:517-522`, same),
  returning `{"neo4j_purged", "qdrant_purged"}` booleans (`:523`). The knowmap twin mirrors
  it (`backend/contexts/knowledge/application/knowmap_config_service.py:336-353`). Both API
  delete handlers only spread those booleans into audit metadata and never branch on them
  (`backend/app/api/v1/graphrag.py:470-484`; `backend/app/api/v1/knowmap.py:344-355`). The
  orphan sweep enumerates candidates only from Neo4j
  (`graphrag_reconciler.py:210-215`: `graph_configs = await self._neo4j.list_config_ids()`),
  backed by `MATCH (n:Entity) RETURN DISTINCT n.graphrag_config_id, n.project_id`
  (`backend/contexts/knowledge/infrastructure/neo4j_driver.py:238-246`); a config with no
  `:Entity` nodes produces zero rows. The sweep's own docstring states a purged config "is
  absent from `list_config_ids` and is never revisited" (`graphrag_reconciler.py:191-193`).
  There is no durable teardown-failure record anywhere (no DB table, Redis set, or Arq job —
  only immutable audit rows).
- **Expected** — [R11.20]: "A reconciler sweeps **the external stores** for graph ids **no
  longer present in Postgres**." Postgres is the authoritative live-set; the sweep must
  discover orphans in *each* external store relative to that set, so a store whose sibling's
  delete already succeeded is still swept. A Qdrant-only orphan must be discoverable.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Multi-store enumeration vs a durable teardown-failure queue? | **Multi-store enumeration** — add Qdrant config-id enumeration and union it with the Neo4j candidates; compare against the Postgres live set; purge both stores. | Matches [R11.20]'s "sweeps the external stores… no longer present in Postgres" literally and catches orphans however they arose (transient failure *or* a hard crash between the two deletes, which a failure-time dead-letter would miss). A durable queue is a narrower, complementary hardening recorded as FU-1. |
| Q-2 | Enumeration cost of scrolling Qdrant every sweep? | Paginate with `scroll`, `with_payload=["config_id"]`, `with_vectors=False`, collect distinct `config_id`s; accept the cost at the reconciler cadence and bound it. | The payload key `config_id` is already written on every point (`graphrag_vector_store.py:133-138`) and filtered on by `delete_by_config` (`:302-312`), so distinct-config enumeration needs no schema change. Cost scales with points-per-project; noted as a risk (§9) with a sampling/bounded-scroll FU if it proves heavy. |

## 4. Reproduction

Preconditions: a project with a GraphRAG (or Knowledge Map) config whose graph is built into
both Neo4j and Qdrant; a Qdrant that will fail the purge (transient outage), modeled with a
vector-store stub whose `delete_by_config` raises.

1. Delete the config; teardown runs (`graphrag_config_service.py:493-523`): Neo4j
   `delete_all` succeeds (`neo4j_purged=True`), Qdrant `delete_by_config` raises and is
   swallowed (`qdrant_purged=False`); the handler audits the booleans and returns 200.
2. Run the reconciler orphan sweep: `list_config_ids()` (`:211`) returns no row for the
   config (its `:Entity` nodes are gone), so it is never a candidate.
3. Confirm the Qdrant points for `config_id` remain forever; no sweep, no retry, no audit of
   the leak.

Deterministic under the stub.

## 5. Root Cause Analysis

The causal chain:

1. Teardown swallows the per-store failure and records it only as an audit boolean with no
   durable, machine-readable retry signal (`graphrag_config_service.py:515-522`;
   `knowmap_config_service.py:340-348`; handlers `graphrag.py:470-484`, `knowmap.py:344-355`).
2. The orphan sweep's sole discovery index is Neo4j `list_config_ids()`
   (`graphrag_reconciler.py:211`, `neo4j_driver.py:238-246`). **This is the root cause** —
   the earliest link whose correction (enumerate candidates from every external store, not
   just Neo4j) makes the Qdrant-only orphan discoverable regardless of how it arose. Once
   the discovery index covers Qdrant, the existing per-candidate purge (`:225-248`) already
   cleans both stores.
3. The absence of a durable retry queue (confirmed: none) is an aggravating factor — with
   multi-store enumeration the sweep itself becomes the durable recovery mechanism, so a
   queue is optional hardening, not required.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — unbounded Qdrant vector storage growth and per-tenant vector data
  persisting indefinitely after config/owner deletion, across both Concept Maps and
  Knowledge Maps (both route teardown through the same primitive and the same Neo4j-only
  sweep).
- **Sibling suspects:**
  - **Knowmap teardown twin (`knowmap_config_service.py:336-353`) — confirmed, same fix.**
    It shares the swallow-and-report pattern and is swept by the same reconciler consumer
    fan-out (`graphrag_reconciler.py:234-248`); the added Qdrant enumeration must cover both
    the `graphrag_*` and `knowmap_*` collection prefixes.
  - **Neo4j-only orphan (inverse leak) — cleared.** A teardown where Qdrant succeeds but
    Neo4j fails leaves Neo4j data, which *is* enumerated by `list_config_ids()` and already
    swept; only the Qdrant-only direction is blind. The fix does not regress this.
  - **F-24 File-RAG tenancy leak — distinct, separate finding.** F-24 is the File-RAG blob/
    per-project-collection leak on tenant deletion with no sweep at all; it is a different
    store surface and a separate release-blocker spec. Not addressed here.
  - **Sweep runs only when `_sweep_consumers` is non-empty (`run_once :176-177`); only the
    graphrag worker loop registers consumers (`backend/app/workers/graphrag_reconciler.py:173-202`).**
    Confirm the Qdrant enumeration is wired into that same graphrag loop (which already
    fans out across both consumers' vector stores), not the knowmap loop.

## 7. Fix Design

1. **Add Qdrant config-id enumeration to the vector-store wrapper.** Add
   `list_config_ids(project_id) -> set[UUID]` (and/or a cross-collection variant) to
   `backend/contexts/knowledge/infrastructure/graphrag_vector_store.py`. It uses the raw
   `AsyncQdrantClient` (`self._client`, `:53-54`) — `get_collections()` to enumerate existing
   `{prefix}_{project_id}` collections (prefix known per instance, `_name :57-58`) and
   `scroll(..., with_payload=["config_id"], with_vectors=False)` paginated to collect
   distinct `config_id` payload values (payload written at `:133-138`). No new payload field
   is required.
2. **Union the candidate set in the orphan sweep.** In
   `graphrag_reconciler.py:202-218`, build candidates as the union of the current Neo4j
   `list_config_ids()` (`:211`) and the Qdrant-enumerated config ids across every registered
   sweep consumer's vector store (both `graphrag_*` and `knowmap_*` prefixes). Keep the
   existing `live_ids` derivation from Postgres (`:206-208`) as the authoritative live set;
   an orphan is any candidate not in `live_ids`. The per-orphan purge (`:223-248`), owner
   attribution (`:218-223`, `owned` from `:209`), audit (`:249-261`), and per-orphan failure
   isolation (`:263-265`) are unchanged.
3. **Keep enumeration failures non-fatal and isolated.** Wrap the Qdrant enumeration like the
   existing Neo4j enumeration (`:210-214`): on failure, log and fall back to the Neo4j-only
   candidate set for that cycle rather than aborting the whole sweep, so a Qdrant outage
   degrades to today's behavior instead of stopping all reconciliation.

Teardown itself (the swallow-and-report primitive) is intentionally left as-is: with the
sweep now covering Qdrant, the durable recovery path exists without changing the request-path
contract. A complementary durable dead-letter is FU-1.

**Data repair:** existing Qdrant-only orphans (already stranded before this fix) are
discovered and purged automatically on the first sweep after deploy, because enumeration now
reads them directly from Qdrant. No migration.

## 8. Regression Test Plan

Unit test in `backend/tests/unit/test_graphrag_reconciler.py`:

1. **Qdrant-only orphan is swept** (primary red-first test): stub the Neo4j `list_config_ids`
   to return nothing and a vector store whose new `list_config_ids` returns a `config_id`
   that is not in the Postgres live set; run the orphan sweep; assert `delete_by_config` is
   invoked for that `config_id` on the vector store and a `graphrag.orphan_swept` audit is
   emitted. Fails today — the candidate set is Neo4j-only, so nothing is swept.
2. **Live config not swept** (guard): a `config_id` present in both the Qdrant enumeration and
   the Postgres live set is *not* purged.
3. **Qdrant enumeration failure degrades gracefully** (guard): a vector-store stub whose
   `list_config_ids` raises still lets the Neo4j-sourced candidates sweep and does not abort
   the cycle.

Add a focused unit test for the new `graphrag_vector_store.list_config_ids` against a fake
Qdrant client asserting it collects distinct `config_id`s across scroll pages.

## 9. Risks and Rollback

- **Enumeration cost** — scrolling Qdrant each sweep scales with points-per-project; on large
  tenants this adds latency. Mitigated by `with_vectors=False` + payload-only scroll and
  pagination; a bounded/sampled scroll is FU-2 if it proves heavy.
- **Over-deletion** — a bug in candidate/live-set comparison could purge a live config.
  Mitigated by keeping Postgres `live_ids` as the sole authoritative live set (unchanged) and
  the guard test (§8.2).
- **Cross-prefix scope** — the enumeration must cover both `graphrag_*` and `knowmap_*`
  collections; missing one re-opens the leak for that product. Covered by wiring across all
  sweep consumers (§7.2) and the knowmap sibling note (§6).
- **Rollback** — revert the sweep to Neo4j-only enumeration and drop the wrapper method;
  code-only, no schema change.

## 10. Acceptance Criteria

- [ ] AC-1: The Qdrant-only-orphan regression test (§8.1) fails before the fix and passes
  after.
- [ ] AC-2: The orphan sweep discovers and purges a config whose Neo4j subgraph is absent but
  whose Qdrant points remain, for both `graphrag_*` and `knowmap_*` collections.
- [ ] AC-3: A config present in the Postgres live set is never purged, regardless of store
  enumeration (§8.2).
- [ ] AC-4: A Qdrant enumeration failure logs and falls back to Neo4j-only candidates without
  aborting the sweep (§8.3).
- [ ] AC-5: `graphrag_vector_store.list_config_ids` returns the distinct set of `config_id`s
  present in a project's collection (unit-tested).
- [ ] AC-6: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`.

## 11. SRS Delta

None. This restores [R11.20]'s "sweeps the external stores for graph ids no longer present in
Postgres" — the code currently sweeps only the Neo4j-keyed store.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (durable teardown dead-letter):** as complementary hardening, persist a durable
  record on any per-store purge failure and drain it in the sweep, so a failed store is
  retried promptly rather than waiting for the next full enumeration.
- **FU-2 (bounded enumeration):** if per-sweep Qdrant scroll cost is significant on large
  tenants, bound or sample it (e.g. enumerate only when the Neo4j and live-set counts
  diverge, or on a slower cadence than the heal loop).
- **FU-3 (F-24 File-RAG leak):** File RAG blobs and per-project collections leak on tenancy
  deletion with no sweep at all — separate release-blocker finding (F-24), not covered here.
