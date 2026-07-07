---
type: bugfix
status: draft
created: 2026-07-07
requirements: [R11.02, R11.03, R11.04, R11.16, R11.18, R11.19]
---

# GraphRAG Phase 2a — Builder Hardening (windowing, embed-dim pin, lock/timeout, enqueue dedup, owner invariant)

## 1. Summary

Phase 2a fixes a cluster of root-caused defects in the GraphRAG build path that are
latent at today's scale but become active failures once the two-axis redesign multiplies
owners, members, and history volume. The anchor defect (D2) is a **live** correctness bug:
the embedding provider/model — and therefore the vector dimension — is derived at build
time from whichever key happens to sort first in the builder key group, while every config
in a project writes into a **single** per-project Qdrant collection whose dimension is
fixed at first create. Swapping the group's embedding key from OpenAI (1536-dim) to Gemini
(768-dim) silently produces vectors the collection rejects or, worse, mis-indexes. The
remaining defects harden the same path: bounded build windowing (D1), a lock/timeout that
cannot expire mid-build (D3), a trigger fan-out that stays correct once membership joins
land (D4), enqueue de-duplication (D5), and an owner→project tenancy invariant that
survives the move off `agent_id` (D6). Four lower-severity hardening items (D7–D10) are
folded in.

This phase introduces no new user-facing behavior — it restores the intent already
documented in R11.16/R11.18/R11.19 and the parent blueprint. **SRS Delta = None.**

**Phase dependencies (hard):** Phase 2a is built on top of Phase 0 (engine de-concreting,
`list_ordered_carried` embed-key consolidation) and Phase 1 (`owner_kind` typed-FK owner,
singleton `agent_group`, membership join in `list_for_agents`, migrations 0043/0044).
Citations below reflect the pre-Phase-2a tree; where a Phase 0/1 change moves a seam, the
fix design says so.

## 2. Observed vs Expected

### D1 — Unbounded build window (V-1, HIGH)
- **Observed** — `_DbDeltaLoader.load` accumulates the entire delta into one in-memory
  list (`app/workers/tasks/graphrag.py:60`, appended per 2000-row page at `:100-101`, loop
  exits only when a short page arrives `:102-103`) and hands the whole thing to a single
  extraction + single embedding batch. `_BATCH_SIZE = 2000` (`:50`) is a DB fetch page, not
  a build window. A first build over a large chatroom history materialises unbounded rows
  and issues one extractor call and one `embed_batch` sized to the whole corpus.
- **Expected** — the build processes history in bounded windows so memory and per-call LLM
  payload stay flat regardless of history size, while the commit remains one atomic 2PC
  (R11.16: build is idempotent and atomic per config).

### D2 — Embed dimension drift (V-2, CRITICAL)
- **Observed** — `_resolve_embed_key` returns `(provider, _EMBED_MODEL[provider], key_id)`
  for the **first** embedding-capable key in the builder group
  (`app/workers/tasks/graphrag.py:110-133`, provider read at `:129`, model looked up in the
  hardcoded `_EMBED_MODEL` map `:30-34`). The dimension is never recorded — it lives only
  implicitly as the Qdrant collection's `VectorParams.size`, set once at first
  `ensure_graphrag_collection` (`contexts/knowledge/infrastructure/graphrag_vector_store.py:49-62`,
  early-return without dimension re-check at `:57`). The collection is **per project**
  (`graphrag_collection_name(project_id):31-33`) and shared by every config in the project
  (points tagged with `config_id` in payload). `graphrag_configs` has **no** embed columns
  (`contexts/knowledge/infrastructure/graphrag_tables.py:14-54`). Model/dim map:
  openai→text-embedding-3-small (1536), gemini→text-embedding-004 (768), voyage→voyage-3
  (1024).
- **Observed failure** — changing the group's first embedding key from OpenAI to Gemini
  changes the resolved dimension 1536→768; the next build embeds at 768 and upserts into a
  1536-dim collection.
- **Expected** — a project pins exactly one embedding model/dimension; a build that would
  produce a different dimension is rejected at config create/update (4xx) or fails loudly at
  build, never silently mis-indexes (R11.18: stable, project-consistent vector space).

### D3 — Lock TTL == job timeout, no mid-build refresh (V-3, HIGH)
- **Observed** — `job_timeout = 600` (`app/workers/main.py:271`) equals `LOCK_TTL_S = 600`
  (`contexts/knowledge/application/graphrag_builder.py:59`). The build lock is refreshed only
  around the two commit points (`graphrag_builder.py:240`, `:277`), never during extraction
  or embedding. A build whose extract+embed exceeds the TTL lets the lock expire while the
  job is still running, so the reconciler or a second trigger can start a concurrent build.
- **Expected** — the lock cannot silently expire while a build holds it; the lock, not the
  job timeout, is the authoritative single-writer guard (R11.16).

### D4 — Trigger fan-out not DISTINCT (V-4, MEDIUM; load-bearing post-Phase-1)
- **Observed** — the per-config trigger counter path (`graphrag_triggers.py:52` list,
  `:56-60` increment) assumes one config per agent (`graphrag_configs.agent_id` UNIQUE,
  `graphrag_tables.py:21-27`). Phase 1 replaces `list_for_agents` with a membership join
  (`agent_groups` → `agent_group_members`). With singleton groups the join yields one row
  per config, but once Phase 2b makes groups multi-member, an agent set that includes two
  members of the same group returns that config twice → double increment / double enqueue.
- **Expected** — a config is counted and enqueued at most once per trigger batch regardless
  of how many of its group's members are in the batch (R11.19).

### D5 — No enqueue de-duplication (V-5, HIGH)
- **Observed** — the two trigger sites (`turn_engine.py:1272-1281`, `messages.py:300-319`)
  enqueue a build per eligible config with no job identity; concurrent turns enqueue
  duplicate `graphrag_build` jobs for the same config. The build lock serialises them, but
  the loser burns a worker slot spinning up, acquiring the lock, finding a fresh watermark,
  and no-op'ing — and races the pre-build snapshot. `shared_kernel/queue.py:29-33` already
  forwards kwargs to `pool.enqueue_job`, so `_job_id` is available but unused.
- **Expected** — at most one queued build per config at a time; a legitimate rebuild after
  completion is not suppressed (R11.16).

### D6 — Owner→project invariant only checks agent (V-7, HIGH)
- **Observed** — config create validates only that the **agent** belongs to the project
  (`graphrag_config_service.py:74`). After Phase 1 the owner is an `agent_group` (and Phase
  2b adds chatroom/workspace owners); nothing verifies the owner entity belongs to the
  config's project, permitting a config that points a project's builder at an owner in
  another tenant.
- **Expected** — create/update rejects any owner not in the config's project, for every
  `owner_kind` (blueprint AC-9a; multi-tenant AuthZ rule in CLAUDE.md).

### D7–D10 — folded hardening
- **D7 (FU-6)** — `ensure_graphrag_collection` is check-then-create with a TOCTOU window and
  early-returns without re-checking dimension (`graphrag_vector_store.py:49-62`, `:57`).
  Expected: idempotent create and a loud raise on dimension mismatch (also the build-time
  half of D2's guard).
- **D8 (B3)** — `graphrag_build` runs on the default Arq lane shared with workflow/RAG/
  notification tasks (`app/workers/main.py`); a burst of builds can starve other lanes.
  Expected: GraphRAG build concurrency is bounded so it cannot monopolise the pool.
- **D9 (FU-4)** — the reconciler constructs a builder inline at four points
  (`graphrag_reconciler.py:102,155,192,251`). Expected: one construction seam (builds on
  Phase 0 WS1's injected repo Port). Verify-first: if Phase 0 already routed all four
  through the injected factory this reduces to asserting no inline construction remains.
- **D10 (FU-7)** — triggers that fire while a build is `running` are skipped
  (`graphrag_triggers.py:58`); the counter/watermark interaction across a running build has
  no defined carry semantics, so deltas arriving mid-build may be under- or double-counted
  on the next cycle. Expected: explicit "pause and carry" — mid-build deltas are picked up
  by exactly one subsequent build.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How to fix the embed-dimension conflict (blueprint Open Q-F)? | Pin one model/dimension per project and record it in Postgres; validate at create/update, 4xx on mismatch. | Sharding a collection per config fragments cross-config retrieval and multiplies collections across 7 name call sites; a loud-only guard cannot prevent misconfiguration at create. The per-project collection already forces one dimension — make it explicit and enforced. Matches the `rag` config exemplar which stores `embed_provider`/`embed_model` (`contexts/knowledge/infrastructure/tables.py:34-35`). |
| Q-2 | How wide is Phase 2a's scope? | CRITICAL/HIGH (D1–D6) plus the MEDIUM/LOW hardening D7–D10. | Do the adjacent hardening while the build path is open and under test, rather than reopening it in a later phase. Each folded item is scoped with an explicit non-goal below. |
| Q-3 | Should embed provider/model be a new user choice or derived-and-frozen? | Derived once from the builder key group, then frozen as the project pin; no new create-time UI/API field in this phase. | Keeps Phase 2a a bugfix (restores stable-dimension intent) without opening a product surface; a user-selectable embedding model is deferred to the Knowledge Map phase. |
| Q-4 | Window boundary unit? | Token-estimate budget with a hard message-count cap per window. | Token budget bounds the LLM payload (the real constraint); the message cap bounds the DB/keyset step and guarantees progress on pathological single-message sizes. |

## 4. Reproduction

- **D2 (deterministic)** — In a project, create a GraphRAG config whose builder key group's
  first embedding key is OpenAI; run a build (collection created at 1536). Revoke/remove the
  OpenAI key so the first embedding key becomes Gemini; trigger a rebuild. `_resolve_embed_key`
  now returns 768-dim; `embed_batch` yields 768-dim vectors upserted into the 1536-dim
  per-project collection → Qdrant dimension error (or silent mis-index if the client does not
  validate). Precondition: builder group holds ≥2 embedding keys of different providers.
- **D1 (load-observable)** — Point a config at a chatroom with a very large message history
  and trigger a first build (`since=NULL`). `_DbDeltaLoader.load` materialises the full
  history and issues one extractor + one `embed_batch`; peak memory and single-call payload
  scale with history size.
- **D3 (timing)** — A build whose extract+embed phase exceeds 600s: the lock TTL lapses
  between the two refresh points while the job still runs; a concurrent trigger/reconciler
  acquires the lock. Non-deterministic — depends on corpus size and provider latency;
  hypothesis is elapsed-time-vs-TTL, not ordering.
- **D5 (concurrency)** — Two turns complete near-simultaneously for the same agent; both
  trigger sites enqueue `graphrag_build` for the same config; two jobs run, the second
  no-ops under the lock. Observable as duplicate enqueue log lines / worker spans.
- **D4/D6/D7–D10** — latent; covered by targeted tests in §8 rather than a live repro.

## 5. Root Cause Analysis

- **D2 root cause** — the embedding identity (provider, model, dimension) is a **build-time
  derivation from mutable key-group ordering** rather than a **persisted, project-scoped
  invariant**. The earliest correcting link is to make (provider, model, dim) a stored
  property of the config, pinned per project, and select the key to match it — not derive
  the model from whichever key sorts first. `list_ordered` at `graphrag.py:124` is the
  ordering seam (Phase 0 WS2 moves this to `list_ordered_carried`; the pin sits on top).
- **D1 root cause** — `load()` returns a single unbounded list (`graphrag.py:52-107`); there
  is no window abstraction between "fetch a page" and "process the build." Correcting link:
  yield bounded windows and drive extraction/accumulation per window, keeping one commit.
- **D3 root cause** — the lock refresh cadence is tied to commit points, not to elapsed
  time, and `job_timeout` offers no headroom over `LOCK_TTL_S`. Correcting link: refresh the
  lock on every window boundary and give `job_timeout` headroom so the lock is authoritative.
- **D4 root cause** — the fan-out relies on `agent_id` uniqueness that Phase 1 removes; the
  correcting link is a `DISTINCT` config set at the resolver, independent of member count.
- **D5 root cause** — enqueue carries no job identity; correcting link is a stable `_job_id`
  per config with a rebuild nonce so completion does not wedge the next build.
- **D6 root cause** — the project-membership check is written against `agent`, the one owner
  kind that existed; correcting link is a kind-dispatched `_assert_owner_in_project`.
- **D7** aggravates D2 (silent early-return hides dimension drift). **D8/D9/D10** are
  independent hygiene items, not causes of D1–D6.

## 6. Blast Radius and Sibling Suspects

- **D2 blast radius** — every project whose builder group has held >1 provider's embedding
  key; any already-built collection whose stored points may mix dimensions. Data-repair plan
  in §7.
- **Embed-resolution siblings** — `_resolve_embed_key` (`graphrag.py:110-133`) and the
  builder's `_embedder_factory` path (`graphrag_builder.py:439`). Both must honor the pin.
  Confirmed these are the only two embed-selection seams; the retrieval path
  (`graphrag_retrieve.py`) reads vectors, does not create them — cleared.
- **Enqueue siblings (D5)** — exactly two trigger sites: `turn_engine.py:1272-1281` and
  `messages.py:300-319`. Both must pass the same `_job_id`. Confirmed no third enqueue site
  for `graphrag_build`.
- **Collection-name siblings (D7)** — 7 call sites of `graphrag_collection_name`
  (`graphrag_vector_store.py:56,97,128,166,207,251,268`); only the create seam (`:49-62`)
  needs the dimension guard; the rest read/scope by name — cleared.
- **Owner-check siblings (D6)** — create (`graphrag_config_service.py:74`) and update
  (`:173-199`). Update must apply the same invariant if it ever accepts an owner change;
  today owner is immutable post-create (mirrors the old immutable `agent_id`) — the invariant
  is enforced at create and re-asserted on update defensively.

## 7. Fix Design

### D2 — pin embed model/dimension per project (anchor)
1. **Schema (migration 0045, expand-only, nullable):** add `embed_provider TEXT`,
   `embed_model TEXT`, `embed_dim INTEGER` to `graphrag_configs`
   (`graphrag_tables.py` mirrors the columns; ORM enum/type rule — plain Text/Integer,
   no PG ENUM needed). Nullable so existing rows are legal; new/updated configs always set
   them. Follows the `rag` config exemplar (`tables.py:34-35`).
2. **Create/update invariant** (`graphrag_config_service.py`): resolve the builder key
   group to `(provider, model, dim)` from the known map (openai 1536 / gemini 768 / voyage
   1024). If the project already has a pinned dimension — read from any sibling config's
   `embed_dim`, else from the live collection's `VectorParams.size` — require equality; on
   mismatch raise a 4xx domain error ("project pinned to N-dim embeddings"). Persist the
   resolved triple on the config.
3. **Build path** (`graphrag.py`): `_resolve_embed_key` takes the config's pinned
   `(provider, model)` and selects the first **carried** (Phase 0 `list_ordered_carried`)
   key matching that provider; if none, fail loudly ("no key for pinned provider") rather
   than silently switching. Legacy null-pin rows self-pin on first successful build: after
   the first `embed_batch`, if `embed_dim IS NULL`, persist `len(vector)` and the resolved
   provider/model in the same transaction as the commit.
4. **Not a mask** — the fix removes the drift source (mutable derivation) instead of catching
   the symptom at upsert. The D7 collection-dimension guard is the defence-in-depth backstop.
5. **Data repair** — one-off reconcile: for each project, if the live collection dimension
   disagrees with the newly pinned dimension, log and quarantine (do not auto-drop vectors);
   surface via the reconciler so an operator decides. No blind vector deletion.

### D1 — bounded windowing, single commit
- Convert `_DbDeltaLoader.load` into `iter_windows` yielding bounded windows (token budget +
  message-count cap, Q-4), keyset-paged on `(created_at, id)` exactly as today. The builder
  consumes all windows for one build, accumulating triples, then performs **one**
  `apply_triples`, **one** `_embed_entities`, **one** pre-build snapshot, **one** `build_id`,
  **one** Qdrant supersede. `last_build_at` advances **once**, at the end — never per window.
  All 2PC/compensation invariants are preserved; only the extract/embed input is chunked.

### D3 — lock authoritative over timeout
- Refresh the build lock on every `iter_windows` boundary (natural cadence from D1). Raise
  `job_timeout` to give headroom over `LOCK_TTL_S` (e.g. `job_timeout` ≥ 3×TTL) so the lock,
  refreshed continuously, is the single-writer authority and the job timeout is only a
  runaway backstop. Keep `LOCK_TTL_S` comfortably above one window's worst case.

### D4 — DISTINCT resolver
- The resolver that returns the trigger config set (`graphrag_triggers.py:52`, backed by the
  Phase 1 membership join) selects `DISTINCT` config ids so member count never inflates the
  count. Harmless with singleton groups; correct before Phase 2b.

### D5 — enqueue dedup with rebuild nonce
- Both trigger sites pass `_job_id="graphrag:build:{config_id}:{nonce}"` where `nonce` is a
  monotonic per-config rebuild watermark (e.g. a small counter or `last_build_at` epoch
  bucket) so a completed job's retained id (`keep_result = 3600`, `main.py:273`) does not
  suppress the next legitimate rebuild, while concurrent triggers within the same watermark
  collapse to one job. Arq returns `None` when a job with that id already exists —
  de-dup is the natural result.

### D6 — owner→project invariant
- Add `_assert_owner_in_project(owner_kind, owner_id, project_id)` invoked at create
  (replacing the agent-only check `:74`) and re-asserted on update. Dispatch per kind:
  `agent_group` → `agent_groups.project_id`; `workspace` → `workspaces.project_id`;
  `chatroom` → 2-hop `chatrooms.workspace_id` → `workspaces.project_id`
  (`contexts/conversation/infrastructure/tables.py:14-16,26-31`). Raise a 4xx domain error
  on mismatch. Implement all three kinds now even though Phase 1 only exercises `agent_group`.

### D7 — idempotent collection create + dim guard
- In `ensure_graphrag_collection`, replace the early-return (`:57`) with: if the collection
  exists, read `VectorParams.size` and raise on mismatch with the requested dimension;
  create only when absent, tolerating a concurrent-create error idempotently (catch
  "already exists" and re-read the dimension).

### D8 — bound build concurrency
- Give `graphrag_build` a bounded lane so a burst cannot starve other tasks: route it to a
  dedicated Arq queue consumed with a small `max_jobs`, or gate the task body with a module
  `asyncio.Semaphore`. Prefer the queue/`max_jobs` approach (observable, no shared in-process
  state). Non-goal: general worker-pool autoscaling.

### D9 — single builder construction seam
- Verify Phase 0 WS1's injected repo Port; consolidate the four reconciler construction
  points (`graphrag_reconciler.py:102,155,192,251`) to one private factory. If Phase 0
  already did this, the deliverable is a test asserting no inline construction remains.

### D10 — pause-and-carry mid-build deltas
- Define that deltas arriving while a build is `running` are carried to exactly one
  subsequent build: the next build's `since` watermark is the **started-at** of the build
  that consumed the prior window, not its finished-at, so no mid-build message is skipped or
  double-counted. Document and test the watermark boundary.

## 8. Regression Test Plan

Failing-test-first (`/build` writes each test before the fix). All under `backend/tests/unit/`
mirroring source, except the two marked integration.

- **AC-1 anchor (D2)** — unit: a config resolves its embedder from the **pinned** provider,
  not the first group key; flipping the first key's provider does not change the resolved
  model/dimension. Fails today (derives from first key at `graphrag.py:129`).
- **D2 create-guard** — unit: creating a second config in a project whose dimension is pinned
  to 1536, with a group yielding 768, raises 4xx. Fails today (no dimension check).
- **D2 self-pin** — unit: a legacy null-pin config persists `(provider, model, dim)` after
  first build.
- **D1** — unit: `iter_windows` over N messages yields ⌈N/window⌉ bounded windows and the
  builder still performs exactly one `apply_triples`/one snapshot/one `build_id` (assert via
  a fake driver counting calls). Fails today (single unbounded list).
- **D3** — unit: the lock is refreshed once per window boundary (assert refresh call count ==
  window count); config asserts `job_timeout > LOCK_TTL_S`.
- **D4** — unit: the trigger resolver returns each config once given a multi-member group
  with two matched members. Fails once the Phase 1 join is in place without DISTINCT.
- **D5** — unit: two enqueue calls for the same config+watermark produce one job id; a new
  watermark produces a new id.
- **D6** — unit: create/update with an owner in another project raises 4xx, for each of the
  three owner kinds.
- **D7** — integration (Qdrant): re-`ensure` an existing collection with a mismatched
  dimension raises; concurrent create does not error.
- **D8** — unit: `graphrag_build` concurrency is bounded (semaphore/queue config asserted).
- **D9** — unit: no inline builder construction remains in the reconciler (introspect/patch
  the factory).
- **D10** — unit: a delta inserted between a build's start and finish is included in the next
  build exactly once (watermark = started-at).

## 9. Risks and Rollback

- **Migration 0045** is expand-only and nullable → forward-compatible; old code ignores the
  new columns. Rollback = downgrade drops the columns; the pin logic degrades to today's
  derive-from-key behavior. Low risk.
- **D2 create-guard** could reject configs that "worked" before by luck of key ordering —
  intended; the 4xx message names the pinned dimension so the operator aligns the key group.
- **D1 windowing** risks changing extraction quality if the extractor's cross-message context
  now spans fewer messages per call. Mitigation: window by token budget large enough to
  preserve local context; triples still merge across windows before the single apply.
- **D5 nonce** risk: too-coarse a nonce suppresses a wanted rebuild; too-fine defeats dedup.
  Mitigation: nonce = rebuild watermark, tested both ways (AC).
- **D8** risk: too-small a lane throttles legitimate build throughput — make the cap
  configurable via settings.
- Rollback for D3–D10 is code-only (no schema); revert the commit.

## 10. Acceptance Criteria

- [ ] AC-1: the D2 anchor regression test (§8) fails before the fix and passes after.
- [ ] AC-2: `graphrag_configs` has `embed_provider`/`embed_model`/`embed_dim` (migration
      0045, expand-only nullable); ORM table mirrors them.
- [ ] AC-3: config create/update rejects (4xx) a builder key group whose resolved dimension
      differs from the project's pinned dimension; the resolved triple is persisted.
- [ ] AC-4: the build path selects the embedding key by the config's pinned provider (via
      `list_ordered_carried`) and fails loudly when none matches; legacy null-pin configs
      self-pin on first successful build.
- [ ] AC-5: a build over a large history processes in bounded windows (token budget + message
      cap) with exactly one `apply_triples`, one snapshot, one `build_id`, one Qdrant
      supersede, and `last_build_at` advanced once.
- [ ] AC-6: the build lock is refreshed on every window boundary and `job_timeout > LOCK_TTL_S`.
- [ ] AC-7: the trigger resolver returns each config at most once regardless of matched
      member count (DISTINCT).
- [ ] AC-8: concurrent triggers for the same config+watermark collapse to one enqueued job;
      a rebuild after completion is not suppressed.
- [ ] AC-9: create/update rejects an owner not in the config's project for every `owner_kind`
      (`agent_group`, `chatroom`, `workspace`).
- [ ] AC-10: `ensure_graphrag_collection` is idempotent under concurrent create and raises on
      dimension mismatch.
- [ ] AC-11: `graphrag_build` concurrency is bounded via a configurable lane.
- [ ] AC-12: the reconciler constructs its builder through a single seam; no inline
      construction remains.
- [ ] AC-13: a delta inserted mid-build is consumed by exactly one subsequent build
      (watermark = started-at).
- [ ] AC-14: `pytest -q`, `ruff check .`, `ruff format --check .`, and `mypy .` pass.

## 11. SRS Delta

None. Phase 2a restores behavior already specified by R11.16 (atomic, idempotent, single-
writer build), R11.18 (stable project-consistent vector space), and R11.19 (correct trigger
accounting). No new requirement is introduced.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1** — user-selectable embedding model per project (surface the pin as a product
  choice) is deferred to the Knowledge Map phase; Phase 2a freezes the derived pin.
- **FU-2** — data-repair automation: Phase 2a quarantines dimension-mismatched collections
  and surfaces them; an operator-driven safe re-embed/rebuild tool is out of scope.
- **FU-3** — physical Neo4j evidence-property rename remains deferred (Phase 0 WS3 decision);
  unaffected here.
