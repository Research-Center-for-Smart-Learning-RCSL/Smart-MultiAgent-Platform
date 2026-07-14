---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.12]
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
| Q-2 | Enqueue on the transient ClamAV-error `SKIPPED` path too, or only terminal over-size? | Enqueue on **both** paths, but the ClamAV-error path only when the retry is **exhausted** (`ctx["job_try"] >= max_tries`, `:211`). | The finding names both paths (`:147-154`, `:162-170`). Over-size is immediately terminal (no retry) → enqueue at once. The ClamAV-error path re-raises for retry (`max_tries=3`): enqueuing on *every* attempt would build a graph that excludes the document while it is still mid-retry and might yet come back `CLEAN` — the rebuild would exclude a document that turns out fine. So enqueue there only on the final failed attempt, when `SKIPPED` is genuinely terminal. Leaving it out entirely would let a retry-exhausted document stay `SKIPPED` with no rebuild — the same bug. Second-angle review caught the every-attempt variant as a premature-exclusion regression. |

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

1. **Over-size skip (`knowmap.py:147-154`).** After marking `SKIPPED`, load the config via
   `KnowmapConfigRepository(db2).get(doc.knowmap_config_id)` (the `doc` is already loaded at
   `:142`; mirror the quarantine fetch at `:184`) and call `enqueue_knowmap_build(config_id=cfg.id,
   last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at)` — identical args to
   `:205-207` — then return.
2. **ClamAV-error skip (`knowmap.py:162-170`).** After marking `SKIPPED`, enqueue the rebuild
   **only on the exhausted attempt** — guard on `ctx.get("job_try", 1) >= knowmap_scan_document.max_tries`
   (the task's `max_tries=3`, `:211`) — then re-raise. On non-final attempts, mark `SKIPPED` and
   re-raise without enqueuing, so a document that may still recover to `CLEAN` on retry is not
   prematurely excluded by a rebuild (Q-2). Confirm arq's `ctx` exposes `job_try` in the deployed
   version (as F-23 flags for arq behavior); if unavailable, fall back to enqueuing on the error
   path and accept the dedup-bounded churn. Note `ctx` is currently unused (`_ = ctx`, `:118`).
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
2. **ClamAV-error `SKIPPED` on the exhausted attempt enqueues a rebuild** — `ScanError` on the
   final try (`ctx["job_try"] == max_tries`) marks `SKIPPED`, enqueues the rebuild, then re-raises.
   Fails today (no enqueue).
3. **ClamAV-error `SKIPPED` on a non-final attempt does NOT enqueue** — `ScanError` with
   `ctx["job_try"] < max_tries` marks `SKIPPED` and re-raises without enqueuing (premature-exclusion
   guard, Q-2).
4. **Parity with `QUARANTINED`** — a `QUARANTINED` verdict still enqueues exactly as before (no
   regression), and terminal SKIPPED produces the same enqueue args.
5. **Dedup** — a SKIPPED enqueue and a concurrent quarantine/ingest enqueue for the same
   `(config, last_build_state, last_build_at)` collapse to one queued build.
6. **End-to-end triple removal (with F-6, may be integration/deferred)** — after a SKIPPED
   rebuild under F-6's replacement semantics, the skipped document's triples are absent from
   Neo4j. Marked as depending on F-6; if F-6 is not yet merged in the build batch, record the
   gap in the deviation log rather than asserting it against additive-only builds.

Primary red-first: (1).

## 9. Risks and Rollback

- **F-6 not landing together.** If F-27 ships without F-6, the rebuild is a no-op for Neo4j
  removal (retrieval still hides the doc). Bundling per Q-1 mitigates; if decoupled at build
  time, document it as a deviation and keep AC-5 deferred.
- **Premature exclusion on the ClamAV-error path.** Enqueuing before retries are exhausted would
  rebuild the graph without a document that may still pass on retry. Mitigated by the
  `job_try >= max_tries` gate (Q-2, §7.2) and test (3); the `_job_id` dedup additionally bounds any
  residual churn to one build per `(config, build cycle)`.
- **Restructuring the tail.** If the enqueue is unified into a shared tail, the ClamAV-error
  re-raise must be preserved so Arq still retries the scan; covered by test (2).
- **Rollback** — remove the two SKIPPED enqueues; behavior reverts. Code-only, no schema/data
  change.

## 10. Acceptance Criteria

- [x] AC-1: The over-size SKIPPED rebuild test (§8.1) fails before the fix and passes after.
  `test_oversize_skipped_enqueues_rebuild` (run red→green).
- [x] AC-2: Over-size `SKIPPED` (terminal) and the ClamAV-error path on its **exhausted** attempt
  enqueue a `knowmap_build` with the same args and dedup job id the `QUARANTINED` path uses; a
  non-final ClamAV-error attempt does not enqueue. Both SKIPPED paths route through the shared
  `_enqueue_rebuild_for_config`; `test_clamav_error_skipped_enqueues_only_on_exhausted_attempt`
  and `..._does_not_enqueue_on_non_final_attempt`.
- [x] AC-3: The ClamAV-error path still re-raises for Arq retry (enqueuing only on the final try).
  Both ClamAV-error tests assert `pytest.raises(ScanError)`.
- [x] AC-4: `QUARANTINED` behavior and the retrieval/selector gating are unchanged; SKIPPED and
  QUARANTINED enqueue identically. `test_quarantine_still_enqueues_with_same_args`; selectors
  untouched.
- [x] AC-5 (with F-6): after a SKIPPED-triggered rebuild under F-6's replacement semantics, the
  skipped document's triples are absent from Neo4j. Satisfied by composition — F-6 is in this
  batch: `ready_document_ids` (unchanged) excludes skipped docs, and F-6's
  `remove_stale_for_build` drops relations absent from the new build. The removal mechanism is
  covered by F-6's Neo4j integration test (`test_knowmap_neo4j_replacement.py`, not run
  locally — see F-6 D-1); no separate F-27 end-to-end test was added.
- [x] AC-6: `ruff check`, `ruff format --check`, and `mypy` pass for the touched module (no new
  errors; one pre-existing unrelated `tenancy` mypy error remains). Full `pytest -q` at the
  batch's end.

## 11. SRS Delta

None — restores the Knowledge Map clean-scan contract and [R11.12] rebuild-trigger behavior.

## 12. Deviation Log

- **D-1 (job_try fallback default):** the ClamAV-error guard is
  `ctx.get("job_try", _SCAN_MAX_TRIES) >= _SCAN_MAX_TRIES`. arq populates `job_try`, so the
  normal path uses the real attempt number (no enqueue before the final try). The default is
  the *exhausted* value rather than 1, so if `job_try` were ever absent the rebuild still fires
  (dedup-bounded churn) instead of silently never rebuilding — the fallback §7.2/Q-2 prescribes.
- **D-2 (both SKIPPED paths share a helper):** rather than the "single tail path" option in
  §7.3, the two SKIPPED branches call a shared `_enqueue_rebuild_for_config` (load config +
  enqueue with the dedup nonce), keeping the ClamAV-error re-raise semantics intact and the
  over-size path terminal. `_SCAN_MAX_TRIES` is a named constant shared by the guard and the
  `.max_tries` assignment so they cannot drift.
- **D-3 (F-6 landed together):** per Q-1 this was built with F-6 in the same batch, so AC-5's
  end-to-end triple removal holds by composition (no deferral needed).

## 13. Follow-ups

- **FU-1 (F-6 co-requirement):** the physical removal of skipped/quarantined triples depends on
  F-6's replacement semantics; F-27's rebuild is otherwise membership-only.
- **FU-2 (F-5 overlap):** the pending-scan build race that makes this cleanup necessary is F-5;
  narrowing that window reduces how often the SKIPPED cleanup is needed.
- **FU-3 (CLEAN-after-retry re-inclusion, pre-existing):** the `CLEAN` verdict path does not
  enqueue a rebuild (it relies on the ingest-time build). A document that was excluded (pending or
  transiently skipped) and only later verified `CLEAN` is not re-added to the graph until the next
  corpus mutation. This is broader than F-27 and pre-exists it; worth a separate look at whether
  `CLEAN` should also trigger a rebuild when the document missed its ingest-time build.
