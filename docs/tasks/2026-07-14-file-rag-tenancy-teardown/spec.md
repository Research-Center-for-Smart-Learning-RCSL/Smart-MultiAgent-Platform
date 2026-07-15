---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R10.06]
---

# F-24: File RAG source blobs and the per-project Qdrant collection leak on tenancy deletion

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-24). **Release
blocker** — routes through `/check-security` before merge (audit FU-1).

**Scope note:** verification found the identical leak for Knowledge Map source blobs (a separate
`knowmap-sources` bucket); per Q-5 this spec covers **both** File RAG and Knowledge Map source
blobs, plus the File RAG per-project Qdrant collection. Knowledge/Concept Map *graph* vectors on
tenancy cascade remain F-8's domain.

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
survive tenant deletion indefinitely. Verification found the identical source-blob leak for
Knowledge Maps (a separate `knowmap-sources` bucket), so per Q-5 the fix spans both source buckets.
It does both halves the audit recommends: (a) a proactive, project-scoped teardown (both source
buckets + the File-RAG per-project Qdrant collection) wired into **both** tenant hard-delete paths —
the retention worker and the immediate admin GDPR purge `prepare_hard_delete` (a second path
verification found bypasses retention) — so erasure happens at the deletion moment, and (b) a
backstop orphan sweep keyed on the live Postgres
project set, mirroring F-8's multi-store sweep, to reclaim orphans from any path (including
already-leaked data). Knowledge/Concept Map graph vectors on tenancy cascade remain F-8's domain.

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
| Q-5 | Cover Knowledge Map source blobs (`knowmap-sources`), which leak identically, or keep F-24 File-RAG-only? | **Include `knowmap-sources`** — the teardown primitive and sweep are bucket-parameterized to cover both source buckets. | The same cascade-with-no-teardown gap orphans Knowledge Map uploaded files in a separate bucket (`knowmap_tus_finalizer.py:83-85`); fixing only File RAG would ship an identical tenant-data leak. Knowledge Map *graph* vectors (Neo4j + `graphrag_*` Qdrant) stay F-8's domain — only source blobs are added here. |

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
2. Both tenant hard-delete paths erase those rows via `ON DELETE CASCADE`
   (`0012_rag.py:59-60,97-98,132-133`) without invoking any File RAG teardown: the retention worker
   (`retention.py:141-199`, sweeps only Postgres + the exports bucket) **and** the immediate admin
   GDPR purge `AccountDeletionService.prepare_hard_delete`
   (`backend/contexts/tenancy/application/account_deletion_service.py:156-210`,
   `projects.delete()`/`orgs.delete()` at `:177-187,:198-205`, called from
   `AdminService.hard_delete_user`, `backend/contexts/identity/application/admin_service.py:248`).
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
  - **Immediate admin GDPR hard-delete — confirmed SECOND path, must be hooked too (C1).**
    Verification refuted the assumption that all org/project hard-deletes flow through retention.
    `AdminService.hard_delete_user` (`admin_service.py:221-270`, gated on the *user* being
    soft-deleted ≥60 days) calls `AccountDeletionService.prepare_hard_delete`
    (`account_deletion_service.py:156-210`), which **immediately** hard-deletes any soft-deleted
    orgs/projects (`t.projects.delete().where(deleted_at IS NOT NULL)` `:177-187`; `t.orgs.delete()`
    `:198-205`) — bypassing `_purge_soft_deleted_tenancy`. These two (`account_deletion_service.py`
    and retention's `sa.delete` over `_SOFT_DELETE_TABLES`) are the only hard-delete sites in the
    repo. `AccountDeletionService.cascade_account_deletion` (`:47-132`) only *soft*-deletes. So the
    proactive teardown must be wired into **both** hard-delete paths (§7.2), or "erasure at the
    deletion moment" (Q-2) does not hold for admin GDPR purges — those would rely on the sweep alone.
  - **Knowledge Map source blobs (`knowmap-sources` bucket) — confirmed, same systemic gap
    (see Q-5).** Knowledge Map documents are uploaded to a **separate** bucket via
    `knowmap_source_object_key` + `self._minio.knowmap_sources_bucket`
    (`backend/contexts/knowledge/application/knowmap_tus_finalizer.py:83-85`), with the same
    cascade (`knowmap_documents.knowmap_config_id → knowmap_configs`, project-scoped) and the same
    absence of any retention/tenancy teardown. So the identical raw-file residue occurs for
    Knowledge Maps. Patching only File RAG would be half a fix of one systemic mistake; the
    teardown primitive and sweep (§7) are written bucket-parameterized to cover both `rag-sources`
    and `knowmap-sources`, pending the Q-5 scope confirmation.
  - **Knowledge/Concept Map *graph* stores (Neo4j + `graphrag_*` Qdrant) on tenancy cascade —
    F-8's domain, not here.** Graph data orphaned by a tenancy cascade is discoverable by F-8's
    reconciler sweep (it enumerates external-store keys and diffs against the Postgres live set),
    so it is covered once F-8 ships. This spec covers only the File-RAG/knowmap **source-blob** and
    the File-RAG per-project Qdrant collection, not graph vectors.
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

**7.1 Project-scoped teardown primitive (row-independent).** Add a teardown that keys solely on
`project_id`:
- MinIO source blobs — **both** buckets (Q-5): enumerate each of `rag-sources` and
  `knowmap-sources` under the `{project_id}/` prefix via
  `MinioClient.list_objects_sync(bucket, prefix=f"{project_id}/")`
  (`backend/shared_kernel/storage/minio_client.py:162-167`) and remove each object
  (`remove_object_sync`, `:169-175`), idempotent on missing keys. Match the prefix exactly as
  `{project_id}/` (a bare project-id segment) so one project's prefix cannot match another's.
- Qdrant — File RAG per-project collection only: drop `rag_{project_id}` via a new
  `QdrantStore.delete_collection(project_id)` mirroring `GraphRagVectorStore.delete_collection`
  (`graphrag_vector_store.py:315-318`: `collection_exists` guard + `delete_collection`). Add this
  method to `QdrantStore` if F-11 has not (Q-3). Knowledge Map graph vectors (`graphrag_*`
  collections) are **not** dropped here — they are F-8's domain (§6).
Best-effort and isolated per store (log + continue), returning a summary. Expose it as
`KnowledgeFacade.purge_project_source_infra(project_id)` (naming it "source_infra", since it now
spans File RAG + Knowledge Map source blobs and the File-RAG collection) so the worker calls a
facade, not application internals (SoC).

**7.2 Proactive teardown in both hard-delete paths (erasure at hard-delete).** `_purge_soft_deleted_tenancy`
(`retention.py:141-199`) does not bulk-delete blindly: it iterates `_SOFT_DELETE_TABLES` and for
each table materializes a 200-row `SELECT id … LIMIT 200` batch, then
`DELETE … WHERE id IN (batch)` (`:187-197`). Two facts drive the hook design: (a) it deletes by an
enumerated id set, so the project IDs being erased can be captured; (b) `orgs` is purged **before**
`projects` in the tuple (`:51-57`), and org deletion cascades to its projects (the `org_retained`
guard, `:175-184`, and its `org → project → workspace → chatroom` comment, exist for exactly this),
so a project can vanish via its org before the `projects` iteration runs. Therefore, **before** the
delete loop, enumerate the project IDs that will be erased this pass — the union of directly-purged
projects (`deleted_at < cutoff AND NOT project_retained`) and projects whose `owner_org_id` is in
the orgs purged this pass — materialize them, and call
`KnowledgeFacade.purge_project_source_infra(project_id)` for each. This matches the module's
established cross-context delegation (`ConversationFacade(session).purge_old_attachments`,
`:107-109`; `RetentionService(...).purge_once`, `:82-91`). The teardown is keyed on `project_id`
only, so it is order-independent and idempotent; failures are logged and never abort the tenancy
purge. Perfect batch alignment with the subsequent 200-row deletes is **not** required — any
project missed in a pass is still soft-deleted (caught next pass) or reclaimed by the backstop
sweep (§7.3), which is the completeness guarantee.

**Wire the SAME teardown into the immediate admin GDPR path (C1).** `AccountDeletionService.prepare_hard_delete`
(`account_deletion_service.py:156-210`) hard-deletes soft-deleted orgs/projects immediately, bypassing
retention. Before its `projects.delete()`/`orgs.delete()` (`:177-205`), enumerate the same set
(projects it will delete directly `:177-187` plus projects under the orgs it deletes `:198-205`) and
call `KnowledgeFacade.purge_project_source_infra(project_id)` for each — so admin GDPR purges also
erase source infra at the deletion moment, not merely at the next sweep. This is the load-bearing
path for the "erasure at deletion" guarantee (Q-2), since it is the primary explicit-GDPR route.

**7.3 Backstop source-infra orphan sweep (mirrors F-8).** Add a new retention policy
`("rag_source_orphans", _purge_rag_source_orphans)` in `_POLICIES` (`retention.py:506-531`) that:
- builds the live set = **every** `project_id` in `projects` (regardless of `deleted_at`, per Q-4);
- enumerates candidate MinIO orphans in **both** `rag-sources` and `knowmap-sources`: the top-level
  `{project_id}/` prefixes (each a raw project UUID) whose UUID is absent from the live set. Note
  `MinioClient.list_objects_sync` hardcodes `recursive=True` and exposes no delimiter parameter
  (`minio_client.py:162-167`), so the sweep must either **(a)** add a `recursive=False`/delimiter
  option to that helper (small, in-scope change) to list common prefixes directly, or **(b)** list
  recursively and derive the distinct first path segment (`object_name.split("/", 1)[0]`). Pick one
  and state it in the build;
- enumerates candidate Qdrant orphans: `get_collections()` names starting `rag_` that are not in
  `{collection_name(pid) for pid in live_set}` (compare against *expected* names to avoid
  underscore-vs-dash parsing ambiguity in the normalized UUID);
- purges each orphan via §7.1's primitive, per-orphan isolated, emitting a
  `rag.source_orphan_swept` audit; enumeration failure logs and skips that store for the cycle
  (does not abort), exactly like F-8's non-fatal enumeration (`spec.md:128-131`).

**Data repair:** blobs/collections already orphaned before this fix are discovered and purged on the
first backstop sweep after deploy (§7.3), because enumeration reads them directly from MinIO/Qdrant.
No migration or schema change.

## 8. Regression Test Plan

Backend. Extend `backend/tests/unit/test_retention_deep.py` (`TestPurgeSoftDeletedTenancy`,
`:204-251`) and add a focused test for the teardown primitive.

1. **Hard-delete erases source infra (primary red-first).** Set up a soft-deleted project past the
   cutoff with a fake MinIO holding both `rag-sources/{pid}/…` and `knowmap-sources/{pid}/…` objects
   and a fake Qdrant holding `rag_{pid}`; run `_purge_soft_deleted_tenancy`; assert both buckets'
   blobs are removed and the collection dropped. Fails today — retention touches none of them.
   Add a variant where the project is erased via its **org** (org purged before projects, §7.2) and
   assert teardown still runs for the org-cascade project.
2. **`purge_project_source_infra` primitive** (unit): given a project_id, removes all objects under
   the `rag-sources/{pid}/` and `knowmap-sources/{pid}/` prefixes and drops `rag_{pid}`, idempotent
   when they are already absent.
3. **Orphan sweep purges only true orphans** (unit): a `rag_{pid}` collection or a
   `rag-sources/{pid}/` / `knowmap-sources/{pid}/` prefix whose `pid` has no `projects` row is
   purged; a `pid` that still has a row (including a soft-deleted one) is **not** purged (Q-4 guard).
4. **Non-fatal isolation** (guard): a store enumeration/purge that raises for one orphan or one
   store logs and continues, and does not abort the retention cycle.
5. **`QdrantStore.delete_collection`** (unit, if added here): drops an existing collection and
   no-ops when absent (mirrors the graph-store method).
6. **Admin GDPR hard-delete erases source infra (C1).** Drive `AccountDeletionService.prepare_hard_delete`
   for a user whose soft-deleted org/project hold `rag-sources`/`knowmap-sources` blobs and a
   `rag_{pid}` collection; assert `purge_project_source_infra` runs and all are gone. Fails today —
   `prepare_hard_delete` deletes the rows immediately with no teardown.

Primary red-first test: (1); the admin-path regression is (6).

## 9. Risks and Rollback

- **Over-deletion (erasing live-tenant data).** A bug in the live-set diff could purge a live
  project's data. Mitigated by keying the live set on *every* `projects` row (Q-4), comparing Qdrant
  against *expected* collection names (not parsed UUIDs), and the guard test (§8.3). Teardown at
  hard-delete only runs for rows already selected for permanent deletion.
- **Enumeration cost.** Listing `rag-sources/` and `knowmap-sources/` prefixes plus
  `get_collections()` each sweep scales with tenant count; bounded by running at the retention
  cadence and skipping on enumeration failure. A slower cadence or divergence-gated run is an FU if
  it proves heavy.
- **F-11 method coordination.** Both specs add `QdrantStore.delete_collection`; if built in
  parallel, reconcile to a single definition (Q-3) to avoid a duplicate/conflicting method.
- **Retention ordering.** Teardown must run before or independently of the Postgres cascade; since
  it is keyed on `project_id` only, it is order-independent, but the call must be inside the same
  purge pass (in **both** the retention loop and `prepare_hard_delete`, §7.2) so it runs for exactly
  the projects being hard-deleted.
- **`list_objects_sync` capability gap (C3).** The sweep's prefix enumeration needs a capability the
  recursive-only helper lacks; the build must either add a delimiter/`recursive=False` option or
  derive distinct first-segment prefixes from a recursive listing (§7.3). The §7.1 teardown-by-known-
  prefix is unaffected.
- **Rollback** — revert the retention wiring, the new policy, and the primitive/facade method;
  code-only, no schema change. The leak returns but no data is destroyed by the rollback.

## 10. Acceptance Criteria

- [x] AC-1: The hard-delete-erases-source-infra test (§8.1) fails before the fix and passes after,
  including the org-cascade variant (project erased via its owning org). *(Covered by the pair D-5:
  `test_purge_primitive_*` proves real blob removal + collection drop; `TestRagSourceTeardownWiring.
  test_tears_down_direct_and_org_cascade_projects` proves the direct + org-cascade doomed projects
  reach the teardown.)*
- [x] AC-2: After a project (or its owning org) is hard-deleted by retention, no objects remain
  under `rag-sources/{project_id}/` or `knowmap-sources/{project_id}/`, and the `rag_{project_id}`
  Qdrant collection is dropped. *(`test_purge_primitive_removes_both_buckets_and_drops_collection`.)*
- [x] AC-3: `KnowledgeFacade.purge_project_source_infra(project_id)` purges both source buckets by
  prefix and drops the File-RAG collection, keyed on `project_id` alone (no document rows required),
  idempotent when the data is already absent. *(`test_purge_primitive_*`, incl. `_idempotent_when_absent`.)*
- [x] AC-4: The backstop orphan sweep purges source blobs/collections whose `project_id` has no
  `projects` row, and never purges data for a project still present (including soft-deleted), per
  Q-4 (§8.3). *(`test_list_orphans_returns_only_projects_with_no_row`, `TestPurgeRagSourceOrphans`.)*
- [x] AC-5: Enumeration/purge failures are logged and isolated per orphan/store and never abort the
  retention cycle (§8.4). *(`test_purge_primitive_isolates_*`, `test_list_orphans_skips_failed_store`,
  `test_orphan_purge_failure_is_isolated`.)*
- [x] AC-6: `QdrantStore.delete_collection` exists (added here or reused from F-11) and drops an
  existing collection / no-ops when absent, with no duplicate or conflicting definition versus F-11.
  *(D-1: reused F-11's method; added `QdrantStore.list_collection_names` for the sweep.)*
- [x] AC-7: A `rag.source_orphan_swept` (and a teardown) audit records the affected `project_id`
  without logging blob contents or keys. *(D-4: `KnowledgeFacade.purge_project_source_infra` emits the
  audit with only `project_id` + counts; action `rag.source_infra_purged` / `rag.source_orphan_swept`.)*
- [x] AC-8: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`;
  the `/check-security` review (FU-1) is completed before merge.
- [x] AC-9: The immediate admin GDPR path (`AccountDeletionService.prepare_hard_delete`) also runs
  `purge_project_source_infra` for every org/project it hard-deletes, so admin purges erase source
  infra at the deletion moment (not only via the sweep) — verified by
  `test_admin_gdpr_hard_delete_purges_source_infra`.

## 11. Security Considerations

Routes through `/check-security` before merge (audit FU-1; data-remanence / erasure-failure class).

- **Residue erased:** raw uploaded source documents (`rag-sources/{project_id}/…` **and**
  `knowmap-sources/{project_id}/…` — the most sensitive residue, actual tenant files) and per-tenant
  embedding vectors + chunk-link payload in `rag_{project_id}` (embeddings can leak content via
  inversion). Confirm all are gone after hard-delete (§8.1).
- **No under-deletion / over-deletion:** the fix must erase exactly the deleted tenant's data and
  nothing live — verified by the live-set guard (§8.3, Q-4).
- **Tenant isolation of the sweep:** the sweep must not cross-purge; project-id prefixing and
  per-project collection naming keep each tenant's blobs/collection isolated. Verify the prefix
  match is exact (`{project_id}/`, not a substring) so one project's prefix cannot match another's.
- **Auditability:** emit `rag.source_orphan_swept` (and a teardown audit) with the affected
  `project_id` so erasure is provable for compliance, without logging blob contents or keys beyond
  the project scope.

## 12. SRS Delta

None. This restores the tenant-scoped erasure implied by the `purge_documents_infra` teardown
contract and [R10.06]'s per-project collection; no new requirement. If `/check-security` review
concludes the SRS should state an explicit tenant-deletion erasure requirement for File RAG,
draft it there and route it as an SRS amendment.

## 13. Deviation Log

- **D-1 (Q-3/AC-6 satisfied by reuse):** `QdrantStore.delete_collection(project_id)` already exists
  (`qdrant_store.py:107-116`, shipped by F-11), so the teardown reuses it — no method was added, and
  there is no duplicate/conflict. Separately added `QdrantStore.list_collection_names()` (the sweep
  needs to enumerate `rag_*` collections; no such method existed).
- **D-2 (immaterial spec-drift):** the blob key layout is `{project_id}/{document_id}/{filename}`
  (`rag_source_key`, `minio_client.py:250-252`) and `{project_id}/{config_id}/{sha256}` for knowmap,
  not the `{project_id}/{config_id}/{sha256}` §2 cited for File RAG. Immaterial: the teardown/sweep
  key on the `{project_id}/` first segment, which is unchanged in both buckets.
- **D-3 (C3 resolution — §7.3 option b):** the sweep derives the distinct first path segment from a
  recursive `list_objects_sync` listing (`object_name.split("/", 1)[0]`) rather than adding a
  delimiter/`recursive=False` option to the helper. No shared-helper signature change.
- **D-4 (audit placement, SoC):** the teardown/orphan audit is emitted by
  `KnowledgeFacade.purge_project_source_infra` (a single method with an `audit_action` param —
  `rag.source_infra_purged` for hard-delete, `rag.source_orphan_swept` for the sweep) rather than by
  each caller, keeping the knowledge-domain audit inside the knowledge context. The primitive
  `RagConfigService.purge_project_source_infra` does the store teardown and returns a summary only.
- **D-5 (AC-1 test split):** §8.1's "blobs removed + collection dropped" is proven by the primitive
  test (`test_rag_source_teardown.py`, real fake MinIO/Qdrant), and the retention wiring test
  (`test_retention_deep.py::TestRagSourceTeardownWiring`) proves the direct + org-cascade doomed
  projects reach the teardown. Split for a cleaner unit boundary; together they cover AC-1/AC-2.
- **D-6 (batch-bounded enumeration):** the retention teardown enumerates the doomed set with the same
  `LIMIT 200` bound the delete loop uses (direct projects) plus projects under the 200-org batch, so
  a night's teardown is bounded to roughly the rows actually deleted that pass. Any misalignment is
  reclaimed by the backstop sweep (§7.2 "exact alignment not required").
- **D-7 (DOM-4 ordering — accepted, from the check-quality gate):** the teardown runs its irreversible
  MinIO/Qdrant deletes *before* the Postgres commit (per the approved §7.2 "before the delete loop"),
  inverting the "infra-after-commit" DOM-4 rule the sibling `purge_documents_infra` follows. Accepted
  because: (a) §7.2 explicitly places teardown before the delete loop / row cascade; (b) teardown only
  ever runs for soft-deleted-and-past-cutoff projects or children of a doomed org — never a live
  tenant (independently confirmed by `/check-security`); (c) on a rare mid-pass rollback the already-
  erased blobs belong to a still-condemned soft-deleted row that is re-erased idempotently next pass —
  no data remanence and no live-tenant destruction. The only residue is a cosmetic `blobs_removed: 0`
  on the retry's erasure audit. Restoring DOM-4 audit-durability (commit the row deletes first, then
  teardown+audit) is **FU-4** — deferred because it would deviate from the approved §7.2 ordering and
  the risk is bounded.
- **D-8 (check-quality polish):** applied three non-blocking findings from the gate — reuse one
  `KnowledgeFacade(session)` across the teardown/sweep loops (was per-iteration); dropped a provably-
  dead `or pid in live_project_ids` sub-condition in the orphan round-trip guard (the `name not in
  expected` check already excludes live collections). The whole-bucket in-memory listing is FU-2.

## 14. Follow-ups

- **FU-1 (durable teardown record):** if proactive teardown fails mid-way, it currently relies on
  the next sweep; a durable failure record drained by the sweep would retry promptly. Complementary
  hardening, not required given the backstop sweep.
- **FU-2 (bounded/gated sweep):** if per-sweep MinIO/Qdrant enumeration is heavy on large
  deployments, gate it on a divergence signal or a slower cadence than the tenancy purge.
- **FU-3 (shared multi-store sweep primitive):** F-8 (graph) and F-24 (File RAG) now both enumerate
  an external store against the Postgres live set; a future refactor could share the sweep skeleton.
- **FU-4 (post-commit teardown for DOM-4 audit-durability):** move the hard-delete teardown+audit to
  after the row-delete commit (capture ids in-txn, commit, then teardown in a committed follow-up), so
  a mid-pass rollback never strands erased blobs against a still-present row or produces a misleading
  `blobs_removed: 0` retry audit (see D-7). Bounded risk today (backstop sweep + idempotency cover it).
- **FU-5 (facade hop for the admin router):** the retention worker and admin GDPR path reach the
  knowledge context through `KnowledgeFacade` (correct SoC). Separately, the check-quality gate noted
  the pre-existing pattern where `app/api/v1/graphrag.py` instantiates services directly rather than
  via a facade; pre-existing and out of scope here (tracked on F-26 as its FU-4).
