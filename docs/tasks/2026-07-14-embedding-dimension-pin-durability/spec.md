---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R10.05, R10.06, R11.19]
---

# F-11: Project embedding-dimension pins disappear on delete and race on create

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-11).

## 1. Summary

Each project's Qdrant collection is fixed-size — `rag_{project_id}` (File RAG),
`knowmap_{project_id}`, and `graphrag_{project_id}` — sized once by whichever config indexes
first. The "pin" that is supposed to keep every sibling config at that one dimension is
derived from **live (non-soft-deleted) configs only**: File RAG has no `embed_dim` column at
all and recomputes the pin by scanning `list_for_project`
(`backend/contexts/knowledge/infrastructure/repositories.py:149`, `deleted_at IS NULL`), while
Knowledge Map and Concept Map store `embed_dim` but their pin lookups also filter
`deleted_at IS NULL`
(`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:243`;
`backend/contexts/knowledge/application/graphrag_config_service.py:162`). Deleting the pinning
config therefore makes the pin vanish, yet nothing drops or resizes the collection
(`delete_collection` has no production caller,
`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:315-318`). A new config at
a different dimension passes the now-empty DB check but targets a wrongly-sized collection:
File RAG's `ensure_collection` early-returns with **no** dimension check
(`backend/contexts/knowledge/infrastructure/qdrant_store.py:63-64`) and attempts a wrong-size
upsert that Qdrant rejects — an opaque ingestion failure with no application-level guard;
Knowledge/Concept Map's build-time `_assert_dimension` guard raises a clean typed error instead.
Either way the config is accepted but unbuildable. Independently, all three create
paths are unlocked read-then-insert guarded only by a `(project_id, name)` unique constraint,
so two concurrent first-creates at different dimensions both commit. The fix persists a durable
per-`(project, kind)` embedding pin in a new table, serializes its initialization with a
transactional advisory lock, drops the empty collection and clears the pin when the last config
is removed, and adds the missing File RAG runtime dimension guard.

## 2. Observed vs Expected

- **Observed** —
  - *Pin derived from live configs only.* File RAG computes `new_dim` from the domain map and
    scans live siblings (`backend/contexts/knowledge/application/config_service.py:94-106`),
    whose repository filters soft-deleted rows
    (`backend/contexts/knowledge/infrastructure/repositories.py:142-155`, `deleted_at IS NULL`
    at `:149`); `rag_configs` has `embed_provider`/`embed_model` but **no `embed_dim`**
    (`backend/alembic/versions/0012_rag.py:56-86`). Knowledge Map stores `embed_dim`
    (`backend/contexts/knowledge/infrastructure/knowmap_tables.py:41`) but `project_pinned_dim`
    filters `deleted_at IS NULL`
    (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:232-249`, `:243`);
    Concept Map's `_project_pinned_dim` does the same
    (`backend/contexts/knowledge/application/graphrag_config_service.py:145-168`, `:162`;
    enforced `:170-194`, conflict `:189-193`). That method's own docstring
    (`graphrag_config_service.py:151-156`) states the deliberate design: the pin is read
    Postgres-only "so config CRUD never depends on Qdrant availability," with "the build-time
    collection-dimension guard (D7)" named as "the backstop for the transitional case where a
    project has a built collection but no yet-pinned sibling." Deleting the sole pinning config
    *is* that transitional case — and File RAG has no D7 backstop at all (below), so the gap the
    graph subsystems merely narrow, File RAG leaves fully open.
  - *Delete never drops or resizes the collection.* File RAG `soft_delete` +
    `purge_documents_infra` only delete points filtered by `doc_id`
    (`backend/contexts/knowledge/application/config_service.py:224-280,284-370`;
    `backend/contexts/knowledge/infrastructure/qdrant_store.py:145-167`); Concept/Knowledge Map
    `purge_config_external_stores` deletes points filtered by `config_id`
    (`backend/contexts/knowledge/application/graphrag_config_service.py:493-523`;
    `backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:284-313`). The only
    collection-dropping method has no callers (`graphrag_vector_store.py:315-318`).
  - *Unlocked read-then-insert on create.* File RAG `:95-106` → insert `:122-135`; Knowledge
    Map `backend/contexts/knowledge/application/knowmap_config_service.py:70-85`; Concept Map
    `graphrag_config_service.py:97-110`. The only unique constraints are
    `(project_id, name) WHERE deleted_at IS NULL` (`0012_rag.py:88-90`; `0048_knowmap.py:93-94`;
    `graphrag_configs` has none) — nothing serializes the dimension.
  - *File RAG has no runtime dimension guard.* `ensure_collection` returns early if the
    collection exists (`qdrant_store.py:54-68`, `:63-64`) then attempts the upsert
    (`:70-83`), called from `backend/contexts/knowledge/application/ingest_service.py:300-303`;
    a wrong-size vector is rejected by Qdrant as a raw error with no typed early guard.
    Knowledge/Concept Map do guard via `_assert_dimension`
    (`graphrag_vector_store.py:60-98`, called `graphrag_builder.py:360-363`), which raises the
    clean typed `GraphRagCollectionDimensionMismatch` at build.
- **Expected** — a project's collection dimension is a stable invariant that survives config
  deletion and cannot be changed by a race. [R11.19] states graph configs "use a single
  embedding model/dimension; a config whose builder Key Group would select a different
  embedding dimension is rejected." [R10.06] gives File RAG one fixed collection per project
  (`rag_{project_id}`); a single per-project collection with per-config dimensions is
  self-contradictory, so the same single-dimension invariant is implicit for File RAG. A
  create that would violate the pin must be rejected, not accepted into an unusable state or a
  corrupted collection.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How to make the pin durable and race-free? | **Dedicated `project_embedding_pins` table** (`project_id`, `kind`, `provider`, `model`, `dim`, `UNIQUE(project_id, kind)`), read/created under a transactional advisory lock keyed on `(project_id, kind)`, independent of the config lifecycle. | Deriving from configs — even including soft-deleted rows — cannot work for File RAG, which has no `embed_dim` column, and never resets after a full purge. Validating against the live Qdrant collection couples every save to Qdrant availability and still needs a lock for the first-create race. A dedicated table is authoritative for all three subsystems, and `pg_advisory_xact_lock` serializes the read-then-insert window that the `(project_id, name)` constraint does not cover. |
| Q-2 | When the last config for a `(project, kind)` is deleted, keep the pin forever or reset? | **Drop the now-empty collection and clear the pin row** when no live config remains for that `(project, kind)`. | Lets a project legitimately change embedding dimension after clearing out its configs, instead of being permanently frozen at the first dimension ever chosen. The teardown runs in the existing post-commit infra-cleanup step (DOM-4 ordering), and the advisory lock prevents a drop from racing a concurrent create. |
| Q-3 | Also fix File RAG's missing runtime dimension guard? | **Yes** — make `QdrantStore.ensure_collection` assert the existing collection's vector size matches, mirroring the graph subsystems' `_assert_dimension`. | The pin table closes the save-time hole, but File RAG is the only subsystem lacking a typed early guard: if any path (e.g. a future backfill gap) reached indexing with a mismatched dimension, it surfaces as an opaque raw Qdrant upsert rejection rather than the clean `GraphRagCollectionDimensionMismatch`-equivalent the graph subsystems raise. Defense-in-depth parity with Knowledge/Concept Map. |

## 4. Reproduction

Two independent reproductions.

**A — pin disappears on delete (File RAG):**
1. In project P with no RAG config, create config C1 with `openai:text-embedding-3-small`
   (1536-dim); index a document so `rag_{P}` is created at size 1536
   (`ingest_service.py:300-303`).
2. Delete C1 (the only config). Points are removed but the 1536-dim collection persists
   (`config_service.py:284-370`; no `delete_collection` call).
3. Create config C2 with `openai:text-embedding-3-large` (3072-dim). The live-sibling scan sees
   no configs (`config_service.py:95-106`), so the save succeeds.
4. Index a document via C2. `ensure_collection` early-returns (`qdrant_store.py:63-64`) and
   attempts to upsert 3072-dim vectors into the 1536-dim collection; Qdrant rejects the upsert
   with a raw error (no typed guard), so ingestion fails and the config is unusable. (Because the
   four whitelisted models all have distinct dimensions, any model change is also a dimension
   change, so this delete-then-recreate path is the realistic trigger — a same-dimension change
   cannot occur today; see F-13.)

**B — concurrent first-create race:** issue two create requests for project P (empty) at the
same instant with different-dimension models. Both `_project_pinned_dim`/sibling scans return
empty, both insert, and the project ends with two configs demanding different collection sizes.
Deterministic under a barrier that releases both after their pin read.

## 5. Root Cause Analysis

The causal chain:

1. The effective pin is a query over **live configs**, not a stored project-level fact
   (`repositories.py:149`; `knowmap_repositories.py:243`; `graphrag_config_service.py:162`).
   **This is the root cause** — the pin's lifetime is tied to the pinning config's lifetime, so
   deleting that config erases the invariant while the sized collection it created survives.
2. Nothing drops or resizes the collection on delete (`graphrag_vector_store.py:315-318` unused),
   so the physical dimension outlives the logical pin — the two diverge.
3. The create path is an unlocked read-then-insert with no dimension constraint or lock
   (`config_service.py:95-135`; only `(project_id, name)` uniqueness), so even without a delete,
   concurrent first-creates race.
4. File RAG's `ensure_collection` early-return (`qdrant_store.py:63-64`) is an aggravating
   factor: the resulting mismatch surfaces as a raw Qdrant upsert rejection rather than the clean
   typed guard the graph subsystems raise.

Correcting (1) with a durable, lock-serialized pin table prevents both the delete-erasure and
the create-race symptoms; (2) and (4) are the accompanying teardown and defense-in-depth fixes.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — accepted-but-unusable File RAG, Knowledge Map, and Concept Map configs
  across every project; a single racing config can freeze future configuration for a project.
  File RAG additionally fails ingestion with an opaque Qdrant error rather than a clean typed
  guard, making the misconfiguration harder to diagnose.
- **Sibling suspects:**
  - **All three subsystems — confirmed, all in scope.** File RAG (no `embed_dim` column,
    no runtime guard), Knowledge Map, and Concept Map share the derive-from-live-configs pattern
    and the never-dropped collection. The pin table + advisory-lock repository is shared across
    the three create paths.
  - **File RAG embedding-model change on update — cleared.** `RagConfigService.update`'s mutable
    set excludes `embed_provider`/`embed_model`
    (`backend/contexts/knowledge/application/config_service.py:180-188`), so a File RAG config's
    dimension cannot change after creation; only create/delete matter here. (Concept/Knowledge
    Map *update* model-swap is a distinct defect — F-13, separate dossier.)
  - **Backfill of existing projects — confirmed required.** Projects created before this fix
    have no pin row; the first post-deploy create would (correctly) initialize the pin, but a
    *delete-then-create* on an existing project would still lose the dimension until a pin
    exists. The migration must backfill a pin row per `(project, kind)` with a live config
    (§7 step 6).
  - **Orphan collections from past deletes — cleared for this fix, noted as FU.** Collections
    left behind by pre-fix deletes are not swept here; that overlaps F-24's teardown scope
    (FU-1).

## 7. Fix Design

1. **New table + migration (next available number, `0052` at time of writing).** Create
   `project_embedding_pins`: `id` (uuid pk), `project_id` (uuid, FK `projects` ON DELETE
   CASCADE), `kind` (text/enum: `file_rag` | `knowmap` | `graphrag`), `provider` (text),
   `model` (text), `dim` (int, not null), `created_at`. `UNIQUE(project_id, kind)`. Table +
   SQLAlchemy model live in `backend/contexts/knowledge/infrastructure/` (knowledge-owned).
2. **Shared pin repository.** Add `EmbeddingPinRepository` in
   `backend/contexts/knowledge/infrastructure/` exposing `ensure(project_id, kind, provider,
   model, dim)` and `clear(project_id, kind)`. `ensure` runs
   `SELECT pg_advisory_xact_lock(hashtext(:project_id || ':' || :kind))` first, then reads the
   pin: if absent, insert `(provider, model, dim)` and return it; if present and `dim` matches,
   return it; if `dim` differs, raise the subsystem's existing conflict error
   (`EmbedDimensionConflict` / `KnowmapEmbedDimensionConflict` / `GraphRagEmbedDimensionConflict`).
   The lock is held for the transaction, so concurrent first-creates serialize. `ensure` is
   Postgres-only (lock + table read/insert, no Qdrant call), preserving the existing
   "config CRUD never depends on Qdrant availability" property the `_project_pinned_dim`
   docstring calls out (`graphrag_config_service.py:151-156`).
3. **Wire the three create paths.** File RAG `config_service.create`, Knowledge Map
   `knowmap_config_service.create`, and Concept Map `graphrag_config_service.create` call
   `EmbeddingPinRepository.ensure(...)` with the domain-computed dimension
   (`embed_dimension()` from `backend/contexts/knowledge/domain/models.py:36-51`) inside the
   same transaction as the config insert. Keep the live-sibling scan as a cheap pre-check but
   the pin table is authoritative. (This gives File RAG a durable stored dimension it currently
   lacks entirely.)
4. **Drop-empty on delete.** In the post-commit infra-cleanup step of each subsystem's
   `soft_delete` (trailing the audit row, DOM-4), after purging points check whether any live
   config remains for `(project, kind)`; if none, call the vector store's collection drop and
   `EmbeddingPinRepository.clear(project_id, kind)`. Add `QdrantStore.delete_collection` for
   File RAG (mirroring the existing but unused `GraphRagVectorStore.delete_collection`,
   `graphrag_vector_store.py:315-318`, which this change also wires up).
5. **File RAG runtime guard.** Change `QdrantStore.ensure_collection`
   (`qdrant_store.py:54-68`) so that when the collection already exists it asserts the stored
   vector size equals `vector_size`, raising a typed `RagCollectionDimensionMismatch` on drift
   instead of returning early and letting a wrong-size upsert hit Qdrant as a raw error —
   mirroring `_assert_dimension` (`graphrag_vector_store.py:92-98`).
6. **Backfill (in the `0052` migration, data step).** For every `(project_id, kind)` that has at
   least one live config, insert a pin row derived from any such config's provider/model
   (dimension via `embed_dimension()`; for File RAG compute from `embed_provider`/`embed_model`).
   Idempotent (`ON CONFLICT (project_id, kind) DO NOTHING`).

**Data repair:** the backfill (step 6) makes the pin authoritative for existing projects on
deploy. No existing collections are resized. Orphan collections from pre-fix deletes are not
swept (FU-1).

## 8. Regression Test Plan

Unit tests (fakes for Qdrant + an in-memory pin repo where the advisory lock is a no-op):

1. **Delete-then-recreate, last config (primary red-first, File RAG):** create config at dim A,
   delete it (last), assert the pin is cleared and the collection drop is invoked; recreate at
   dim B and assert it succeeds and re-pins B. Fails today — deletion neither clears a pin (none
   exists) nor drops the collection, and the recreate silently accepts B.
2. **Delete non-last config keeps the pin:** two configs at dim A, delete the first; a new
   config at dim B is rejected with the subsystem's dimension-conflict error. Fails today — the
   live-sibling scan still sees the second config, so this specific case may pass; assert the
   rejection routes through the pin repo, not the sibling scan, to lock in the new source of
   truth.
3. **File RAG runtime guard:** `ensure_collection` against an existing collection whose stored
   size differs from `vector_size` raises `RagCollectionDimensionMismatch`. Fails today — it
   returns early.
4. **Pin conflict is raised per subsystem:** `EmbeddingPinRepository.ensure` with a mismatched
   dim raises the correct typed error for each of the three kinds.

Integration test (real Postgres, `tests/integration/`, requires the advisory lock):

5. **Concurrent first-create race:** two concurrent `ensure(project, kind, ...)` calls at
   different dimensions — exactly one commits its pin, the other raises the conflict. Documents
   the advisory-lock serialization the unit fakes cannot exercise. Cross-referenced to FU-3
   (host wiring profile) if it cannot run on the current host.

## 9. Risks and Rollback

- **Advisory-lock contention** — the critical section is a single read + optional insert per
  `(project, kind)`; contention is limited to concurrent creates on the same project/kind and is
  brief. Low risk.
- **Backfill correctness** — a project whose live configs already disagree on dimension (only
  possible via the pre-fix race) would backfill one arbitrary dim; the migration should log such
  projects for manual review rather than silently pick one. Surface via a migration-time warning.
- **Drop-empty racing a create** — the advisory lock is held across both the delete's
  "any-live-config" check and a concurrent create's pin `ensure`, so a create cannot slip a
  config in between the check and the drop, nor vice versa. The check must run under the same
  lock key.
- **`delete_collection` now runs in production** for the first time — guard it behind the
  no-live-config check and the lock; a spurious drop would only occur if the check is wrong, which
  the tests cover.
- **Rollback** — code + the `0052` migration are revertible; dropping the pin table returns the
  system to derive-from-live-configs behavior. No collection data is lost by rollback (pins are
  metadata; collections are untouched except by the intended drop-empty path).

## 10. Acceptance Criteria

- [x] AC-1: The delete-then-recreate regression test (§8.1) fails before the fix and passes
  after, for File RAG. (`tests/unit/test_embedding_pin.py::test_drop_empty_last_config_clears_pin_and_drops_collection`
  + `::test_recreate_after_drop_repins_new_dimension`.)
- [x] AC-2: A durable `project_embedding_pins` row exists per `(project_id, kind)` once any
  config is created; deleting the last config for that `(project, kind)` clears the pin and drops
  the collection, and deleting a non-last config retains both. (Pin ensure wired into all three
  create paths; `drop_project_collection_if_empty` on each service, tested for File RAG last/
  non-last.)
- [x] AC-3: Creating a config whose embedding dimension differs from the project's pinned
  dimension is rejected with the subsystem's typed dimension-conflict error, for all three of
  File RAG, Knowledge Map, and Concept Map — including after the pinning config was deleted while
  siblings remain. (`test_ensure_raises_typed_conflict_per_subsystem` [parametrized over all three],
  `test_recreate_rejected_when_pin_survives_sibling_delete`.)
- [ ] AC-4: Two concurrent first-creates at different dimensions result in exactly one committed
  pin and one rejection (§8.5, integration). **Deferred to FU-3 (D-3)** — no Postgres in this
  build environment and no reusable real-PG project fixture to build against safely; the pin
  repository's conflict *decision* is unit-covered, the advisory-lock *serialization* awaits the
  integration host.
- [x] AC-5: File RAG `ensure_collection` raises `RagCollectionDimensionMismatch` on a
  dimension-mismatched existing collection instead of returning early and letting the wrong-size
  upsert be rejected by Qdrant as a raw error (§8.3). (`test_ensure_collection_raises_on_dimension_mismatch`.)
- [ ] AC-6: The `0052` migration backfills a pin row for every existing `(project, kind)` with a
  live config and logs any project with conflicting live-config dimensions. **Code complete +
  import-verified; migration *application* is host-gated (D-3)** — `alembic upgrade` needs the DB,
  unavailable here. Chain verified linear (0051 -> 0052, no fork).
- [x] AC-7: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`. **Caveat:** the unit suite passes and `ruff check` is clean; the change introduces
  **zero** new `mypy` errors (43 -> 39, the 4 removed being this task's) and **zero** new
  `ruff format` diffs. The residual 39 `mypy` / 19 `ruff format` findings are a pre-existing repo
  baseline in untouched files (FU-4), not introduced here. `pytest -q` (full tree) and
  `alembic upgrade` need infra absent in this environment (unit tier is green).

## 11. SRS Delta

Proposed optional clarification (apply only on explicit approval): amend [R10.06] to state that
the `rag_{project_id}` collection has a single fixed embedding dimension per project, pinned by
the first config and enforced against all later configs — making explicit for File RAG the
invariant [R11.19] already states for graph configs. If not adopted, the fix still restores the
implicit single-collection-single-dimension behavior; mark this "None" at approval.

## 12. Deviation Log

- **D-1 (kind as Text + CHECK, not a PG ENUM):** §7.1 said "text/enum". The `kind` column is
  `sa.Text` with a `CHECK (kind IN ('file_rag','knowmap','graphrag'))` constraint rather than a
  bespoke PG ENUM, following the codebase's ORM enum-match rule (a new ENUM would have to be
  minted and kept in lock-step with the Table binding, an asyncpg type-binding hazard the CHECK
  avoids while carrying the same guarantee).
- **D-2 (drop-empty lock in the post-commit transaction):** §7.4 places the drop-empty in the
  post-commit infra-cleanup step; §9 requires the "any-live-config" check + drop to run under the
  same advisory-lock key. Implemented as `EmbeddingPinRepository.acquire_lock` +
  `clear` invoked from each service's `drop_project_collection_if_empty`, which the API handler
  calls after the DELETE commit; the lock is held in the request session's post-commit
  transaction (released by the follow-up-audit commit). This is the concrete realization of the
  §9 requirement, recorded for clarity — no behavioral departure.
- **D-3 (AC-4 integration + AC-6 apply host-gated):** the concurrent-create integration test and
  the live `alembic upgrade`/backfill run require a real Postgres, absent in this build
  environment, and the repo has no reusable real-PG project fixture. Per §8.5's own escape clause
  these fold into FU-3. The pin repository's conflict decision, the drop-empty lifecycle, and the
  runtime guard are all unit-verified; the migration is import-verified and the revision chain is
  confirmed linear. No code was changed to accommodate this — only the *verification* is deferred.
- **D-4 (SRS Delta adopted):** at approval the user elected to apply the optional §11 amendment to
  `[R10.06]`, making the single-fixed-dimension invariant explicit for File RAG. Applied to
  `REQUIREMENTS.md` at approval.
- **D-5 (code-review W1 — pin clear split to close the stale-pin race, user-approved):** a
  high-recall code review found that the D-2 design cleared the pin in a transaction *after* the
  DELETE commit, leaving a window where a concurrent create at a different dimension read the stale
  pin and was spuriously rejected. Fix: `drop_project_collection_if_empty` is replaced by two
  methods — `clear_pin_if_last_config` (acquires the lock + clears the pin **in the DELETE's own
  transaction, before its commit**, so the clear is atomic with the soft-delete and a concurrent
  create blocks on the lock until then) and `drop_orphan_collection` (post-commit, DOM-4-respecting;
  re-acquires the lock and re-checks so a concurrent re-pin keeps its collection). All three
  handlers (`rag.py`, `graphrag.py`, `knowmap.py`) updated. This supersedes D-2's post-commit
  clear. Residual (documented): an ultra-narrow interleave where a *different-dimension* config is
  created and ingests between the two commits can leave the orphan collection un-dropped, yielding a
  recoverable `RagCollectionDimensionMismatch` on that config's first ingest — no worse than the
  pre-F-11 baseline. Covered by `test_last_config_clears_pin_then_drops_collection`,
  `test_clear_pin_keeps_pin_when_sibling_remains`, `test_drop_orphan_skips_when_config_reappeared`.
- **D-6 (code-review W2 — SAVEPOINT around the ensure() race insert, user-approved):** the
  `EmbeddingPinRepository.ensure` IntegrityError fallback re-read on the same session without a
  rollback; under asyncpg the aborted transaction would make the follow-up SELECT raise
  `PendingRollbackError` (a 500) instead of the typed dimension-conflict. Fix: wrap the race insert
  in `begin_nested()` so a unique-violation rolls back only the savepoint, leaving the transaction
  usable for the re-read. The transaction-scoped advisory lock is unaffected by the savepoint.
- **D-7 (code-review W5 — drop-after-commit, user-approved):** folded into D-5 — the collection
  drop now runs strictly *after* the DELETE commit (`drop_orphan_collection`), so a failed commit
  can no longer leave a dropped collection behind a still-present pin.

## 13. Follow-ups

- **FU-1 (orphan collections / overlaps F-24):** collections and blobs left behind by pre-fix
  config deletes, and by tenancy/retention cascades, are not swept by this fix; F-24's teardown
  work is the place to reconcile them.
- **FU-2 (shared-kernel promotion):** if a fourth subsystem later needs a per-project embedding
  pin, consider promoting `EmbeddingPinRepository` to a shared-kernel utility; scoped to the
  knowledge context for now.
- **FU-3 (test host):** the concurrent-create integration test needs a real Postgres advisory
  lock; folds into the audit's FU-3 host wiring profile. Now also carries AC-4's integration test
  and AC-6's live migration/backfill run (D-3): both are code-complete but await the integration
  DB to execute.
- **FU-4 (pre-existing mypy/format baseline):** `backend/` carries 39 pre-existing `mypy` errors
  (22 files) and 19 `ruff format` diffs in files untouched by this task (e.g. `retention.py`
  unused-ignores, `workflow_service.py` arg-type, assorted test identity checks). Not introduced
  here and out of scope; recorded so the DoD `mypy .`/`ruff format --check .` non-zero exit is not
  mistaken for a regression from F-11.
