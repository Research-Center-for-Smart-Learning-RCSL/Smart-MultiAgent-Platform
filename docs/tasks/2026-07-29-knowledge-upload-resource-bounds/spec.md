---
type: feature
status: implemented
created: 2026-07-29
requirements: [R10.02, R10.03, R11.12, R11.13, R20.03, R22.15.04, R22.15.07]
depends_on: [2026-07-29-knowledge-ingest-concurrency-and-enqueue]
---

# Bound knowledge-upload staging, scanning and ingestion resources

## 1. Summary

RAG and Knowledge Map accept resumable sources up to 1 GiB but can buffer an unbounded
PATCH body, hold unreserved staging space, parse before scan verdict, and materialize the
whole source/text/chunk set in a shared 50-job worker. Preserve the raw upload contract
while imposing explicit transfer, extraction and execution budgets.

## 2. Goals and Non-goals

**Goals**

- Bound request memory, staging disk, scan and parser resources.
- Scan before parsing and isolate heavy knowledge jobs from general workloads.
- Preserve raw uploads up to 1 GiB with durable typed processing failures.

**Non-goals**

- Guarantee arbitrary 1 GiB contents can be fully indexed.
- Build the final per-tenant round-robin dispatcher.
- Change tenant authorization or allowlist semantics.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is “1 GB”? | 1 GiB = 1,073,741,824 bytes. | Matches existing binary implementation. |
| Q-2 | What does it guarantee? | Raw transport/storage; indexing has documented budgets. | Storage size is not a parser-resource promise. |
| Q-3 | Scanner disabled? | Preserve R22.15.07 no-op CLEAN; actual error/timeout fails closed. | Compatibility without ambiguous bypass. |
| Q-4 | Hard budgets? | 64 MiB extracted UTF-8, 10M token estimate, 20k chunks, 5k PDF pages, 100 OCR pages; DOCX 256 MiB measured expansion, 100:1 ratio, 10k entries, 64 MiB entry. | Fits isolated low concurrency. |

## 4. Current State

TUS calls `request.body()` when Content-Length is absent (`app/api/v1/tus.py:260-280`);
reservations are request-count based. Finalizers enqueue ingest and scan independently.
`MinioBlobStore.get()` uses `resp.read()`, parsers return complete strings, chunkers
accumulate complete lists, and all jobs share `WorkerSettings.max_jobs = 50`.

## 5. Design

### Options considered

**Lower raw cap:** simple but breaks R10.02.

**Only isolate worker:** one source can still OOM and parse before scan.

**Layered bounds (selected):** stream ingress, reserve bytes, scan first, isolate queues,
spool sources and enforce parser/chunker budgets.

### Decision

Implement layered P0/P1/P2 bounds. Preserve the source for owner diagnosis/deletion
after a typed `resource_budget_exceeded` failure.

## 6. Detailed Changes

- **Backend:** stream 16 MiB PATCH chunks; atomic Redis reservations/quotas; staging
  headroom/reconciliation; `failure_code`; scan-first orchestration; streaming
  MinIO/clamd; spool/path parsers; bounded chunk iteration and batched persistence.
- **API:** expose bounded failure code and regenerate frontend types.
- **Frontend:** render typed scanning/resource failures through i18n.
- **Deploy:** dedicated `knowledge_scan` (max 2, 20 min) and `knowledge_ingest`
  (max 1, 30 min) workers; memory/temp limits and validated settings.

## 7. NFR Checklist

- [x] i18n for typed failure states.
- [x] Audit rejection/quarantine without parser exceptions.
- [x] User/project reservation and queue isolation.
- [x] Stable error UX.
- [x] Metrics without tenant-ID labels.

## 8. Security Considerations

Enforce size while streaming, distrust archive headers, measure decompression, scan
before parse, isolate parsers, clean temporary files and cap user/project/host staging.

## 9. Quality Notes

Reuse TUS CAS storage, GraphRAG concurrency patterns, BlobStore ports and settings
validation. Multipart and tus share one bounded parser pipeline.

## 10. Risks and Rollback

Expansion-heavy files now fail explicitly instead of crashing workers. Queue split needs
lockstep producer/consumer deployment. Roll back by phase; additions remain backward
compatible until old binaries retire.

## 11. Acceptance Criteria

- [x] AC-1: PATCH without Content-Length buffers at most 16 MiB; +1 returns 413 with
      offset/file restored.
- [x] AC-2: Atomic reservations, quotas and headroom release on finalize/delete/expiry.
- [x] AC-3: No parser runs before CLEAN/no-op-CLEAN; quarantine/errors never ingest.
- [x] AC-4: Dedicated bounded workers isolate knowledge and general jobs.
- [x] AC-5: Raw 1 GiB objects are never materialized as Python bytes.
- [x] AC-6: Every approved budget accepts exactly-at and rejects +1 with typed failure.
- [x] AC-7: Small-file output is characterization-identical.
- [x] AC-8: Security, quality and runnable full gates pass; database-backed wiring
      remains unavailable on this host.

## 12. Test Plan

Route streaming/rollback, concurrent reservation Lua, sweep reconciliation, scan order,
sparse-stream memory, parser boundaries, queue isolation and four-MIME regressions.

## 13. SRS Delta

Amend R10.02/R22.15.04 to define **1,073,741,824 bytes (1 GiB)** as raw upload/storage
maximum. Document asynchronous extraction/archive/OCR/chunk budgets and stable failure
codes. Configured scanner failure is not a pass.

## 14. Open Questions

Operational quotas may be lowered by deployment sizing but not raised past hard maxima
without security review.

## 15. Deviation Log

- The host has no Docker daemon and cannot resolve the configured `postgres`
  service, so database-backed wiring tests were not runnable. Unit, non-DB
  integration, static, frontend and merged-Compose validation passed.

## 16. Follow-ups

- FU-1: Fair per-project round-robin dispatch and embedding-cost governance.
- FU-2: Unify legacy byte and bounded path PDF/DOCX/OCR implementations around
  one extraction core; the bounded knowledge path is authoritative meanwhile.
- FU-3: Extract bounded parse/chunk orchestration from the two large ingestion
  methods after the resource contracts settle.
