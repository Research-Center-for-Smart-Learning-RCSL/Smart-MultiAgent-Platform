---
type: bugfix
status: approved
created: 2026-07-17
requirements: [R11.04, R11.12]
---

# Knowledge Map replacement splits one Neo4j phase across two commits

## 1. Summary

This dossier remediates F-2 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md`.
A full-corpus Knowledge Map replacement commits current triples and stale-row pruning in
separate Neo4j sessions. If pruning fails, the config becomes readable `FAILED` while the
graph contains a partially replaced mixture (`backend/contexts/knowledge/application/graphrag_builder.py:359-376`).

- **Goal:** make full replacement one atomic Neo4j Phase-1 transaction, including an empty
  corpus.
- **Non-goals:** change Concept Map delta semantics, redesign graph versioning, or repair the
  separate best-effort Qdrant supersede sweep.

## 2. Observed vs Expected

- **Observed:** the builder calls `apply_triples` and `remove_stale_for_build` sequentially
  (`backend/contexts/knowledge/application/graphrag_builder.py:359-376`); each driver method
  opens its own auto-commit session
  (`backend/contexts/knowledge/infrastructure/neo4j_driver.py:204-211,276-277`). A prune
  exception calls `_fail_phase1`, which deletes the snapshot/pointer and records `FAILED`
  (`backend/contexts/knowledge/application/graphrag_builder.py:377-386`). `FAILED` remains
  readable: the retrieval guard skips only `IN_FLIGHT_BUILD_STATES`
  (`backend/contexts/knowledge/application/graphrag_retrieve.py:112`).
- **Expected:** [R11.04] and §11.2a define Phase 1 as one Neo4j transaction ("Open a Neo4j
  transaction, upsert all nodes/edges ... Commit") and state that a Phase-1 failure commits
  nothing (`REQUIREMENTS.md:507,515,517`). [R11.12] requires a rebuild to reflect the current
  document corpus (`REQUIREMENTS.md:542`).
- **Contradicting in-code claim:** `graphrag_builder.py:366-375` documents the two-transaction
  split as a deliberate, safe trade-off, arguing the half-pruned graph "self-heals" on the next
  successful replace build. That argument holds only while a later successful build is
  guaranteed; in the window before one runs, the graph keeps serving relations sourced from
  deleted or quarantined documents, and §11.2a step 4 admits no such window. The comment is
  therefore wrong, not merely stale, and this task must rewrite it (see §7.6).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Compensate after a prune failure or prevent the partial commit? | Prevent it: execute upsert/evidence reset and stale prune in one managed Neo4j transaction. | Compensation is multi-call and can fail; the SRS explicitly defines Phase 1 as a single transaction (`REQUIREMENTS.md:463-466`). |
| Q-2 | Keep the separate `remove_stale_for_build` port? | Collapse replacement behind one atomic port operation; retain only private query helpers needed by the adapter. | Exposing two public operations lets application orchestration split the invariant again (`backend/contexts/knowledge/application/graphrag_ports.py:118-155`). |

## 4. Reproduction

1. Build a Knowledge Map containing relation A.
2. Change the corpus so the next build produces relation B and should remove A.
3. Let `apply_triples(replace=True)` commit, then force `remove_stale_for_build` to fail.
4. Observe `FAILED`, deleted recovery material, and a readable graph containing B plus A.

The successful replacement integration covers only the no-failure path
(`backend/tests/integration/test_knowmap_neo4j_replacement.py:42-95`).

## 5. Root Cause Analysis

One logical external-store phase was modeled as two independent port calls. The adapter
therefore commits the first mutation before beginning the second. The builder's Phase-1
failure path assumes Neo4j committed nothing and discards recovery material, so the
abstraction mismatch converts an adapter error into durable partial state. The earliest
correction is to make the port operation match the atomic invariant.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** all full-corpus Knowledge Map replacement builds, including document
  delete/quarantine/reprocess and empty-corpus rebuilds
  (`backend/app/workers/tasks/knowmap.py:402-409`).
- **Cleared:** Concept Map builds pass `replace=False` and do not prune
  (`backend/tests/unit/test_graphrag_builder.py:466-470`).
- **Confirmed test gap:** `FakeNeo4j` offers `raise_on_apply` but implements
  `remove_stale_for_build` as an unconditionally-succeeding recorder, so no existing test can
  fail the second step (`backend/tests/unit/test_graphrag_builder.py:231-264`). The atomic
  fake needs a new failure hook.
- **Port collapse is cheap (Q-2):** `remove_stale_for_build` has exactly five references —
  the adapter (`neo4j_driver.py:238`), the Protocol (`graphrag_ports.py:146`), the single
  builder call site (`graphrag_builder.py:376`), the integration helper
  (`tests/integration/test_knowmap_neo4j_replacement.py:46`), and the unit fake
  (`tests/unit/test_graphrag_builder.py:263`). No other context or worker calls it.
- **Existing debt:** the post-Qdrant replacement vector sweep is best-effort
  (`backend/contexts/knowledge/application/graphrag_builder.py:425-457`); it is not the
  causal path for readable partial Neo4j state and remains out of scope.

## 7. Fix Design

1. Add an infrastructure-owned atomic replacement operation to the `Neo4jDriver` port.
   Execute the current triple upsert/evidence reset and stale relation/orphan entity prune
   inside one managed transaction callback.
2. Refactor the existing parameterized Cypher into private helpers shared by delta apply and
   atomic replacement; do not concatenate user-controlled values into Cypher.
3. Ensure a zero-triple replacement still opens the transaction and runs the prune. The
   current early return at `backend/contexts/knowledge/infrastructure/neo4j_driver.py:103-104`
   must not bypass empty-corpus deletion.
4. Make `GraphRagBuilder` call the atomic operation only for `replace=True`; keep the delta
   apply path unchanged. Qdrant Phase 2 starts only after the Neo4j transaction commits.
5. Existing partially replaced configurations require one successful full replacement build
   after deployment; no database migration is required.
6. Rewrite the `graphrag_builder.py:366-375` comment. It currently asserts the partial state
   is safe; after this change the guarantee is a single transaction, and leaving the old text
   would contradict the code.

Reuse the current evidence-reset Cypher
(`backend/contexts/knowledge/infrastructure/neo4j_driver.py:131-188`), prune Cypher
(`backend/contexts/knowledge/infrastructure/neo4j_driver.py:262-275`), builder state/audit
machinery, and real-Neo4j test fixture.

**No transaction exemplar exists.** All eleven `Neo4jAsyncDriver` methods use auto-commit
`session.run`, and no `execute_write` / `begin_transaction` call exists anywhere in `backend/`.
The atomic operation introduces the first managed-transaction usage in this codebase, so the
Cypher is reused but the transaction handling is new code with no in-repo pattern to copy.
`neo4j==5.24.0` (`backend/pyproject.toml:38`) provides `AsyncSession.execute_write`.

### Security Considerations

All values remain bound Cypher parameters. Atomic rollback prevents deleted or quarantined
facts from remaining visible to authorized project members and Agents. Project/config labels
and per-Agent evidence allowlists must remain unchanged.

## 8. Regression Test Plan

1. Extend `backend/tests/integration/test_knowmap_neo4j_replacement.py` with a forced
   second-step failure and assert the exact pre-build graph remains after transaction
   rollback.
2. Extend `backend/tests/unit/test_graphrag_builder.py` with an atomic-replacement fake that
   raises before commit; assert `FAILED`, no Qdrant call, and no successful replacement
   result.
3. Add an empty-corpus case proving atomic removal of every relation and orphan entity.
4. Retain successful evidence recomputation and `replace=False` delta characterizations.

### Test environment prerequisite

Rollback is the whole point of this change and only a real cluster can demonstrate it; a
`FakeNeo4j` cannot. `backend/tests/integration/test_knowmap_neo4j_replacement.py` therefore
carries the load-bearing assertions, and it needs a reachable Neo4j.

The repo's compose `neo4j` service publishes no host port and sits on `data_net`
(`deploy/compose/docker-compose.yml:253-268`), so it is not reachable from a host-run `pytest`.
Verified working setup: a standalone `neo4j:5.24-community` container with `-p 7687:7687` and
`NEO4J_AUTH=neo4j/neo4jneo4j`, plus `SMAP_NEO4J_URL=bolt://localhost:7687` (the settings default
is `bolt://neo4j:7687`, `backend/app/config/settings.py:78`). Baseline confirmed green under
this setup before any change.

**Pre-existing defect this task must not inherit:** that test file declares no
`pytest.mark.integration`, while `pyproject.toml:357-358` sets `testpaths = ["tests"]` with
`--strict-markers`. A plain `pytest -q` therefore collects it and fails with
`ValueError: Cannot resolve address neo4j:7687` whenever no Neo4j is present. Add the marker
so the default suite stays clean; the new tests added here inherit it.

## 9. Risks and Rollback

The transaction holds Neo4j locks for the whole config-scoped replacement; the build already
processes the full corpus, so this extends transaction duration but not logical scope. A code
rollback reopens the defect. Existing bad graphs are repaired by a successful rebuild, not a
schema migration.

## 10. Acceptance Criteria

- [ ] AC-1: The forced-prune regression fails before the fix and passes after.
- [ ] AC-2: Full replacement upsert/evidence reset and stale prune commit in one Neo4j
  transaction; any exception rolls both back.
- [ ] AC-3: A replacement failure leaves the exact pre-build graph, reports `FAILED`, and
  performs no Qdrant mutation.
- [ ] AC-4: An empty-corpus replacement atomically removes every relation and orphan entity.
- [ ] AC-5: Existing successful evidence/provenance recomputation and Qdrant replacement
  behavior remain green.
- [ ] AC-6: Concept Map `replace=False` delta behavior is unchanged.
- [ ] AC-7: Real-Neo4j integration, focused unit tests, backend lint, format, and type checks
  pass. The integration tier is run against a real cluster per the §8 prerequisite, not
  declared N/A.
- [ ] AC-8: `test_knowmap_neo4j_replacement.py` carries `pytest.mark.integration`, so a plain
  `pytest -q` on a machine with no Neo4j neither collects nor fails it.

## 11. SRS Delta

None. This restores [R11.04] and [R11.12].

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- FU-1: Give the Qdrant replacement sweep a durable retry path if a separate audit confirms
  that stale points materially degrade retrieval after the Neo4j atomicity fix.
- FU-2: `smap/bootstrap/neo4j_init.py:50-68` creates indexes in `settings.neo4j.database`
  (default `smap`), but every `Neo4jAsyncDriver` session omits `database=` and so reads and
  writes the default `neo4j` database. The two coincide only because Community edition rejects
  `CREATE DATABASE` and the bootstrap falls back to `neo4j` (`neo4j_init.py:66`), which is what
  deploy runs (`deploy/compose/docker-compose.yml:254`). On Enterprise the indexes would be
  built in one database and the data written to another. Out of scope here; no behavior in this
  task depends on it.
