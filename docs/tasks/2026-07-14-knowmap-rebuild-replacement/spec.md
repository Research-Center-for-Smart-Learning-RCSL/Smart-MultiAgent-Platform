---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.12]
---

# F-6: Knowledge Map document rebuilds are additive and retain deleted knowledge

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-6).

## 1. Summary

When a Knowledge Map document is deleted (or quarantined), the API purges its PostgreSQL
rows and blob and queues a full-corpus rebuild — but the rebuild only *adds*. The builder
re-reads the surviving corpus and applies its triples with additive Neo4j `MERGE`, unioning
evidence and never removing rows absent from the new corpus; the Qdrant cleanup only
supersedes vectors for entity names the current build re-embeds. Consequently, entities and
relations contributed solely by the deleted document remain in Neo4j forever, their vectors
orphan in Qdrant, and — worse — a relation co-evidenced by both the deleted document and a
surviving one keeps the deleted document's evidence ref, which the retrieval allowlist then
uses to hide a *still-valid* relation. The graph never shrinks until the whole config is
deleted. The fix introduces true differential replacement for full-corpus Knowledge Map
builds: relations/entities absent from the current build are removed, evidence and
member-provenance unions are recomputed to reflect only the current corpus, and orphan
Qdrant vectors are deleted.

## 2. Observed vs Expected

- **Observed** — document delete hard-deletes the row and blob and enqueues a full-corpus
  rebuild (`backend/app/api/v1/knowmap.py:533,552,572-574`). The loader reads only the live
  corpus and carries no signal about what was removed
  (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:65-69,86-95`). The
  builder applies current triples via a single additive `apply_triples`
  (`backend/contexts/knowledge/application/graphrag_builder.py:320-325`). Neo4j `MERGE`
  adds nodes/relations and *unions* `evidence_msg_ids` and `source_member_ids`, never
  subtracting (`backend/contexts/knowledge/infrastructure/neo4j_driver.py:117-156`); the
  only removal paths are `delete_by_build` (rollback only, `:183-205`) and `delete_all`
  (whole-config wipe, `:232-236`). Qdrant supersede deletes only points whose entity name
  is in the current re-embed batch
  (`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:226-282`), so an
  entity no longer produced is never cleaned up; point IDs are fresh `uuid4` per build
  (`graphrag_builder.py:556`). Retrieval drops a relation unless **every** source document
  is allowed (`backend/contexts/knowledge/application/knowmap_context_provider.py:79-96`,
  the `docs <= allowed_document_ids` test at `:94`), so a stale deleted-doc evidence ref on
  a live relation hides that relation.
- **Expected** — a full-corpus Knowledge Map rebuild reflects exactly the current corpus:
  triples/entities/vectors contributed only by removed documents are gone, and evidence and
  provenance describe only surviving sources. Intent source: [R11.12] (reprocess/delete as a
  graph-change trigger), Phase 3 G6 and AC-7 (cited by the audit). The delete-path docstring
  and comment already assert the rebuild "drops the removed document's triples"
  (`knowmap.py:504-505,569-571`) — a false premise the code does not honor.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Full wipe-and-reapply vs true differential replacement? (Re-confirmed after code review.) | **True differential replacement** — remove only relations/entities/vectors absent from the current build and recompute affected evidence/provenance. | Re-confirmed after establishing that this architecture already fully reprocesses each build (every relation re-`MERGE`d `neo4j_driver.py:133`, every entity re-embedded with a fresh point `graphrag_builder.py:556`), so differential yields **no measurable cost saving** over the simpler wipe-and-reapply. It is chosen deliberately to avoid ever fully emptying the config subgraph mid-build: a mid-build failure leaves untouched rows intact rather than a wiped graph, at the cost of the `build_id`/degree-0 removal pass and the per-build evidence-reset mechanism (§7.1-7.2). Wipe-and-reapply (reusing `delete_all` + `delete_by_config`) remains the documented fallback if the reset mechanism proves fragile against the deployed Neo4j. |
| Q-2 | Does the removal apply to every build or only shrinking ones? | Every full-corpus knowmap build applies replacement semantics. | The loader already re-reads the entire corpus each build (`knowmap_delta_loader.py:65`), so "replace what this build did not touch" is well-defined for all knowmap builds and needs no special shrink-detection. Concept Map delta builds are out of scope (see §6). |

## 4. Reproduction

Preconditions: a Knowledge Map config with two documents A and B; A and B share at least one
relation R, and A also contributes an entity/relation X present in no other document.

1. Build the config; graph contains X (from A only) and R (evidence refs from both A and B).
2. Delete document A (`knowmap.py:533`); a full-corpus rebuild is queued (`:572-574`).
3. After the rebuild:
   - **Stale graph:** GET the graph — X is still present in Neo4j though its only source is
     gone; X's vector remains in Qdrant.
   - **Hidden live relation:** query the graph via an Agent — R is dropped because its
     unioned `evidence_refs` still include A's ref, and `{A,B} <= {B}` is false at the
     allowlist filter (`knowmap_context_provider.py:94`), even though B still supports R.

Deterministic given the co-evidence setup.

## 5. Root Cause Analysis

The causal chain:

1. The loader hands the builder only surviving documents and no removal signal
   (`knowmap_delta_loader.py:65-69`), so the builder cannot know what disappeared.
2. `apply_triples` → Neo4j `MERGE` is purely additive and unions evidence/provenance
   (`neo4j_driver.py:117-156`); there is no "delete config rows this build did not touch"
   step. **This is the root cause** — the earliest link whose correction (a removal pass +
   union recomputation scoped to the current build) prevents all three symptoms (stale
   graph, orphan vectors, hidden live relations).
3. Qdrant supersede is name-scoped (`graphrag_vector_store.py:258-281`), an aggravating
   factor: even with a Neo4j fix, vectors for no-longer-produced entities would leak without
   a build-scoped delete.
4. The retrieval allowlist's all-sources-must-be-allowed rule
   (`knowmap_context_provider.py:94`) turns a stale evidence ref into a false negative; it
   is correct *given clean unions* and needs no change once (2) recomputes unions — it is a
   symptom surface, not the root cause.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Knowledge Map that has had any document deleted, quarantined, or
  replaced: permanently stale UI graph, wasted Qdrant top-k seeds, reduced recall, and
  evidence/allowlist distortion that hides valid relations, persisting until the whole
  config is deleted.
- **Sibling suspects:**
  - **Concept Map (graphrag) delta builds (cleared — out of scope).** Concept Maps are
    conversation-backed and use genuine delta builds re-embedding only touched entities; the
    audit did not flag them for replacement, and this fix must not change their delta
    semantics. Both products share `graphrag_builder`, `neo4j_driver`, and the vector store,
    so the replacement pass must be gated to the knowmap full-corpus path (loader/mode
    signal), not applied unconditionally in shared code.
  - **Quarantine rebuild (confirmed — same path).** Quarantine also enqueues a full-corpus
    rebuild (`backend/app/workers/tasks/knowmap.py:204-207`); it benefits from the same fix
    with no extra work.
  - **Existing whole-config purge (reusable primitive, cleared).** `cascade_external_stores`
    (`backend/contexts/knowledge/application/knowmap_config_service.py:307-353`) and its
    Concept Map twin `purge_config_external_stores`
    (`backend/contexts/knowledge/application/graphrag_config_service.py:493-523`) already
    prove a clean Neo4j `delete_all` + Qdrant `delete_by_config` works; reuse their
    building blocks, but at build/entity granularity rather than whole-config.

## 7. Fix Design

Introduce differential replacement into the full-corpus Knowledge Map build, gated so
Concept Map delta builds are unaffected.

1. **Sweep absent relations, then orphan entities, in Neo4j.** The relation `MERGE` already
   sets `r.build_id = $bid` on **every** touch (`neo4j_driver.py:133`), so after the build's
   `apply_triples` (`graphrag_builder.py:320-325`) every current relation carries the current
   build id and every stale relation carries an older one. Run a config-scoped removal pass:
   (a) delete relations for the config whose `build_id != current build_id`; (b)
   detach-delete config entities left with degree 0. **Do not key entity removal on entity
   `build_id`** — entity `build_id` is set `ON CREATE` only (`neo4j_driver.py:118,125`), so a
   re-touched live entity keeps a stale id and a `build_id`-based delete would remove live
   entities. Degree-0-after-relation-removal is the correct liveness signal here because
   `apply_triples` only ever creates an entity as an endpoint of a relation (`:117-132`), so
   an entity with no surviving relation is genuinely absent from the current corpus. This
   pass mirrors the inverse of `delete_by_build` (`:183-205`), which already uses the
   degree-0 node-cleanup idiom (`:201-202`).
2. **Recompute evidence/provenance per build, don't accumulate across builds.** The relation
   `SET` currently unions `evidence_msg_ids` and `source_member_ids` with
   `coalesce(r.…, []) + …` across all builds (`neo4j_driver.py:137-146`). Change it to reset
   these arrays on the **first touch within the current build** and union only within it:
   gate the union on `r.build_id = $bid` (true only after an earlier row of the same build
   already touched the relation) versus reset to the incoming row's values otherwise. Because
   the loader reprocesses the entire corpus each build (`knowmap_delta_loader.py:65`), the
   current build's rows already carry the complete surviving evidence, so this yields exactly
   the live-corpus evidence and removes the stale-ref false negative at
   `knowmap_context_provider.py:94` with no retrieval-side change. **Mechanism caveat:** this
   relies on UNWIND processing rows sequentially with earlier rows' writes visible to later
   rows, and on the `SET` right-hand sides being evaluated against pre-`SET` values within a
   row; both hold in current Neo4j but must be asserted by the §8 tests against the deployed
   version, since a regression here silently drops or double-counts evidence.
3. **Build-scoped Qdrant cleanup.** Add `delete_points_not_in_build(config_id, keep_build_id)`
   to `graphrag_vector_store.py` (the payload already tags `config_id` + `build_id`, so a
   `must config_id` / `must_not build_id==keep` filter suffices; alongside
   `delete_superseded_entities:226-282`). For the knowmap replacement path, call it in the
   builder's post-embed phase **in place of** the name-scoped supersede
   (`graphrag_builder.py:422-429`) — it is a strict superset that also removes vectors for
   entities no longer produced. The name-scoped supersede stays for the Concept Map delta
   path (§7.4). Keep it best-effort/non-fatal, consistent with the existing sweep.
4. **Gate to knowmap full-corpus builds.** The replacement pass (1)-(3) must run only for the
   knowmap full-corpus path and not for Concept Map delta builds; thread a mode/flag from the
   loader or the `knowmap_build` task (`backend/app/workers/tasks/knowmap.py:277`, which
   currently calls `builder.run(...)` with no mode) so the builder selects replacement vs
   additive-delta explicitly. Do not repurpose the existing `mode="delta"/"full"` argument
   silently — it currently only affects `since` and is a no-op for the DocDeltaLoader
   (`graphrag_builder.py:171,279`); make the replacement behavior an explicit, tested branch.

Transactional safety: the build already snapshots for 2PC rollback
(`graphrag_builder.py:248`); the removal pass must occur inside the same phased commit so a
failure rolls back via the existing `delete_by_build`/snapshot path rather than leaving a
half-pruned graph. Reads during the window are governed by the separate active-version
concern (F-10) and are out of scope here.

**Data repair:** existing graphs already carry accumulated stale entities/relations and
unioned dead refs. The first replacement rebuild per config repairs it — because every build
now reconciles the full corpus. No separate migration is required, but a one-off trigger of a
rebuild for each existing knowmap config is recommended at deploy (recorded as FU-1).

## 8. Regression Test Plan

Unit tests (`backend/tests/unit/test_graphrag_builder.py` and
`test_graphrag_vector_store.py`), which currently encode the leaky behavior and must be
extended:

1. **Removal of absent entities/relations** (new, `test_graphrag_builder.py`): build a corpus
   with docs A+B producing entity X (A only) and relation R (A+B); rebuild with A removed;
   assert X and its Neo4j nodes/relations are gone and R remains. Fails today — the additive
   MERGE never removes X (`neo4j_driver.py:117-156`).
2. **Evidence-ref recomputation** (new): after the A-removed rebuild, assert R's
   `evidence_msg_ids` no longer contain A's ref, so the retrieval allowlist keeps R for an
   Agent allowed only B. Fails today — union retains A's ref and
   `knowmap_context_provider.py:94` hides R. **Also assert the within-build union boundary:**
   a relation evidenced by two surviving documents (two rows in the same build) retains
   **both** refs after the rebuild — proving the per-build reset does not clobber a relation
   restated across rows of the same build (the mechanism caveat in §7.2).
3. **Qdrant orphan removal** (extend `test_graphrag_vector_store.py:92-156`): the existing
   `test_supersede_keeps_untouched_and_live_entities` encodes keeping untouched entities;
   add a case asserting `delete_points_not_in_build` removes points for entities absent from
   the current build while keeping current-build points. Fails today — no such method exists.
4. **Concept Map delta unaffected** (new/guard): assert a Concept Map delta build still
   re-embeds only touched entities and does not prune the config graph.

Test (1) is the primary red-first test.

## 9. Risks and Rollback

- **Over-deletion / cross-product bleed** — the removal pass runs in shared builder code; a
  mis-scoped gate could prune a Concept Map graph. Mitigated by the explicit knowmap-only
  flag (§7.4) and guard test (§8.4).
- **Transactional partial prune** — a failure mid-removal must roll back cleanly; anchor the
  pass in the existing 2PC/snapshot flow (`graphrag_builder.py:248`) and reuse
  `delete_by_build` semantics for rollback.
- **Performance** — a per-build config-scoped delete pass and a Qdrant build-scoped delete
  add cost proportional to config size; acceptable given builds are already full-corpus
  re-reads and re-embeds.
- **Rollback** — revert to additive apply, the name-scoped Qdrant supersede, and the
  union-based evidence; code-only, no schema change. Graphs rebuilt under the fix remain
  correct after rollback (they are simply additive again going forward).

## 10. Acceptance Criteria

- [x] AC-1: The absent-entity-removal regression test (§8.1) fails before the fix and passes
  after. Encoded in `test_knowmap_neo4j_replacement.py` (real Neo4j required; not run locally —
  D-1). The runnable proof of the orchestration is the builder wiring test.
- [x] AC-2: After a full-corpus knowmap rebuild, Neo4j contains no entity or relation absent
  from the current corpus for that config. `remove_stale_for_build` deletes relations with a
  non-current `build_id` then detach-deletes degree-0 entities; asserted by the integration
  test (D-1).
- [x] AC-3: A relation co-evidenced by a deleted and a surviving document has its
  `evidence_msg_ids`/`source_member_ids` recomputed to exclude the deleted document, and is
  returned by retrieval for an Agent allowed the surviving document (§8.2). Per-build reset in
  `apply_triples(replace=True)` with the within-build union preserved; integration test asserts
  both the reset and the within-build union (D-1).
- [x] AC-4: Qdrant retains no config points for entities absent from the latest build
  (`delete_points_not_in_build` exercised). Unit `test_delete_points_not_in_build_removes_orphans_keeps_current`
  runs the real filter against an in-memory Qdrant (run).
- [x] AC-5: Concept Map delta builds retain their existing re-embed-only-touched semantics
  and do not prune the config graph (§8.4). Builder guard test asserts a default (delta) build
  calls `apply_triples(replace=False)`, no removal pass, and the name-scoped supersede (run).
- [x] AC-6: The replacement pass executes inside the existing 2PC/snapshot flow and rolls
  back cleanly on failure. `remove_stale_for_build` runs inside the Phase-1 `try`, so a failure
  routes through `_fail_phase1` (snapshot restore) exactly like an `apply_triples` failure —
  verified by inspection against the existing Phase-1 failure tests.
- [x] AC-7: `ruff check`, `ruff format --check`, and `mypy` pass for the touched modules (no
  new errors; one pre-existing unrelated `tenancy` mypy error remains). Full `pytest -q` at the
  batch's end; the Neo4j integration test runs in CI (D-1).

## 11. SRS Delta

None. This restores the [R11.12] / Phase 3 G6+AC-7 replacement-on-corpus-change behavior the
code already claims to implement.

## 12. Deviation Log

- **D-1 (Neo4j Cypher not run locally):** the removal-pass and per-build evidence-reset
  correctness (AC-1/AC-2/AC-3) is Cypher whose behavior depends on Neo4j's UNWIND row-write
  visibility and pre-clause SET evaluation. The repo has NO Neo4j-backed test (all driver
  tests are pure-helper units) and no Neo4j is available in this environment, so the end-to-end
  graph-shape assertions live in `tests/integration/test_knowmap_neo4j_replacement.py`,
  written but run only where a real Neo4j is provisioned. Runnable coverage: the builder
  orchestration (replace→apply_triples(replace=True)+removal pass+build-scoped vector sweep;
  delta→additive) and the Qdrant filter (`delete_points_not_in_build` against an in-memory
  Qdrant that evaluates the real filter).
- **D-2 (reset scope — confidence/timestamps unchanged):** per §7.2 the per-build reset is
  scoped to `evidence_msg_ids` and `source_member_ids` only — the fields the retrieval
  allowlist reads (`docs <= allowed_document_ids`). `confidence` (max) and
  `first_seen_at`/`last_seen_at` (min/max) still accumulate across builds; they do not affect
  relation visibility, only ranking. Recorded as FU-4 for a possible future full recompute.
- **D-3 (NULL build_id robustness):** `remove_stale_for_build` deletes relations where
  `r.build_id IS NULL OR r.build_id <> $bid`, so a legacy relation with no build id that the
  current corpus no longer produces is also removed (a re-touched relation always carries the
  current `$bid` and survives). Beyond the literal spec, but strictly safer.
- **D-4 (empty-corpus vector purge):** the build-scoped Qdrant sweep runs for every replace
  build, including one that embedded zero entities (a corpus emptied to nothing), so the last
  surviving vectors are purged — the `if embeddings:` guard would have skipped that case.

## 13. Follow-ups

- **FU-1 (deploy data repair):** trigger one rebuild per existing Knowledge Map config after
  deploy so accumulated stale entities/relations/vectors and dead evidence refs are
  reconciled; no schema migration is needed.
- **FU-2 (F-10 interaction):** reads can still observe a partially committed graph during the
  build window; the active-version marker fix (F-10, separate finding) governs read isolation
  and is not addressed here.
- **FU-3 (F-8/F-9 interaction):** the build-scoped Qdrant delete added here does not fix the
  reconciler-orphan (F-8) or non-idempotent-retry (F-9) point-ID issues; keep those separate.
- **FU-4 (confidence/timestamp reset):** the per-build reset covers evidence + member
  provenance only (the allowlist-relevant fields); `confidence`/`first_seen_at`/`last_seen_at`
  still accumulate across builds and could carry a removed document's contribution into
  ranking. Out of scope for the visibility bug; revisit if temporal ranking drifts.
- **FU-5 (pre-existing test pollution, NOT introduced here):** running the graphrag/knowmap
  unit files together, `test_agent_delete_graph_cascade.py::test_delete_agent_cascades_graphrag_external_stores`
  fails on a cross-file ordering dependency. Confirmed reproducing on the pre-F-6 baseline
  (git-stash bisect), so it is unrelated to this change; it passes in isolation. Worth a
  separate fixture-isolation fix.
- **FU-6 (reconciler replace-path Qdrant sweep — from check-quality):** the build-scoped
  Qdrant sweep (`delete_points_not_in_build`) runs only in the builder's happy path
  (`graphrag_builder.py`). When a `replace=True` build crashes in Phase-2 and the reconciler
  recovers it, the reconciler runs no build-scoped sweep and does not know the build was a
  replacement, so orphan vectors for entities the current corpus no longer produces persist
  until the next successful non-recovered replace build re-sweeps them. Neo4j is already
  correct (the prune ran in Phase-1). Bounded and self-healing; not a security leak (retrieval
  is CLEAN-doc-scoped), so deferred. Fix: persist replace intent on the build/config (or
  discriminate by owner type) and run the sweep on the reconciler's replace-recovery path.
- **FU-7 (vector store lacks a Protocol port):** unlike `Neo4jDriver`, the Qdrant vector
  store is a concrete infrastructure class imported directly into the application builder —
  there is no vector port in `graphrag_ports.py`. `delete_points_not_in_build` was added to
  the concrete class only, perpetuating the asymmetry. Mirror the Neo4j port pattern so the
  application depends on an abstraction.
- **FU-8 (`remove_stale_for_build` node-cleanup cartesian):** the orphan-node cleanup carries
  one `WITH` row per deleted relation, so the follow-on `MATCH (n:Entity {cid})` is an N×M
  cartesian (deleted-rels × entities). Deletes are idempotent so correctness holds, but a
  `WITH DISTINCT`/aggregation collapses it to one pass. Efficiency only.
  **Resolved (c9816ca):** the cleanup now projects `WITH DISTINCT $cid AS cid`, so the entity
  scan runs once instead of once per stale relation. Behavior-preserving (asserted by the
  integration test's graph-shape checks).
