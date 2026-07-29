---
type: refactor
status: in-progress
created: 2026-07-29
requirements: [R23.03]
depends_on: [2026-07-29-knowledge-ingest-concurrency-and-enqueue]
---

# Invert knowledge-ingest infrastructure dependencies

## 1. Summary

Move repository, vector-store and staged-source construction out of four knowledge
application services into app composition through narrow Protocols and one shared
factory for API, worker and TUS flows.

## 2. Motivation

`application/ingest_service.py`, `knowmap_ingest_service.py`,
`rag_tus_finalizer.py` and `knowmap_tus_finalizer.py` construct concrete infrastructure,
violating dependency inversion and R23.03, duplicating wiring and hiding resource
ownership.

## 3. Non-goals

- No observable behavior, API or schema change.
- Do not replace `AsyncSession` with a full Unit of Work.
- Do not merge RAG and Knowledge Map into a generic service.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One large repository port? | No; config, document, chunk, vector and staged-source ports stay narrow. | Avoids a god abstraction. |
| Q-2 | Where is construction owned? | `backend/app/wiring/knowledge_ingest.py`. | `app` is the composition root. |

## 5. Current vs Target Structure

Current: application service → concrete repository/Qdrant/MinIO factory.

Target: app wiring → application Protocol + infrastructure adapter. API, Arq and TUS
acquire one lifecycle-aware factory.

## 6. Characterization Test Plan

Pin create, READY behavior, retry/claim, audit metadata, exact
commit/publish/enqueue ordering, compensation, Qdrant lifecycle and Knowledge Map build
gating before moving construction.

## 7. Migration Steps

1. Define exact-method Protocols and AST boundary tripwire.
2. Constructor-inject multipart repositories/adapters.
3. Add app factory and migrate API/worker callers with lifecycle tests.
4. Inject staged-source/finalizer ports into TUS.
5. Remove concrete application imports and duplicate factories.

## 8. Risks and Rollback

Risks are transaction drift, resource leaks, facade blast radius and wiring divergence.
Keep commits per step; rollback is `git revert`.

## 9. Acceptance Criteria

- [ ] AC-1: Characterization tests pass unmodified.
- [ ] AC-2: Four application modules have no knowledge-infrastructure imports/construction.
- [ ] AC-3: Protocols expose only consumed methods.
- [ ] AC-4: API, workers and TUS use shared app wiring.
- [ ] AC-5: resources close on success and exception.
- [ ] AC-6: Backend tests, Ruff and mypy pass.

## 10. SRS Delta

None; enforces R23.03 without behavior change.

## 11. Deviation Log

Empty.

## 12. Follow-ups

- FU-1: Introduce a Unit of Work only if transaction duplication remains.
