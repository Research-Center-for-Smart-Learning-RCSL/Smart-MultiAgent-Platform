---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.04]
---

# F-9: Phase-2 retries generate new Qdrant point IDs and are not idempotent

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-9).

## 1. Summary

Qdrant point identity is solely the supplied point ID, but both the initial builder and the
reconciler's Phase-2 retry mint a fresh `uuid4()` per entity on every attempt. So when a
Qdrant upsert commits but the client times out, the retry inserts *new* points for the same
`(config_id, build_id)` entities instead of overwriting the originals — duplicates that
survive until the best-effort, non-fatal supersede-by-entity-name sweep happens to run.
Retrieval requests `top_k` candidates *before* deduplicating entity names, so duplicate
points consume candidate slots and shrink the effective seed set, reducing recall. This
directly violates §11.2a step 3 of `REQUIREMENTS.md`: "Qdrant upserts are **idempotent on
`point_id`**." The fix derives the point ID deterministically from
`(config_id, build_id, entity)` at both mint sites, so a retry reproduces the original IDs
and upserts in place — true idempotency — while the existing supersede sweep continues to
collapse cross-build copies.

## 2. Observed vs Expected

- **Observed** — `upsert_entities` keys each Qdrant `PointStruct` solely on the supplied
  `id=str(pid)` (`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:131,144-148`);
  identity is the point ID, nothing derives from the payload. The initial builder mints
  `EntityEmbedding(point_id=uuid.uuid4(), ...)` per entity
  (`backend/contexts/knowledge/application/graphrag_builder.py:554-562`), passed verbatim as
  `points` (`:368`). The reconciler Phase-2 retry mints fresh `uuid4()` again on every attempt
  (`backend/app/workers/graphrag_reconciler.py:107-113`, IDs at `:112`), inside the backoff
  loop (`graphrag_reconciler.py:292-303`). The only thing that removes the resulting
  duplicates is the best-effort, non-fatal `delete_superseded_entities` at build finalize
  (`graphrag_builder.py:422-434` → `graphrag_vector_store.py:226-282`), which is name-scoped
  and skipped on failure. Retrieval requests `top_k` at the Qdrant layer first
  (`backend/contexts/knowledge/application/graphrag_retrieve.py:117-122`, `limit=top_k` at
  `graphrag_vector_store.py:178`) and dedups entity names only afterward (`graphrag_retrieve.py:126-128`),
  so duplicates occupying top-`k` slots yield fewer than `top_k` distinct seeds.
- **Expected** — §11.2a step 3: Phase-2 Qdrant upserts are idempotent on `point_id`, so
  retrying a build's Phase 2 (the reconciler's whole purpose, §11.2a step 5) replaces rather
  than duplicates. [R11.04] requires a build to be transactional across Neo4j and Qdrant
  without leaving inconsistent state; accreting duplicate vectors on ambiguous network
  failures is exactly the inconsistency the 2PC is meant to avoid.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Deterministic point IDs vs a post-retry supersede cleanup? | **Deterministic point IDs** derived from `(config_id, build_id, entity)`. | Directly satisfies §11.2a "idempotent on `point_id`" and fixes both mint sites (builder + retry) with one shared helper. A retry — or a partially-committed original upsert — reproduces the same IDs and overwrites in place, so no duplicate is ever created within a build. The supersede-cleanup alternative keeps IDs random and only removes duplicates after the fact, remaining vulnerable in the window before the (best-effort) sweep. |
| Q-2 | Key on `(config_id, build_id, entity)` or `(config_id, entity)` (build-agnostic)? | **Include `build_id`.** | Idempotency is required *per build's* Phase-2 retry; a build-agnostic key would make a new build overwrite the previous build's points before Phase-2 success, destroying the pre-cutover state the 2PC/rollback relies on. Cross-build dedup stays the job of the existing name-scoped supersede sweep (`graphrag_vector_store.py:226-282`), which is unchanged. |

## 4. Reproduction

Preconditions: a config in `failed_compensating` whose Phase-2 Qdrant upsert will be retried;
a fake Qdrant keyed by point ID (a dict) so duplicate IDs are observable.

1. Run the builder's Phase-2 upsert for build `B` with entities `{X, Y}`; two points land.
2. Simulate the ambiguous failure: the reconciler retry runs `_phase2` for the same `(config,
   B)` (`graphrag_reconciler.py:292-303` → worker `:107-113`).
3. Observe four points for two entities — the retry minted new `uuid4()` IDs
   (`workers/graphrag_reconciler.py:112`) rather than overwriting.
4. Query retrieval with `top_k=2`: both slots can be filled by duplicate points of `X`;
   `dict.fromkeys` (`graphrag_retrieve.py:128`) collapses them to a single seed, so `Y` is
   never traversed.

Deterministic under the fake.

## 5. Root Cause Analysis

The causal chain:

1. Qdrant identity is the supplied point ID with no payload-derived key
   (`graphrag_vector_store.py:131,144-148`).
2. Both mint sites use `uuid4()` — random per attempt (`graphrag_builder.py:556`;
   `workers/graphrag_reconciler.py:112`). **This is the root cause** — the earliest link
   whose correction (deterministic IDs from `config_id/build_id/entity`) makes every retry
   and every partial-commit overwrite in place, eliminating duplicates at the source.
3. The name-scoped, best-effort supersede sweep (`graphrag_vector_store.py:226-282`) is an
   aggravating factor: it is the *only* current duplicate remover and is skipped on failure
   or when a reconciler-recovered build has no entity list.
4. Retrieval's `top_k`-before-dedup ordering (`graphrag_retrieve.py:117-128`) is a symptom
   surface that turns residual duplicates into lost recall; the dedup at `:126-128` stays as
   defense-in-depth and needs no change once (2) removes intra-build duplicates.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — storage growth and degraded graph retrieval after ambiguous Qdrant
  network failures, for every Concept Map and Knowledge Map (shared engine and shared vector
  store).
- **Sibling suspects:**
  - **Both mint sites must change together — confirmed.** Fixing only the builder or only the
    retry re-opens the gap: the retry must reproduce the builder's original IDs for a partial
    original upsert to be overwritten. Both use the *same* helper with the *same* formula.
  - **Knowmap vector store — same code path, covered.** Knowledge Maps use the same
    `GraphRagVectorStore` (with the `knowmap` prefix) and the same builder/reconciler; the
    deterministic helper applies uniformly.
  - **Supersede sweep (`graphrag_vector_store.py:226-282`) — unchanged, still needed.** It
    remains responsible for collapsing *cross-build* copies (different `build_id` ⇒ different
    IDs by design); this fix does not remove it.
  - **Retrieval slot-consumption for cross-build stale points — partially residual.** Until
    the supersede sweep runs, stale points from a *prior* build still exist and can consume
    top-`k` slots. F-10's read-gate (only read fully-committed state) and the unchanged
    supersede sweep together bound this; fully eliminating cross-build slot waste (e.g. read
    filtered to the active build) is out of scope here — cross-linked as FU-1.

## 7. Fix Design

1. **Add a deterministic point-ID helper in the domain layer.** Add
   `deterministic_point_id(config_id: UUID, build_id: UUID, entity: str) -> UUID` to
   `backend/contexts/knowledge/domain/graphrag.py` (which already hosts `BuildState`, and is
   importable by both the application builder and the `app/workers` retry without crossing
   layer boundaries). Implement as `uuid.uuid5(_POINT_NAMESPACE, f"{config_id}:{build_id}:{entity}")`
   with a fixed module namespace constant (e.g. `_POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
   "smap:graphrag:entity-point")`). Pure function, no I/O.
2. **Use it in the builder.** Thread `build_id` into `_embed_entities`
   (`graphrag_builder.py:528-562`; the caller already holds `build_id` in `_run_locked`) and
   replace `point_id=uuid.uuid4()` (`:556`) with
   `point_id=deterministic_point_id(cfg.id, build_id, entity)`. `cfg.id` is the config id and
   `entity` is already in the comprehension.
3. **Use it in the reconciler retry.** Replace the `_uuid.uuid4()` in the point tuple
   (`backend/app/workers/graphrag_reconciler.py:112`) with
   `deterministic_point_id(cfg.id, build_id, entity)` — `cfg` and `build_id` are already in
   scope in `_make_phase2_retry`, and `entity` is the first element of each `pairs` item.
4. **Leave `upsert_entities`, the supersede sweep, and retrieval unchanged.** Qdrant's upsert
   already replaces on matching ID; with deterministic IDs the idempotency contract holds
   without touching the write path. The retrieval dedup (`graphrag_retrieve.py:126-128`) stays
   as-is.

**Data repair:** existing duplicate points from past retries are collapsed by the supersede
sweep on the next successful build per config (name-scoped, keeps the newest build's copy);
no migration is required. A one-off rebuild per config accelerates cleanup but is optional
(FU-2).

## 8. Regression Test Plan

Unit tests:

1. **Helper determinism** (`backend/tests/unit/test_graphrag_domain.py` or the builder test
   module): `deterministic_point_id(c, b, e)` is stable across calls and differs when any of
   `config_id`, `build_id`, or `entity` differ. Small pure-function test.
2. **Retry idempotency** (primary red-first test, `test_graphrag_reconciler.py`): a fake
   vector store backed by a dict keyed on point ID; run Phase-2 upsert for `(config, B)` with
   entities `{X, Y}`, then re-run the retry `_phase2` for the same `(config, B)`; assert the
   store holds exactly two points (one per entity), not four. Fails today — `uuid4()` at
   `workers/graphrag_reconciler.py:112` mints distinct IDs, doubling the points.
3. **Builder determinism** (`test_graphrag_builder.py`): assert `_embed_entities` for a given
   `(cfg, build_id)` yields the ID `deterministic_point_id(cfg.id, build_id, entity)` for each
   entity. Fails today — IDs are random.

## 9. Risks and Rollback

- **ID collisions** — `uuid5` over `{config_id}:{build_id}:{entity}` collides only on
  identical entity strings within one build, which already map to one logical point (dedup by
  name is intended); no false collision across configs/builds.
- **Entity-string sensitivity** — the key uses the exact `entity` string that becomes the
  payload `entity`; ensure the builder and retry pass the *same* normalization (both use the
  raw entity from `build_entity_descriptions`/Neo4j triples — verify parity in the retry
  path, `workers/graphrag_reconciler.py:59-75`).
- **Cross-build behavior preserved** — different `build_id` ⇒ different IDs, so the existing
  supersede sweep still governs cross-build cleanup; no change to that contract.
- **Rollback** — revert both mint sites to `uuid4()` and drop the helper; code-only, no
  schema change. Points written with deterministic IDs remain valid Qdrant points after
  rollback.

## 10. Acceptance Criteria

- [x] AC-1: The retry-idempotency regression test (§8.2) fails before the fix and passes
  after. (`test_reconciler_phase2_retry_is_idempotent_on_point_id` — 4 points before, 2 after.)
- [x] AC-2: Re-running a build's Phase-2 upsert for the same `(config_id, build_id)` results
  in one Qdrant point per entity (no duplicates), for both Concept Maps and Knowledge Maps.
  (Same helper used by both mint sites; Knowledge Maps share `GraphRagVectorStore`/builder/retry.)
- [x] AC-3: `deterministic_point_id` is stable for equal inputs and distinct when any of
  `config_id`, `build_id`, or `entity` differ (§8.1). (`test_deterministic_point_id_is_stable_and_varies`.)
- [x] AC-4: The builder and the reconciler retry produce identical point IDs for the same
  `(config_id, build_id, entity)` (§8.3), so a partially-committed original upsert is
  overwritten on retry rather than duplicated. (Both call the one pure helper;
  `test_builder_upserts_deterministic_point_ids` pins the builder side.)
- [x] AC-5: `pytest -q` (38 passed) and `ruff check . && ruff format --check .` pass in
  `backend/`. `mypy .` introduces no new errors in the touched files; 37 pre-existing baseline
  errors remain across 21 unrelated files (none in F-9's files) — see D-2.

## 11. SRS Delta

None. This restores §11.2a step 3's "Qdrant upserts are idempotent on `point_id`" contract.

## 12. Deviation Log

- **D-1 (test location):** §8 and §8.2/§8.3 named `backend/tests/unit/test_graphrag_reconciler.py`,
  which does not exist. The reconciler's existing rollback/retry/orphan-sweep coverage (and its
  fakes) live in `backend/tests/unit/test_graphrag_builder.py`; the three F-9 tests were added
  there, reusing those fakes rather than duplicating ~250 lines into a new file. This satisfies
  the spec's stated intent ("extend the existing rollback coverage"). The §8.1 helper test used
  the offered alternative (the builder test module) rather than a new `test_graphrag_domain.py`.
- **D-2 (mypy baseline):** AC-5 asks for a clean `mypy .`. The repo carries a pre-existing
  baseline of 37 mypy errors across 21 files (e.g. `contexts/keys/application/group_service.py`,
  `app/workers/tasks/retention.py`, several test modules). None are in any file this task
  touched, and the change adds none. Not fixed here (out of scope; would require editing
  unrelated files) — recorded as FU-3.

## 13. Follow-ups

- **FU-1 (F-10 interaction):** cross-build stale points can still consume `top_k` slots until
  the supersede sweep runs; F-10's read-gate and a future active-build read filter would
  eliminate the residual slot waste. Separate finding.
- **FU-2 (deploy data repair):** optionally trigger one rebuild per config after deploy to
  accelerate collapse of pre-existing duplicate points; the supersede sweep otherwise cleans
  them on the next natural build. No migration.
- **FU-3 (mypy baseline cleanup):** the backend `mypy .` gate is red on baseline with 37
  pre-existing errors across 21 files; a dedicated pass should drive it to green so the gate is
  meaningful. Out of scope for a bugfix that touches none of those files.
