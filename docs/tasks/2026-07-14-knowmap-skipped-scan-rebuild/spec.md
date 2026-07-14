---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.12]
supersedes:
---

# F-27: Knowledge Map `SKIPPED` scan verdict enqueues no rebuild, unlike `QUARANTINED`

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-27).
Co-requires `docs/tasks/2026-07-14-knowmap-rebuild-replacement/spec.md` (F-6) for end-to-end
effect (§7, Q-1). Overlaps the clean-scan intent of F-5
(`docs/tasks/2026-07-14-knowmap-scan-gating/spec.md`).

## 1. Summary

The Knowledge Map scan worker enqueues a graph rebuild when a document's scan verdict is
`QUARANTINED`, but not when it is `SKIPPED` (over-size or a ClamAV scan error). Both verdicts
are equally excluded from the build and retrieval selectors, so the asymmetry means a document
that was built into the graph while `scan_status=pending` (the F-5 race) and later marked
`SKIPPED` has its triples left in Neo4j with no rebuild ever triggered — contradicting the
"never built unless clean" intent, exactly as a `QUARANTINED` verdict is meant to trigger the
cleanup. The fix enqueues a rebuild on `SKIPPED` on both skip paths, mirroring the quarantine
branch. Because Knowledge Map builds are additive (F-6), the rebuild only physically removes the
skipped document's triples once F-6's replacement semantics land; this dossier is therefore
bundled with F-6 so the removal is verifiable end-to-end.

## 2. Observed vs Expected

- **Observed** — in `knowmap_scan_document`
  (`backend/app/workers/tasks/knowmap.py:115-211`): the `QUARANTINED` verdict enqueues
  `enqueue_knowmap_build(config_id, last_build_state, last_build_at)` (`:204-207`), but the two
  `SKIPPED` paths do not — over-size marks `SKIPPED` and returns `"skipped:too_large"`
  (`:147-154`), and the ClamAV-error path marks `SKIPPED` then `raise`s (`:162-170`), so control
  never reaches the shared `:204` enqueue. Both `ready_document_ids` and `allowed_document_ids`
  exclude `quarantined` **and** `skipped`
  (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:374-425`), so a skipped
  document is build- and retrieval-ineligible just like a quarantined one — but only the
  quarantined one triggers the rebuild that acts on that exclusion.
- **Expected** — a `SKIPPED` verdict triggers a graph rebuild on the same footing as
  `QUARANTINED`, so the buildable corpus is recomputed without the un-scannable document (and,
  with F-6, its triples are removed from Neo4j). Intent: the Knowledge Map clean-scan contract
  (the never-built-unless-clean rule the selectors' docstrings state, `knowmap_repositories.py:377-382`),
  [R11.12] (reprocess/scan as a graph-change trigger). Distinct from retrieval hiding: the
  `allowed_document_ids` gate already hides skipped docs at query time
  (`:398-425`), but the graph itself is not corrected without a rebuild.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Symmetry fix only, or bundle F-6? | **Bundle the F-6 dependency.** F-27 enqueues the rebuild on `SKIPPED`, and the end-to-end "triples leave Neo4j" acceptance is verified with F-6's replacement semantics applied. | Chosen over a symmetry-only fix. Knowledge Map builds are additive `MERGE` with no removal of absent triples (`graphrag_builder.py` apply path, `neo4j_driver.py:94-181`; confirmed F-6), so a rebuild alone does not evict the skipped document's persisted triples — it only recomputes buildable-corpus membership. Bundling F-6 makes the fix demonstrably correct end-to-end in one batch rather than shipping a rebuild that is a no-op for Neo4j removal until F-6 lands. |
| Q-2 | Enqueue on the transient ClamAV-error `SKIPPED` path too, or only terminal over-size? | Enqueue on **both** skip paths. | The finding names both paths (`:147-154`, `:162-170`) as missing the enqueue. The dedup job id collapses repeated enqueues, so enqueuing on the retriable path is safe; leaving it out risks a document that exhausts retries staying `SKIPPED` with no rebuild — the same bug. |

## 4. Reproduction

1. A Knowledge Map document is built into the graph while `scan_status=pending` (the F-5 race).
2. The async scan later returns `SKIPPED` — the file exceeds `clamav_max_scan_bytes`
   (`knowmap.py:147-154`), or ClamAV raises `ScanError` (`:162-170`).
3. Observed: the document is marked `SKIPPED` (excluded from selectors), but no
   `knowmap_build` is enqueued, so the graph is never recomputed; its triples remain in Neo4j.
   Compare: a `QUARANTINED` verdict on the same document does enqueue a rebuild (`:204-207`).

Deterministic given the scan verdict.

## 5. Root Cause Analysis

The rebuild enqueue is placed only on the `QUARANTINED` tail (`knowmap.py:204-207`), while the
two `SKIPPED` branches exit before it — over-size `return`s (`:147-154`) and the ClamAV-error
path `raise`s (`:162-170`). That is the root cause: an asymmetric trigger for two verdicts that
the selectors treat identically. A contributing wrinkle: both skip branches load only the
document (`doc`) and never the config (`cfg`), so mirroring the enqueue requires fetching `cfg`
as the quarantine branch does (`:184`). Note the in-code comment near the quarantine enqueue
(`:202-203`) claims the rebuild makes the tainted document's triples "leave the graph"; per F-6
the additive build does not remove them — so even the existing quarantine path relies on F-6 for
that claim to hold.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — bounded. Retrieval already hides skipped documents via
  `allowed_document_ids` (`knowmap_repositories.py:398-425`), so this is not an independent
  retrieval leak; the visible effect is stale triples remaining in the Neo4j graph (and its UI
  view) for a skipped-after-pending document. It is a trigger asymmetry, not a new exposure.
- **Sibling suspects:**
  - **F-5 (scan gating, separate dossier).** F-5 fixes the pending-scan race that lets a
    document be built before a clean verdict; F-27 handles the cleanup when the eventual verdict
    is `SKIPPED`. Complementary — F-5 narrows the window, F-27 corrects the outcome.
  - **F-6 (rebuild replacement, co-required).** Without F-6 the rebuild does not remove Neo4j
    triples; bundled per Q-1.
  - **`mark_scan` status asymmetry (note).** `mark_scan` flips `DocumentStatus` to
    `QUARANTINED` only for a quarantine verdict (`knowmap_repositories.py:342-354`); a `SKIPPED`
    verdict sets `scan_status` only and leaves `status=READY`. Both are still excluded via the
    `scan_status` filter, so this does not change eligibility — flagged so the implementer does
    not "fix" the status flip and accidentally widen scope.
  - **RAG (File) scan path (cleared for this finding).** F-27 is Knowledge-Map-specific; the RAG
    scan/skip path is not in scope.

## 7. Fix Design

Mirror the quarantine rebuild enqueue on both `SKIPPED` branches, and rely on F-6 for the
physical triple removal.

1. **Over-size skip (`knowmap.py:147-154`).** After marking `SKIPPED`, load the config (mirror
   the quarantine fetch at `:184`) and call `enqueue_knowmap_build(config_id=cfg.id,
   last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at)` — identical args to
   `:205-207` — then return.
2. **ClamAV-error skip (`knowmap.py:162-170`).** After marking `SKIPPED`, load the config and
   enqueue the rebuild the same way **before** re-raising for Arq retry, so a document that
   exhausts retries still had a rebuild queued.
3. **Prefer a single tail path if clean.** If it reads better, restructure so a `SKIPPED` verdict
   flows to the same enqueue tail as `QUARANTINED` (e.g. enqueue when `scan_status in
   {QUARANTINED, SKIPPED}`), keeping the ClamAV-error re-raise semantics intact. Either shape is
   acceptable; the observable contract is "rebuild enqueued on SKIPPED".
4. **Dedup** is inherited: `knowmap_build_job_id` derives from `(config_id, last_build_state,
   last_build_at epoch)` (`backend/contexts/knowledge/application/knowmap_triggers.py:21-37`),
   and Arq drops a duplicate `_job_id`, so a SKIPPED enqueue coalesces with a concurrent
   quarantine/ingest enqueue for the same config and does not multiply builds.
5. **F-6 dependency.** The rebuild recomputes buildable-corpus membership; the skipped document's
   Neo4j triples are physically removed only once F-6's full-corpus replacement semantics apply.
   This dossier is built together with F-6 so the end-to-end removal is verified.

**Reuse inventory:**
- `enqueue_knowmap_build` and `knowmap_build_job_id`
  (`knowmap_triggers.py:21-63`) — the exact helpers the quarantine and ingest paths use.
- The quarantine branch (`knowmap.py:184,204-207`) as the structural template (config fetch +
  enqueue).

**Data repair:** existing skipped-after-pending documents will be corrected the next time their
config rebuilds under F-6 (a one-off manual/enqueued rebuild per affected config). No migration.

## 8. Regression Test Plan

Net-new — `knowmap_scan_document` has no existing test (only the ingest-enqueues-scan path is
covered, `backend/tests/unit/test_knowmap_ingest.py`). Add a scan-worker test module with a fake
enqueue and repositories:

1. **Over-size `SKIPPED` enqueues a rebuild (red-first)** — a document exceeding
   `clamav_max_scan_bytes` is marked `SKIPPED` and enqueues one `knowmap_build` with the config's
   dedup job id. Fails today (no enqueue on the over-size path).
2. **ClamAV-error `SKIPPED` enqueues a rebuild before raising** — `ScanError` marks `SKIPPED`,
   enqueues the rebuild, then re-raises for retry. Fails today.
3. **Parity with `QUARANTINED`** — a `QUARANTINED` verdict still enqueues exactly as before (no
   regression), and both verdicts produce the same enqueue args.
4. **Dedup** — a SKIPPED enqueue and a concurrent quarantine/ingest enqueue for the same
   `(config, last_build_state, last_build_at)` collapse to one queued build.
5. **End-to-end triple removal (with F-6, may be integration/deferred)** — after a SKIPPED
   rebuild under F-6's replacement semantics, the skipped document's triples are absent from
   Neo4j. Marked as depending on F-6; if F-6 is not yet merged in the build batch, record the
   gap in the deviation log rather than asserting it against additive-only builds.

Primary red-first: (1).

## 9. Risks and Rollback

- **F-6 not landing together.** If F-27 ships without F-6, the rebuild is a no-op for Neo4j
  removal (retrieval still hides the doc). Bundling per Q-1 mitigates; if decoupled at build
  time, document it as a deviation and keep AC-5 deferred.
- **Retry noise on the ClamAV-error path.** Enqueuing on every transient error + retry could
  queue repeatedly; the `_job_id` dedup and Arq's duplicate-drop bound it to one build per
  `(config, build cycle)`.
- **Restructuring the tail.** If the enqueue is unified into a shared tail, the ClamAV-error
  re-raise must be preserved so Arq still retries the scan; covered by test (2).
- **Rollback** — remove the two SKIPPED enqueues; behavior reverts. Code-only, no schema/data
  change.

## 10. Acceptance Criteria

- [ ] AC-1: The over-size SKIPPED rebuild test (§8.1) fails before the fix and passes after.
- [ ] AC-2: Both `SKIPPED` paths (over-size and ClamAV error) enqueue a `knowmap_build` with the
  same args and dedup job id the `QUARANTINED` path uses.
- [ ] AC-3: The ClamAV-error path still re-raises for Arq retry after enqueuing.
- [ ] AC-4: `QUARANTINED` behavior and the retrieval/selector gating are unchanged; SKIPPED and
  QUARANTINED enqueue identically.
- [ ] AC-5 (with F-6): after a SKIPPED-triggered rebuild under F-6's replacement semantics, the
  skipped document's triples are absent from Neo4j. Deferred/marked if F-6 is not in the batch.
- [ ] AC-6: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`.

## 11. SRS Delta

None — restores the Knowledge Map clean-scan contract and [R11.12] rebuild-trigger behavior.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (F-6 co-requirement):** the physical removal of skipped/quarantined triples depends on
  F-6's replacement semantics; F-27's rebuild is otherwise membership-only.
- **FU-2 (F-5 overlap):** the pending-scan build race that makes this cleanup necessary is F-5;
  narrowing that window reduces how often the SKIPPED cleanup is needed.
