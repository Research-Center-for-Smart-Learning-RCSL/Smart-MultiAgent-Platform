---
type: bugfix
status: in-progress
created: 2026-07-29
requirements: [R10.02, R10.10, R10.11, R11.12, R22.15.05, R22.15.07]
depends_on: [2026-07-22-reingest-allowlist-propagation]
---

# Fence concurrent knowledge ingestion and recover enqueue failures

## 1. Summary

Concurrent multipart retries of one RAG or Knowledge Map document can both enter the
destructive indexing pipeline, while a Redis publication failure in the RAG tus finalizer
can leave a committed `INGESTING` row without an ingest job. The same investigation found
that create-race recovery has no database uniqueness constraint and stale workers have no
ownership token. This task makes one attempt the only writer and makes enqueue recovery
ownership-aware.

## 2. Observed vs Expected

- **Observed.** Multipart services use a non-locking SHA lookup and index every non-READY
  match (`application/ingest_service.py:249-281`,
  `application/knowmap_ingest_service.py:182-214`). Composite SHA indexes are non-unique
  (`alembic/versions/0012_rag.py:122-127`,
  `alembic/versions/0048_knowmap.py:145-146`). Workers receive only `document_id`, and the
  RAG tus started publication is outside enqueue recovery
  (`application/rag_tus_finalizer.py:195-229`).
- **Expected.** One config/SHA identifies one document. At most one owned attempt mutates
  its chunks/vector points, a live attempt is coalesced, and telemetry failure never
  prevents authoritative job dispatch.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What does a duplicate upload do while ingest is live? | Overwrite the submitted allowlist, return the current `INGESTING` document under the existing 201 contract, and do not index again. | Preserves approved re-upload behavior without duplicate external work. |
| Q-2 | How are stale attempts fenced? | Persist token, lease and attempt; pass token/attempt to workers; guard terminal writes and acquire a per-document PostgreSQL advisory transaction lock before processing/reclaim. | A DB lease alone cannot fence Qdrant's document-wide delete. |
| Q-3 | How are existing duplicate rows handled? | Migration/preflight fails closed and reports duplicate groups; no automatic merge. | Merging could widen access or orphan evidence. |
| Q-4 | Is a durable queue outbox included? | No. Conditional recovery plus an expired-claim reconciler closes the named failure and crash-stuck state. | A general outbox is wider infrastructure. |

## 4. Reproduction

Against PostgreSQL, barrier two multipart requests after both read one FAILED document,
then release them and observe both delete/reinsert chunks. RAG can also interleave
document-wide Qdrant deletion/upsert. Separately make `Publisher.emit` raise in the RAG
tus finalizer: enqueue is never called and the committed row remains `INGESTING`.

## 5. Root Cause Analysis

The earliest cause is absence of an enforced single-owner invariant. Plain lookup,
non-unique config/SHA indexes and document-only jobs allow multiple sessions and stale
workers to own the same effects. FU-7 is aggravated by treating best-effort realtime
publication as a prerequisite for authoritative enqueue.

## 6. Blast Radius and Sibling Suspects

- Both products, multipart and tus, every project.
- Create races may already have persisted duplicate rows; preflight detects them.
- Four post-lookup `set_agents` assertions race with hard delete and must return typed
  errors instead of assertion 500s.
- Other Arq producers are out of scope.

## 7. Fix Design

1. Add composite uniqueness and claim token/deadline columns to both document tables.
2. Add repository create-or-fetch, claim/reclaim, ownership verification, advisory lock
   and conditional success/failure operations.
3. Resolve policy, allowlist, audit and claim in one short transaction. READY behavior is
   unchanged; live `INGESTING` coalesces; terminal or expired rows claim.
4. Pass attempt/token into jobs. Stale jobs perform no blob/chunk/vector/status work.
5. Hold the advisory transaction lock through destructive processing and re-check
   ownership after acquiring it.
6. Publish started best-effort, enqueue under recovery and conditionally fail only the
   still-owned attempt. Reconcile expired claims.

Pre-deploy duplicate inventory:

```sql
SELECT 'rag' AS product, rag_config_id AS config_id, sha256,
       array_agg(id ORDER BY uploaded_at, id) AS document_ids
FROM rag_documents
GROUP BY rag_config_id, sha256
HAVING count(*) > 1
UNION ALL
SELECT 'knowmap', knowmap_config_id, sha256,
       array_agg(id ORDER BY uploaded_at, id)
FROM knowmap_documents
GROUP BY knowmap_config_id, sha256
HAVING count(*) > 1;
```

Any returned group requires operator review of allowlists, chunks/vector evidence and
status before migration; the migration does not mutate those rows.

## 8. Regression Test Plan

Write real-PostgreSQL barrier tests for first-upload and retry races before the fix.
Add repository boundary tests, stale-worker no-op tests, enqueue/publication ordering
tests, conditional compensation tests and duplicate-preflight migration tests.

## 9. Risks and Rollback

Migration fails if production contains duplicates; it reports groups without changing
data. Worker rollout must accept legacy jobs before producers switch. Roll back per
commit; ownership columns remain until old jobs and leases expire.

## 10. Acceptance Criteria

- [ ] AC-1: Concurrent first uploads produce one row and one indexing owner.
- [ ] AC-2: Concurrent FAILED/QUARANTINED retries produce one destructive attempt.
- [ ] AC-3: Live `INGESTING` re-upload overwrites allowlist and coalesces.
- [ ] AC-4: Expired claims recover; stale workers/failure handlers are no-ops.
- [ ] AC-5: READY identical/different behavior and audit outcomes are unchanged.
- [ ] AC-6: Publish failure cannot prevent enqueue; enqueue failure conditionally
      persists FAILED and attempts best-effort failed publication.
- [ ] AC-7: Hard-delete races return a typed error, never an assertion 500.
- [ ] AC-8: Focused unit/integration/wiring tests and backend gates pass.

## 11. SRS Delta

None. This restores retry safety and async dispatch implicit in R10/R11/R22.

## 12. Deviation Log

Empty.

## 13. Follow-ups

- FU-1: A reusable transactional outbox may replace per-feature reconcilers.
