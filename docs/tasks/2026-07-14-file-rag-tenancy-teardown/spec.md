---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R10.06]
---

# F-24: File RAG source blobs and the per-project Qdrant collection leak on tenancy deletion

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-24). **Release
blocker** — routes through `/check-security` before merge (audit FU-1).

## 1. Summary

File RAG's only infrastructure teardown, `RagConfigService.purge_documents_infra`, requires a list
of live document rows (to read each `minio_path` and `id`) and is invoked from exactly two request
handlers — RAG config delete and RAG document delete. Nothing else calls it. When an org or project
is soft-deleted and later hard-deleted by the retention worker, the `rag_configs → rag_documents →
rag_chunks` rows cascade away in Postgres via `ON DELETE CASCADE`, but the retention worker's only
MinIO sweep targets the exports bucket and it never touches Qdrant. Because the document rows (which
carry the blob keys) are gone and File RAG has no orphan sweep, the uploaded source blobs under
`rag-sources/{project_id}/…` and the entire per-project Qdrant collection `rag_{project_id}` are
left with no discovery path — permanent per-tenant data residue after the tenant is deleted. This
is a data-remanence / erasure-failure defect: raw uploaded documents and per-tenant embeddings
survive tenant deletion indefinitely. The fix does both halves the audit recommends: (a) proactive,
project-scoped File RAG teardown wired into the retention hard-delete step so erasure happens at the
deletion moment, and (b) a backstop File RAG orphan sweep keyed on the live Postgres project set,
mirroring F-8's multi-store sweep, to reclaim orphans from any path (including already-leaked data).

## 2. Observed vs Expected

- **Observed:**
  - `RagConfigService.purge_documents_infra(*, project_id, docs)`
    (`backend/contexts/knowledge/application/config_service.py:284-370`) purges **per-document
    only**: Qdrant points by `doc_id` filter (`:314-331` → `QdrantStore.delete_documents`,
    `backend/contexts/knowledge/infrastructure/qdrant_store.py:145-167`, which never drops the
    collection and early-returns if it is missing, `:161-162`) and MinIO blobs one-by-one from each
    passed doc's `minio_path` (`:333-368`, key at `:353`). It requires the `docs` list, so it
    cannot run after the rows have cascaded away.
  - Exactly two callers, both request handlers: RAG config delete
    (`backend/app/api/v1/rag.py:373`, after `soft_delete` + commit) and document delete (`:612`,
    after commit). No sibling teardown method exists; retention and `contexts/tenancy/` never call
    it (grep-confirmed).
  - Retention: `_SOFT_DELETE_TABLES` (`backend/app/workers/tasks/retention.py:51-57`) is
    `(orgs, projects, agents, workflows, chatrooms)` — no RAG table. The hard-delete loop
    `_purge_soft_deleted_tenancy` (`:141-199`, cutoff `now()-60d` `:147`, `sa.delete` batches
    `:195-196`) erases rows purely in Postgres. The only MinIO sweep, `_purge_exports_bucket`
    (`:356-409`, registered `:527`), touches only `exports_bucket` (`:374,:388`); no Qdrant client
    is imported anywhere in the module.
  - Cascade chain (`backend/alembic/versions/0012_rag.py`): `rag_configs.project_id → projects.id`
    `ondelete=CASCADE` (`:59-60`); `rag_documents.rag_config_id → rag_configs.id` CASCADE
    (`:97-98`); `rag_chunks.document_id → rag_documents.id` CASCADE (`:132-133`). Deleting a
    project cascades all three, destroying the only record of which blobs/points existed.
  - Blob layout: bucket `rag-sources` (`backend/app/config/settings.py:102`), key
    `{project_id}/{config_id}/{sha256}` (`rag_source_object_key`,
    `backend/contexts/knowledge/application/ingest_service.py:76-80`); `minio_path =
    rag-sources/{project_id}/{config_id}/{sha256}`. Every project blob shares the
    `rag-sources/{project_id}/` prefix.
  - Collection name `rag_{project_id}` with UUID dashes normalized to underscores
    (`qdrant_store.py:37-40`). `QdrantStore` has **no** `delete_collection` method at all (only
    `ensure_collection`, `upsert_chunks`, `search`, `delete_document`, `delete_documents`); the only
    `delete_collection` in the tree is `GraphRagVectorStore.delete_collection`
    (`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:315-318`), which per F-11
    has no production caller.
  - No domain event bus exists (no `shared_kernel/events`); org/project deletion emits only
    append-only `AuditEvent` records (`org.deleted`/`project.deleted`), which nothing subscribes to.
- **Expected** — when a tenant (org/project) is deleted, all of its File RAG data is erased,
  including source blobs and the per-project Qdrant collection. The `purge_documents_infra`
  docstring (`:290-296`) states infra teardown must accompany deletion, and [R10.06] defines the
  per-project `rag_{project_id}` collection as tenant-scoped data. Tenant deletion must not leave
  per-tenant blobs or vectors behind.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Proactive teardown, backstop orphan sweep, or both? | **Both.** | Proactive teardown guarantees erasure at the 60-day hard-delete moment (GDPR-clean, deterministic), matching the existing retention→facade delegation precedent. The sweep is defense-in-depth: it reclaims orphans from any path — a failed teardown, a hard crash between steps, and data already leaked before this fix. F-8 built the graph-store analogue; this mirrors it for File RAG. |
| Q-2 | Run teardown at soft-delete or at hard-delete? | **At hard-delete** (in `_purge_soft_deleted_tenancy`). | Soft-delete is recoverable within the 60-day window; erasing blobs/vectors then would break recovery. Hard-delete is the point at which Postgres rows are permanently erased, so external stores should be erased in the same step for consistent semantics. |
| Q-3 | New `QdrantStore.delete_collection` — add here or depend on F-11? | **Add if absent; coordinate with F-11.** | F-11 also adds `QdrantStore.delete_collection` (`docs/tasks/2026-07-14-embedding-dimension-pin-durability/spec.md:194-196`) and defers tenancy-cascade orphans to F-24 (`:291-293`). Both are draft; whichever builds first adds the method mirroring `GraphRagVectorStore.delete_collection:315-318`, the other reuses it. |
| Q-4 | Sweep "live set" = only non-deleted projects, or every project row present? | **Every `project_id` present in the `projects` table** (regardless of `deleted_at`). | A soft-deleted-but-not-yet-hard-deleted project still has a row and is recoverable, so its blobs/collection must be retained until hard-delete. An orphan is data whose `project_id` has **no** row at all — mirrors F-8's "not present in Postgres." |

## 4. Reproduction

Preconditions: a project with a File RAG config and at least one uploaded document (blob under
`rag-sources/{project_id}/…`, points in `rag_{project_id}`); a MinIO and Qdrant the test can
inspect.

1. Soft-delete the project (`ProjectService.soft_delete`,
   `backend/contexts/tenancy/application/project_service.py:144-152`) — sets `deleted_at`.
2. Advance past the retention window and run `_purge_soft_deleted_tenancy`
   (`retention.py:141-199`); the `projects` row is hard-deleted and `rag_configs/rag_documents/
   rag_chunks` cascade away in Postgres.
3. Inspect MinIO and Qdrant: the `rag-sources/{project_id}/…` blobs and the `rag_{project_id}`
   collection still exist, with no Postgres row referencing them and no sweep to find them.

Deterministic with inspectable MinIO/Qdrant stubs.

## 5. Root Cause Analysis

The causal chain:

1. File RAG infra teardown is coupled to live document rows: `purge_documents_infra` needs `docs`
   to enumerate blob keys and point IDs (`config_service.py:284-370`), so it can only run on the
   request path while the rows still exist (`rag.py:373,612`).
2. Tenant hard-delete erases those rows via `ON DELETE CASCADE` (`0012_rag.py:59-60,97-98,132-133`)
   without invoking any File RAG teardown (`retention.py:141-199` sweeps only Postgres +
   the exports bucket).
3. There is no File RAG orphan sweep (the graph reconciler enumerates via `neo4j.list_config_ids()`,
   `backend/contexts/knowledge/application/graphrag_reconciler.py:211`, covering only Neo4j-keyed
   stores). **The root cause is the absence of any project-scoped (row-independent) File RAG
   teardown at tenant deletion.** Correcting it — a teardown keyed on `project_id` alone (blob
   prefix + collection name), run at hard-delete, plus a sweep keyed on the live project set —
   makes erasure happen and makes orphans discoverable without depending on the cascaded rows.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — unbounded MinIO growth (raw uploaded source files) and unbounded Qdrant growth
  (per-tenant embedding collections), persisting indefinitely after every org/project deletion; a
  per-tenant data-remanence exposure (§Security).
- **Sibling suspects:**
  - **Org-owned projects — covered.** `OrgService.soft_delete` soft-deletes each child project
    (`backend/contexts/tenancy/application/org_service.py:142-185`); retention hard-deletes the
    `projects` rows, so per-project teardown at the projects step covers org deletion. Verify child
    projects receive `deleted_at` in the org path during build.
  - **Account deletion — covered by the same path.** `AccountDeletionService.cascade_account_deletion`
    (`backend/contexts/tenancy/application/account_deletion_service.py:47-…`) fans out to project/org
    soft-delete, converging on the same retention hard-delete.
  - **F-8 graph-store leak — distinct, separate spec.** F-8 is a Qdrant-only orphan on a *config*
    delete whose Qdrant purge fails (`docs/tasks/2026-07-14-graphrag-orphan-sweep-multistore/spec.md`);
    F-24 is File RAG orphaned by a *parent cascade* with no sweep at all. Both use the
    "enumerate external store vs Postgres live set" pattern; kept separate per F-8 §6/FU-3.
  - **F-11 drop-empty-on-delete — coordinate, not duplicate.** F-11 adds
    `QdrantStore.delete_collection` and drops the collection when the last config is deleted on the
    request path (`docs/tasks/2026-07-14-embedding-dimension-pin-durability/spec.md:191-196`), and
    defers cascade orphans to F-24. Reuse its method (Q-3); do not re-solve the request-path case.
  - **`chat-uploads` / conversation attachments — cleared.** Conversation attachments have their own
    retention path (`ConversationFacade.purge_old_attachments`, `retention.py:107-109`); not File
    RAG, not in scope.

## 7. Fix Design

Two coordinated parts, sharing one new project-scoped teardown primitive.

**7.1 Project-scoped teardown primitive (row-independent).** Add
`RagConfigService.purge_project_infra(*, project_id)` (or an equivalently named method) that keys
solely on `project_id`:
- MinIO: enumerate `rag-sources/` under the `{project_id}/` prefix via
  `MinioClient.list_objects_sync(bucket, prefix=f"{project_id}/")`
  (`backend/shared_kernel/storage/minio_client.py:162-167`) and remove each object
  (`remove_object_sync`, `:169-175`), idempotent on missing keys.
- Qdrant: drop the whole `rag_{project_id}` collection via a new `QdrantStore.delete_collection(
  project_id)` mirroring `GraphRagVectorStore.delete_collection` (`graphrag_vector_store.py:315-318`:
  `collection_exists` guard + `delete_collection`). Add this method to `QdrantStore` if F-11 has not
  (Q-3).
Best-effort and isolated per store (log + continue), returning a summary. Expose it as
`KnowledgeFacade.purge_project_rag_infra(project_id)` so the worker calls a facade, not application
internals (SoC).

**7.2 Proactive teardown in retention (erasure at hard-delete).** In `_purge_soft_deleted_tenancy`
(`retention.py:141-199`), for each project about to be hard-deleted, call
`KnowledgeFacade.purge_project_rag_infra(project_id)` before the `sa.delete(projects)` cascade. This
matches the module's established cross-context delegation (`ConversationFacade(session).purge_old_
attachments`, `:107-109`; `RetentionService(...).purge_once`, `:82-91`). Keyed on `project_id`, so
row cascade order does not matter; failures are logged and do not abort the tenancy purge (the
backstop sweep reclaims anything missed).

**7.3 Backstop File RAG orphan sweep (mirrors F-8).** Add a new retention policy
`("rag_orphans", _purge_rag_orphans)` in `_POLICIES` (`retention.py:506-531`) that:
- builds the live set = **every** `project_id` in `projects` (regardless of `deleted_at`, per Q-4);
- enumerates candidate MinIO orphans: top-level prefixes under `rag-sources/` (each a raw
  `project_id`) whose UUID is absent from the live set;
- enumerates candidate Qdrant orphans: `get_collections()` names starting `rag_` that are not in
  `{collection_name(pid) for pid in live_set}` (compare against *expected* names to avoid
  underscore-vs-dash parsing ambiguity in the normalized UUID);
- purges each orphan via §7.1's primitive, per-orphan isolated, emitting a
  `rag.orphan_swept` audit; enumeration failure logs and skips that store for the cycle (does not
  abort), exactly like F-8's non-fatal enumeration (`spec.md:128-131`).

**Data repair:** blobs/collections already orphaned before this fix are discovered and purged on the
first backstop sweep after deploy (§7.3), because enumeration reads them directly from MinIO/Qdrant.
No migration or schema change.

## 8. Regression Test Plan

Backend. Extend `backend/tests/unit/test_retention_deep.py` (`TestPurgeSoftDeletedTenancy`,
`:204-251`) and add a focused test for the teardown primitive.

1. **Hard-delete erases File RAG infra (primary red-first).** Set up a soft-deleted project past
   the cutoff with a fake MinIO holding `rag-sources/{pid}/…` objects and a fake Qdrant holding
   `rag_{pid}`; run `_purge_soft_deleted_tenancy`; assert the project's blobs are removed and the
   collection dropped. Fails today — retention touches neither.
2. **`purge_project_infra` primitive** (unit): given a project_id, removes all objects under the
   `rag-sources/{pid}/` prefix and drops `rag_{pid}`, idempotent when they are already absent.
3. **Orphan sweep purges only true orphans** (unit): a `rag_{pid}` collection / `rag-sources/{pid}/`
   prefix whose `pid` has no `projects` row is purged; a `pid` that still has a row (including a
   soft-deleted one) is **not** purged (Q-4 guard).
4. **Non-fatal isolation** (guard): a store enumeration/purge that raises for one orphan or one
   store logs and continues, and does not abort the retention cycle.
5. **`QdrantStore.delete_collection`** (unit, if added here): drops an existing collection and
   no-ops when absent (mirrors the graph-store method).

Primary red-first test: (1).

## 9. Risks and Rollback

- **Over-deletion (erasing live-tenant data).** A bug in the live-set diff could purge a live
  project's data. Mitigated by keying the live set on *every* `projects` row (Q-4), comparing Qdrant
  against *expected* collection names (not parsed UUIDs), and the guard test (§8.3). Teardown at
  hard-delete only runs for rows already selected for permanent deletion.
- **Enumeration cost.** Listing `rag-sources/` prefixes and `get_collections()` each sweep scales
  with tenant count; bounded by running at the retention cadence and skipping on enumeration
  failure. A slower cadence or divergence-gated run is an FU if it proves heavy.
- **F-11 method coordination.** Both specs add `QdrantStore.delete_collection`; if built in
  parallel, reconcile to a single definition (Q-3) to avoid a duplicate/conflicting method.
- **Retention ordering.** Teardown must run before or independently of the Postgres cascade; since
  it is keyed on `project_id` only, it is order-independent, but the call must be inside the same
  purge pass so it runs for exactly the projects being hard-deleted.
- **Rollback** — revert the retention wiring, the new policy, and the primitive/facade method;
  code-only, no schema change. The leak returns but no data is destroyed by the rollback.

## 10. Security Considerations

Routes through `/check-security` before merge (audit FU-1; data-remanence / erasure-failure class).

- **Residue erased:** raw uploaded source documents (`rag-sources/{project_id}/…` — the most
  sensitive residue, actual tenant files) and per-tenant embedding vectors + chunk-link payload in
  `rag_{project_id}` (embeddings can leak content via inversion). Confirm both are gone after
  hard-delete (§8.1).
- **No under-deletion / over-deletion:** the fix must erase exactly the deleted tenant's data and
  nothing live — verified by the live-set guard (§8.3, Q-4).
- **Tenant isolation of the sweep:** the sweep must not cross-purge; project-id prefixing and
  per-project collection naming keep each tenant's blobs/collection isolated. Verify the prefix
  match is exact (`{project_id}/`, not a substring) so one project's prefix cannot match another's.
- **Auditability:** emit `rag.orphan_swept` (and a teardown audit) with the affected `project_id`
  so erasure is provable for compliance, without logging blob contents or keys beyond the
  project scope.

## 11. SRS Delta

None. This restores the tenant-scoped erasure implied by the `purge_documents_infra` teardown
contract and [R10.06]'s per-project collection; no new requirement. If `/check-security` review
concludes the SRS should state an explicit tenant-deletion erasure requirement for File RAG,
draft it there and route it as an SRS amendment.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (durable teardown record):** if proactive teardown fails mid-way, it currently relies on
  the next sweep; a durable failure record drained by the sweep would retry promptly. Complementary
  hardening, not required given the backstop sweep.
- **FU-2 (bounded/gated sweep):** if per-sweep MinIO/Qdrant enumeration is heavy on large
  deployments, gate it on a divergence signal or a slower cadence than the tenancy purge.
- **FU-3 (shared multi-store sweep primitive):** F-8 (graph) and F-24 (File RAG) now both enumerate
  an external store against the Postgres live set; a future refactor could share the sweep skeleton.
