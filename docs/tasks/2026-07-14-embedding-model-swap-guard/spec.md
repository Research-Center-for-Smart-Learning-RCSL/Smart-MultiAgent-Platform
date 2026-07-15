---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.19]
---

# F-13: Builder-key swaps repin queries without rebuilding old vector spaces

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-13).

## 1. Summary

A Concept Map or Knowledge Map config stores a full `(provider, model, dim)` embedding pin, but
the guard that gates a builder Key Group change compares **only the dimension**
(`backend/contexts/knowledge/application/graphrag_config_service.py:188-193`;
`backend/contexts/knowledge/application/knowmap_config_service.py:140-145`). So a designer can
switch the builder to a *different embedding provider/model that happens to share the dimension*,
the update persists the new pin (`graphrag_config_service.py:365-371`;
`knowmap_config_service.py:146-166`) and enqueues **no** rebuild, and the next query is embedded
with the new model — resolved from the freshly persisted pin
(`backend/contexts/knowledge/application/embed_resolution.py:84-96`) — against vectors produced
by the old model. Same dimension, different vector space: cosine similarity is meaningless, so
retrieval silently collapses. Point payloads carry no model/version tag
(`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:129-141`), so the mixed
space is undetectable at query time. For Concept Maps the space becomes *mixed* because delta
builds re-embed only touched entities (`graphrag_builder.py:528-562`, DOM-8 comment `:415-436`);
for Knowledge Maps all old-model vectors persist until an unrelated corpus change triggers a
build (and even then are retained additively — F-6).

**Current status — latent, with a related observable defect today.** The whitelist ships four
models with four *distinct* dimensions (1536 / 3072 / 768 / 1024,
`backend/contexts/knowledge/domain/models.py:36-41`), so a *same-dimension* model swap — the
silent-recall-collapse case — cannot occur today; it becomes a live, silent, major bug the
instant any dimension-colliding model is whitelisted (a near-certainty as providers are added —
many ship 1024- or 1536-dim models). What *is* reproducible now is the adjacent half of the same
defective guard: on a single-config project the pin lookup excludes the config itself
(`_project_pinned_dim(... exclude_config_id=config_id)`), so changing a *built* config's builder
group to a different-dimension model is **accepted at update** and only fails later at the D7
build guard with an opaque dimension mismatch — a confusing post-hoc failure instead of a clean
rejection. The fix — reject a provider/model change while the config holds indexed vectors,
consistent with [R11.19]'s "rejected" disposition — delivers a clean early error for that case
today and closes the latent silent-corruption hole before the whitelist grows. The designer
clears/recreates to change model.

## 2. Observed vs Expected

- **Observed** —
  - *Dimension-only guard.* Concept Map update detects a group swap
    (`graphrag_config_service.py:334`), resolves the new group's `(provider, model, dim)`, and
    compares only `dim` (`_enforce_and_resolve_pin` `:188-193`; `_project_pinned_dim` reads only
    `embed_dim`, `:145-168`). Knowledge Map is identical (`knowmap_config_service.py:132-145`,
    `project_pinned_dim` reads only `embed_dim`).
  - *New pin persisted, no rebuild.* Concept Map writes the new provider+model+dim via
    `set_embed_pin` and returns with no builder/trigger/enqueue call
    (`graphrag_config_service.py:359-393`, pin at `:365-371`). Knowledge Map writes the new pin
    columns and returns, no enqueue (`knowmap_config_service.py:151-166`).
  - *Query uses the new pin against old vectors.* Retrieval embeds the query via
    `embedder_factory(cfg)` on the freshly loaded config
    (`backend/contexts/knowledge/application/graphrag_retrieve.py:98-107`), which resolves the
    embedder from `cfg.embed_provider`/`cfg.embed_model`
    (`backend/contexts/knowledge/application/embed_resolution.py:84-96`) — the post-swap model.
    Retrieval does not filter by `build_id` (`graphrag_retrieve.py:108-122`), so it searches the
    whole mixed set.
  - *No model tag on stored vectors.* Point payload is `{config_id, entity, description,
    build_id}` only (`graphrag_vector_store.py:129-141`) — nothing records the producing model.
  - *Mixed space for Concept Maps.* Delta builds embed only the delta's entities
    (`graphrag_builder.py:540,528-562`; the DOM-8 comment states earlier builds' points "MUST be
    kept", `:415-436`).
- **Expected** — [R11.19]: configs sharing a project's graph vector collection use "a single
  embedding model/dimension; a config whose builder Key Group would select a different embedding
  dimension is rejected." A same-dimension model change produces a semantically incompatible
  vector space and defeats the "single embedding model" half of the invariant just as surely as a
  dimension change; it must not be silently accepted. Either the change is rejected, or the entire
  vector space is rebuilt under the new model before any query uses it.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Reject the swap while indexed data exists, or accept it and re-embed with atomic cutover? | **Reject** a provider/model change while the config holds indexed vectors; the designer clears/recreates to change model. | Rejection is simple, safe, and consistent with [R11.19]'s explicit "rejected" disposition for embedding conflicts. The re-embed-with-cutover alternative is materially more complex — it requires F-6's replacement semantics (to drop old-model Knowledge Map vectors) plus a Concept-Map *full* re-embed mode (delta builds only touch changed entities), and an atomic read cutover — none of which exist today. Re-embed remains a future enhancement (FU-1). |
| Q-2 | Compare the group ID or the resolved embedding provider/model? | Compare the **resolved embedding `(provider, model)`**, not the group ID. | A builder Key Group also selects the extraction LLM; a group change that resolves to the *same* embedding provider/model does not change the vector space and must stay allowed. Only a change in the resolved embedding provider or model invalidates existing vectors. |
| Q-3 | What counts as "holds indexed vectors"? | The config has entered at least one build (`last_build_at IS NOT NULL`, i.e. any prior build attempt), evaluated fail-closed. | Old-model vectors can exist as soon as a build's Qdrant phase has run; a never-built config has no vectors and may freely change model. Fail-closed (reject if a build was ever attempted) avoids reasoning about partial/rolled-back builds. `/build` confirms the exact terminal-vs-attempted `BuildState` set against `backend/contexts/knowledge/domain/graphrag.py`. |

## 4. Reproduction

**Reproducible today (single-config, different-dimension — the observable half):**
1. In a project whose only Concept Map (or Knowledge Map) config C uses model A
   (`openai:text-embedding-3-small`, 1536-dim), build C so the collection is sized 1536.
2. Update C's builder Key Group to one resolving to model B of a *different* dimension (e.g.
   `openai:text-embedding-3-large`, 3072-dim). `_project_pinned_dim(... exclude_config_id=C)`
   excludes C and finds no sibling, so `existing_dim` is `None` and the dimension check passes
   (`graphrag_config_service.py:188-193`); the update succeeds and repins to B.
3. Trigger a build. It fails at the D7 guard `_assert_dimension`
   (`graphrag_vector_store.py:92-98`) with an opaque 3072-vs-1536 mismatch — a confusing
   post-hoc failure. The fix instead rejects at step 2 with a clear message.

**Latent (same-dimension — the silent-corruption half):** identical to the above but with a
model B of the *same* dimension as A. The shipped whitelist has no two models sharing a dimension
(`domain/models.py:36-41`), so this cannot be reproduced against production config; a unit test
reproduces it by monkeypatching `EMBED_MODEL_DIMENSIONS` with two synthetic same-dimension models
(§8). With same dimensions, step 2 succeeds *and* step 3's build passes the D7 guard, so queries
silently embed with B against A's vectors and recall collapses with no error.

## 5. Root Cause Analysis

The causal chain:

1. The update-time conflict guard compares only `embed_dim` and ignores the persisted
   `provider`/`model` half of the pin
   (`graphrag_config_service.py:188-193`; `knowmap_config_service.py:140-145`;
   `_project_pinned_dim`/`project_pinned_dim` select only `embed_dim`). **This is the root
   cause** — it treats "same dimension" as "same vector space", which is false across models.
2. The update persists the new pin without enqueuing a rebuild
   (`graphrag_config_service.py:359-393`; `knowmap_config_service.py:151-166`), so old-model
   vectors remain while the config now advertises the new model.
3. Retrieval embeds with the current pin and searches the whole collection unfiltered
   (`graphrag_retrieve.py:98-122`; `embed_resolution.py:84-96`), so once a same-dimension swap is
   possible every query would use the incompatible model — the (latent) silent symptom.
4. Absence of any model/version tag on point payloads (`graphrag_vector_store.py:129-141`) means
   nothing can distinguish or filter the stale vectors — an aggravating factor that makes a
   partial/re-embed remedy harder (hence the reject decision, Q-1).

Correcting (1) — reject a resolved provider/model change while indexed vectors exist — prevents
the incompatible state from ever being persisted.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — *once a dimension-colliding model is whitelisted*, immediate silent recall
  collapse for any Concept Map or Knowledge Map whose builder embedding model is swapped after it
  has been built, with Concept Maps accruing a permanently mixed vector space across deltas. *With
  the current whitelist* (no dimension collisions), the observable impact is narrower: a
  single-config project can repin a built config to a different-dimension model and only discover
  the breakage at the next build. Both stem from the same dimension-only guard.
- **Sibling suspects:**
  - **Concept Map and Knowledge Map update paths — confirmed, both in scope.** They share the
    dimension-only guard and the no-rebuild tail; both get the resolved-provider/model check.
  - **File RAG update — cleared.** `RagConfigService.update`'s mutable set excludes
    `embed_provider`/`embed_model` (`backend/contexts/knowledge/application/config_service.py:180-188`),
    so a File RAG config's embedding model cannot change post-creation — no equivalent swap exists.
  - **Create-time pin conflict (F-11) — related, separate.** F-11 hardens the *create/delete* pin
    lifecycle (dimension durability); this fix hardens the *update* path against a same-dimension
    *model* change. Complementary; the two dossiers touch adjacent code in the same services and
    should be built consistently (share the resolved-`(provider, model, dim)` comparison helper if
    convenient).
  - **F-14 (builder vs consumer group collision) — related, separate.** F-14 concerns the same
    Knowledge Map update path validating the new builder group against attached Agents; it is an
    isolation defect, not an embedding-space defect. Both add validation to
    `knowmap_config_service.update`; coordinate so the checks compose (a required security review
    per the audit's FU-1).
  - **Same-model group change — must stay allowed (Q-2).** A builder group change whose resolved
    embedding provider/model is unchanged does not invalidate vectors and must not be rejected;
    the guard keys on resolved `(provider, model)`, not group identity.

## 7. Fix Design

1. **Compare resolved `(provider, model)`, not just dimension.** In the Concept Map swap branch
   (`graphrag_config_service.py:334-371`), after `new_pin = _enforce_and_resolve_pin(...)`
   (`:355-357`) and only when `new_pin is not None` (the graphrag pin is nullable — a group with
   no embedding key resolves to `None`, `:184-186`, and has no vector space to invalidate),
   compare `new_pin[0]`/`new_pin[1]` against the config's persisted pin (`cfg.embed_provider`,
   `cfg.embed_model`). In the Knowledge Map swap branch (`knowmap_config_service.py:132-149`),
   after `provider, model, dim = pin` (`:139`; knowmap always resolves a pin, `:135-138`),
   compare `provider`/`model` against `cfg.embed_provider`/`cfg.embed_model` — in both cases in
   addition to the existing `dim` comparison, which still runs first so a dimension conflict keeps
   precedence.
2. **Reject a model change on a built config.** If the resolved `provider` or `model` differs from
   the persisted pin **and** the config holds indexed vectors (Q-3: `last_build_at IS NOT NULL`,
   fail-closed), raise a new typed error — `GraphRagEmbeddingModelChangeBlocked` /
   `KnowmapEmbeddingModelChangeBlocked` — mapped to HTTP 409 at the interface layer with a message
   directing the designer to clear/recreate to change the embedding model. Keep the existing
   dimension-conflict error for `dim` mismatches (unchanged).
3. **Allow the change on a never-built config.** If no indexed vectors exist
   (`last_build_at IS NULL`), permit the provider/model change and persist the new pin as today —
   there is no vector space to invalidate.
4. **No rebuild is enqueued** (that is the rejected re-embed alternative, FU-1). The reject path
   leaves existing vectors and the existing pin untouched.
5. **Interface mapping.** Add the new errors to the knowledge context's error mappers so the API
   returns 409 with a stable error code, and add the frontend i18n string for the message (the
   Agent/Map detail views surface config-update errors).

**Data repair:** configs already corrupted by a pre-fix swap hold a mixed/incompatible vector
space. This fix does not auto-repair them; a one-time operator action (delete + recreate, or a
future re-embed action per FU-1) is required. The migration is code-only (new error types, no
schema change); document the manual remediation in the deploy notes.

## 8. Regression Test Plan

Unit tests (fake group→embedding resolver; in-memory config store):

1. **Reject same-dimension model swap on built config (primary red-first, latent case):**
   monkeypatch `EMBED_MODEL_DIMENSIONS` (`domain/models.py:36-41`) to add two synthetic models A
   and B at the *same* dimension (the shipped whitelist has no such pair). A Concept Map config
   with `last_build_at` set and pin model A; update to a group resolving to B raises
   `GraphRagEmbeddingModelChangeBlocked` and persists nothing. Fails today — the update succeeds
   and repins to B (the silent-corruption path).
2. **Reject different-dimension model swap on a single built config (observable-today case):** a
   built single-config project on model A; update to a group resolving to a different-dimension
   model raises `GraphRagEmbeddingModelChangeBlocked` at update time. Fails today — the update is
   accepted (pin excludes self) and the mismatch only surfaces at the next build's D7 guard.
3. **Knowledge Map parity:** same assertion as test 1 against `knowmap_config_service.update` with
   `KnowmapEmbeddingModelChangeBlocked`.
4. **Never-built config allows the change:** with `last_build_at IS NULL`, the same model swap
   succeeds and persists the new pin.
5. **Same-model group change allowed:** a group change resolving to the *same* provider/model
   (different extraction LLM) succeeds even on a built config — the guard keys on resolved
   `(provider, model)`, not group identity.
6. **Dimension conflict unchanged:** a group resolving to a different dimension still raises the
   existing dimension-conflict error on a *multi-config* project (a live sibling pins it), not the
   new model-change error.

## 9. Risks and Rollback

- **Over-restriction** — the fail-closed `last_build_at IS NOT NULL` rule rejects a model swap even
  on a config whose only build failed before the Qdrant phase (no vectors written). This is safe
  (the designer can delete/recreate) and preferable to reasoning about partial builds; if it
  proves too strict operationally, tighten to a terminal-success `BuildState` check later.
- **Blocking legitimate group changes** — mitigated by Q-2: only a resolved embedding
  provider/model change is blocked; changing the group for extraction-LLM reasons while keeping the
  same embedding is unaffected (test 5).
- **Interaction with F-14** — both add validation to `knowmap_config_service.update`; ensure the
  order and error precedence are defined (e.g. builder/consumer isolation vs embedding-model
  check) so one does not mask the other.
- **Rollback** — revert the guard change and the new error types; no schema change, no data
  migration. Configs updated under the new rule are unaffected by rollback.

## 10. Acceptance Criteria

- [x] AC-1: The reject-model-swap regression test (§8.1) fails before the fix and passes after,
  for Concept Maps. (`tests/unit/test_embedding_model_swap_guard.py::test_graphrag_rejects_same_dimension_model_swap_on_built_config`.)
- [x] AC-2: A builder Key Group change that resolves to a different embedding provider or model is
  rejected with a typed 409 error when the config holds indexed vectors — for both the latent
  same-dimension case (§8.1, §8.3) and the observable-today different-dimension single-config case
  (§8.2), across Concept Maps and Knowledge Maps. (`test_graphrag_rejects_same_dimension_model_swap_on_built_config`,
  `test_graphrag_rejects_different_dimension_model_swap_on_single_built_config`,
  `test_knowmap_rejects_model_swap_on_built_config`; mapped 409 in `error_mapping.py`.)
- [x] AC-3: The same change is allowed and persists the new pin when the config has never built
  (§8.4). (`test_graphrag_allows_model_change_on_never_built_config`,
  `test_knowmap_allows_model_change_on_never_built_config` — both assert the config pin is written
  and the F-11 durable pin is refreshed, D-2.)
- [x] AC-4: A group change resolving to the same embedding provider/model succeeds on a built
  config (§8.5), and a dimension change still raises the existing dimension-conflict error on a
  multi-config project (§8.6). (`test_graphrag_allows_same_model_group_change_on_built_config`,
  `test_graphrag_dimension_conflict_keeps_precedence`, and the knowmap parity pair.)
- [x] AC-5: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`;
  `pnpm lint` / `pnpm typecheck` pass in `frontend/` for the added i18n string. Backend: unit
  suite green, `ruff check` clean, **zero net new** `mypy` errors (F-13 files clean; the residual
  baseline is FU-4 from F-11). Frontend: `pnpm lint`, `pnpm typecheck`, `pnpm test` (660), and
  `pnpm build` all pass. `pnpm run gen:api` N/A — no API contract change (problem+json errors are
  not typed OpenAPI models).

## 11. SRS Delta

None. This restores [R11.19]'s "single embedding model" invariant by treating a same-dimension
model change as a rejected embedding conflict, consistent with the requirement's existing
"rejected" disposition.

## 12. Deviation Log

- **D-1 (test seam):** §8.1 proposes monkeypatching `EMBED_MODEL_DIMENSIONS` with two synthetic
  same-dimension models. The tests instead mock the pin *resolution* seam
  (`_enforce_and_resolve_pin` / `_resolve_group_pin`) to return the resolved `(provider, model,
  dim)` directly — the same-dimension scenario is a resolver returning a same-dim different-model
  pin. This isolates the new model-change decision (what AC-1/AC-2 assert); the resolver's own
  dimension logic is already covered by `test_graphrag_embed_pin.py`. Equivalent AC coverage,
  fewer moving parts.
- **D-2 (refresh the F-11 durable pin on an allowed change):** F-13 was specced before F-11's
  `project_embedding_pins` table landed in the same batch. On an *allowed* (never-built) model
  change the guard now also calls `EmbeddingPinRepository.upsert` so the durable pin stays
  consistent with the config's newly persisted embedding. Without it, a later sibling create would
  hit a stale-pin false dimension-conflict. The update-path dimension guard guarantees no live
  sibling pins a different dimension, so the overwrite is safe. This realizes §6's "F-11 and F-13
  ... should be built consistently; share the resolved-(provider, model, dim) comparison."
- **D-3 (frontend surface scoped to Knowledge Map):** §7.5 asks for the i18n string on "the
  Agent/Map detail views." Only the Knowledge Map detail view edits `builder_key_group_id` in the
  current UI; the Concept Map builder group is set at create and otherwise only its recency is
  patched (`ConceptMapPanel`), so the graphrag 409 has no frontend edit surface to wire today. The
  i18n string + the specific-error toast were added to `KnowledgeMapConfigDetailView`
  (`agents.knowmapDetail.embedModelChangeBlocked`, en + zh-TW); the backend still returns the
  typed 409 for API/Concept-Map clients. If a Concept Map builder-group edit UI is later added, it
  should reuse the same pattern (noted in FU-4).

## 13. Follow-ups

- **FU-1 (re-embed with cutover):** the rejected alternative — accept a model change and re-embed
  the whole corpus/all entities under a new embedding version with atomic read cutover — is a
  genuine future enhancement. It depends on F-6 replacement semantics (drop old Knowledge Map
  vectors) and a Concept-Map full re-embed mode, and would benefit from a model/version tag on
  point payloads (`graphrag_vector_store.py:129-141`).
- **FU-2 (data remediation tooling):** provide an operator path to clear a config's vectors and
  rebuild (short of delete/recreate) so a designer can change embedding models without losing the
  config's identity and Agent attachments.
- **FU-3 (coordinate with F-14):** both fixes add validation to the Knowledge Map update path and
  one (F-14) is a required security review; sequence them so the checks compose.
- **FU-4 (Concept Map builder-group edit UI):** the graphrag 409 (D-3) has no frontend edit
  surface today because the Concept Map builder group is not editable post-create. If such a UI is
  added, wire its save error handler to the same `/graphrag-embedding-model-change-blocked` problem
  type and add an `agents.conceptMaps.*` i18n string mirroring
  `agents.knowmapDetail.embedModelChangeBlocked`.
