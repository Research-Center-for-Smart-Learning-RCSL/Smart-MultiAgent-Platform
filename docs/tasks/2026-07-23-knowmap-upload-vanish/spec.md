---
type: bugfix
status: implemented
created: 2026-07-23
requirements: []
---

# Knowledge Map document uploads silently vanish

## 1. Summary

On staging (smap.rcsl.online) a user uploaded documents to a Knowledge Map,
left the page, returned, and the file was gone from the document list. The
symptom was three independent defects stacked on the same code path, each of
which returns HTTP 500 from `POST /api/knowmap-configs/{id}/documents` and rolls
the whole request transaction back, so the `knowmap_documents` row is never
committed and the file appears to disappear:

1. staging MinIO was missing the `knowmap-sources` bucket (an environment
   provisioning gap), so the blob `put_object` failed with `NoSuchBucket`.
2. `pypdf` and `python-docx` were never declared as runtime dependencies, so
   PDF/DOCX text extraction raised `ParserError: pypdf not installed` in every
   environment (a code/packaging defect); only `.txt` and `.md` ever worked.
3. The multipart ingest created the document row inside the same uncommitted
   transaction as indexing, so any ingest failure rolled the row back to nothing
   instead of persisting a visible `FAILED` row (the tus path already persisted
   FAILED, so the two upload paths behaved inconsistently).

## 2. Observed vs Expected

- Observed: upload returns 500; `knowmap_documents` and `knowmap_chunks` stay at
  0 rows while `knowmap_configs.corpus_revision` had advanced (older successful
  operations); the graph build ran with `triples=0`. The user saw the file card
  briefly (the upload widget's local selection) and an empty list on refetch.
- Expected: a supported upload persists durably and survives navigation; an
  upload that cannot be processed reports a clear, correctly-typed error and
  stays visible as `FAILED` rather than vanishing.

## 3. Root Cause Analysis

- Bucket (staging only): `smap.bootstrap minio-init` provisions seven buckets
  and is idempotent, but staging's MinIO predated the newer buckets and the
  bootstrap was not re-run after the Knowledge Map feature shipped. Missing:
  `knowmap-sources`, `agent-workspace`, `prompt-assistant-files`,
  `skill-bundles`. Evidence: `S3Error NoSuchBucket bucket_name: knowmap-sources`
  on the upload route (`backend/app/api/v1/knowmap.py` ingest path).
- Dependencies (all environments): `shared_kernel/text_extraction/parsers.py`
  imports `pypdf`/`python-docx` lazily and raises `ParserError(... not installed)`
  when absent. Neither was in `backend/pyproject.toml` `dependencies` nor in
  `backend/requirements.lock`. The production/staging image installs from
  `requirements.lock` alone (`backend/Dockerfile` builder stage), so a
  pyproject-only add would not have shipped them.
- Vanish-on-rollback: `KnowmapIngestService._index_document` persists `FAILED`
  by rolling back partial writes then committing the status, but for a brand-new
  upload the `create` was in the same uncommitted transaction, so the rollback
  discarded the create too. Only reindex (row already committed) survived.

## 4. Fix

- Bucket: ran `docker exec <backend-web> python -m smap.bootstrap minio-init` on
  staging (idempotent; existing buckets and the scoped service account are left
  untouched). No repo change.
- Dependencies: declared `pypdf==6.*` and `python-docx==1.*` in
  `backend/pyproject.toml` and surgically pinned `pypdf==6.14.2`,
  `python-docx==1.2.0`, and its `lxml==6.1.1` dependency in
  `backend/requirements.lock` without a full re-resolve (a `pip-compile`
  regeneration dragged ~20 transitive pins forward, including protobuf 6 to 7,
  which is out of scope and higher risk). The lock-consistency gate
  (`scripts/check_lock_consistency.py`) passes and a dry-run install of the full
  lock resolves with no conflicts.
- Vanish-on-rollback: `KnowmapIngestService.ingest` commits the accepted upload
  before indexing, so an index failure leaves a durable `FAILED` row, mirroring
  the tus path.
- Error clarity: added `DocumentUnprocessable` (422) for parse failures, distinct
  from `IngestFailed` (500, kept for server-side embedding/provider/store
  failures). `_index_document` raises `DocumentUnprocessable` when the wrapped
  cause is a `ParserError`. The frontend surfaces a specific message for this type
  (`agents.knowmap.uploadUnprocessable`).

## 5. Verification

- Backend: `ruff check`, `ruff format --check`, `mypy` (touched modules), and
  227 knowmap/ingest/error-mapping unit tests pass, including a new test asserting
  a `ParserError` surfaces as `DocumentUnprocessable` while the row is persisted
  `FAILED`.
- Lock: `scripts/check_lock_consistency.py` OK; `pip install --dry-run -r
  requirements.lock` resolves cleanly with the three additions and no other pin
  moved.
- Frontend: `vue-tsc` typecheck, eslint (changed view), and the
  KnowledgeMapConfigDetailView unit tests pass.
- Staging: after the bucket bootstrap and image rebuild, PDF upload persists and
  the graph build proceeds (confirmed by the operator).

## 6. Follow-ups

- FU-1 (parity) - done: File RAG (`contexts/knowledge/application/ingest_service.py`)
  had the same catch-all `IngestFailed` and the same vanish-on-rollback on its
  multipart path. Applied the same `DocumentUnprocessable` typing and
  commit-before-index treatment; covered by `tests/unit/test_rag_ingest.py`.
- FU-2 (hardening) - done: the MinIO readiness probe
  (`shared_kernel/infra/probes/minio.py`) now asserts all seven provisioned buckets
  exist and names any that are missing, so a bucket provisioning gap fails
  readiness at boot instead of surfacing as a 500 on a user's first upload;
  covered by `tests/unit/test_minio_probe.py`.
- FU-3 (ops): re-run `smap.bootstrap minio-init` as a standard post-deploy step so
  newly added buckets are provisioned automatically. (With FU-2, a stale MinIO now
  also fails readiness, surfacing the gap before traffic is routed.)
- FU-4 (known limitation, from code review): committing the multipart upload row
  before synchronous indexing trades the old vanish-on-crash for a row that stays
  `INGESTING` if the process is killed mid-index (OOM/redeploy); unlike the tus
  path there is no Arq worker to reclaim it, so it is only recovered by re-uploading
  the identical bytes (which re-indexes the non-READY row). This is a deliberate net
  improvement over vanishing (the file stays visible and retriable) but has no
  automatic recovery. A stale-`INGESTING` watchdog (flip rows stuck past a timeout
  to `FAILED`) would close it if the spinner-forever case proves to matter.
