---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R10.04, R11.13]
---

# F-20: Editing chunk parameters leaves one config with mixed chunking semantics

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-20; verdict was
`plausible` because the SRS was silent on retroactivity — resolved by Q-1 below).

## 1. Summary

Chunk parameters (`chunk_size_tokens`/`chunk_overlap_tokens` for fixed, `similarity_threshold`
for semantic) live only on the config row and are consumed live at each ingest. Both the File
RAG and Knowledge Map config-update paths accept a `chunk_params` patch and write it verbatim
with no reprocessing and no rebuild. Because already-ingested chunks are never revisited and
carry no per-document provenance, editing chunk params after documents exist leaves the corpus
split: old chunks keep the prior policy, new uploads use the new policy, and the single config
setting no longer describes its own corpus. `chunk_strategy` and the embedding provider/model
are already immutable-after-create; chunk params are the inconsistent outlier. The fix makes
chunk params immutable once the config has any document — the API rejects a *changing*
`chunk_params` patch when documents exist, and the UI disables the fields — matching the
existing immutability precedent and eliminating mixed semantics without building a reprocess
pipeline.

## 2. Observed vs Expected

- **Observed** — chunk-param edits are silently prospective-only:
  - File RAG update whitelists `chunk_params` as mutable and writes it straight to the DB with
    only an audit event; no reprocess/rebuild is queued
    (`backend/contexts/knowledge/application/config_service.py:180-194`, `chunk_params` at `:183`;
    endpoint `backend/app/api/v1/rag.py:295-328`).
  - Knowledge Map update whitelists `("name", "chunk_params")` and does the same
    (`backend/contexts/knowledge/application/knowmap_config_service.py:129-151`).
  - The chunker reads the **live** config at each ingest
    (`backend/contexts/knowledge/application/ingest_service.py:294-299`;
    `backend/contexts/knowledge/application/knowmap_ingest_service.py:213-218`;
    `chunk_document` at `backend/contexts/knowledge/infrastructure/chunkers.py:239-272`).
  - No per-document/-chunk provenance exists: `chunk_params` is on the config row only
    (`backend/contexts/knowledge/infrastructure/tables.py:27`); `rag_documents`/`rag_chunks`
    store no chunk-param snapshot (`tables.py:51-110`; `backend/contexts/knowledge/domain/models.py:132-156`).
  - No capability re-chunks already-`READY` documents: the only "reprocess" is the
    failed/re-upload re-index path (`ingest_service.py:142-167`), and Knowledge Map "rebuild"
    re-extracts triples from **existing stored chunks**, never re-chunking
    (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:65,76-90`).
  - Both detail UIs expose the edit with no retroactivity warning; `chunk_strategy` is already
    a `disabled` (immutable) select (`frontend/src/slices/agents/views/RagConfigDetailView.vue:558-596`;
    `frontend/src/slices/agents/views/KnowledgeMapConfigDetailView.vue:561-601`).
- **Expected** — the chunking strategy and its parameters describe a config's whole corpus
  consistently ([R10.04] defines chunking per config; [R11.13] chunks the Knowledge Map corpus
  under the config). Per Q-1, chunk params are fixed once documents exist — like
  `chunk_strategy` and the embedding model already are — so a config can never hold a mix of
  chunking policies.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | The SRS is silent on chunk-param retroactivity. Which policy? | **Lock once documents exist.** Chunk params are editable only while the config has zero documents; once any document exists, a *changing* patch is rejected (409) and the UI disables the fields. | Smallest correct fix; eliminates mixed semantics entirely; consistent with `chunk_strategy` and embedding immutability already enforced. A versioned reprocess pipeline was rejected as far larger (new worker, re-embedding, provenance, cutover); a warn-only band-aid was rejected as leaving the corpus permanently mixed. |
| Q-2 | Should an *identical* `chunk_params` patch (no value change) be blocked when docs exist? | No — allow no-op patches. | The full-form detail-view PATCH always includes `chunk_params` (`RagConfigDetailView.vue:257-267` builds it via `assembleChunkParams` every save), so blocking unchanged values would break unrelated edits (name/top_k/rerank). Reject only when the value actually differs. |

## 4. Reproduction

Preconditions: a RAG (or Knowledge Map) config with at least one `READY` document.

1. Note the current `chunk_size_tokens` (e.g. 512).
2. `PATCH /api/rag-configs/{id}` with `chunk_params={"chunk_size_tokens": 256, ...}`.
3. Observe: the patch succeeds and the config now reports 256
   (`config_service.py:180-194`), but existing chunks/vectors were produced at 512 and are
   untouched (no reprocess).
4. Upload a new document: it is chunked at 256 (`ingest_service.py:294-299`), so the config
   now contains two chunking policies with no way to tell which chunk used which
   (`tables.py:94-110` — no provenance).

Deterministic.

## 5. Root Cause Analysis

Two facts combine: (1) chunk params are a **mutable** config field
(`config_service.py:183`, `knowmap_config_service.py:129-131`) with no re-chunk side effect,
and (2) chunking is a **live** read of the config at ingest time
(`ingest_service.py:294-299`) with no per-document snapshot (`tables.py:51-110`). The root
cause — the earliest link whose correction prevents the symptom — is (1): allowing chunk
params to change after documents exist is what creates the divergence. Fact (2) is inherent
to a single-config-row design and is acceptable *if* the params can no longer change once a
corpus exists. Correcting (1) (immutability-once-populated) removes the mixed-semantics
outcome without touching the ingest read path.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every File RAG and Knowledge Map config whose chunk params were edited
  after documents existed holds mixed-policy chunks; retrieval quality and evidence boundaries
  for the pre-edit documents silently differ from the displayed config.
- **Sibling suspects:**
  - **Knowledge Map path (confirmed, in scope).** Identical defect and identical fix shape;
    `knowmap_config_service.update` (`:129-151`) must gain the same guard. It already
    instantiates `KnowmapDocumentRepository` elsewhere in the service (`:183`).
  - **`chunk_strategy` (cleared — already immutable).** Not in either mutable whitelist and
    rendered `disabled` in both UIs (`RagConfigDetailView.vue:558-562`); this fix extends the
    same treatment to the params. Confirm the strategy is genuinely rejected (not silently
    dropped) on patch when docs exist, and align the two.
  - **Embedding model/provider (cleared — already immutable).** Guarded separately; the
    `immutableHint` label already covers embedding immutability
    (`RagConfigDetailView.vue:546`). Reuse/extend that hint pattern for chunk params.
  - **Concept Map (GraphRAG) configs (cleared — different surface).** GraphRAG configs are not
    document-backed and expose no chunk-param edit; out of scope.
  - **Semantic `max_tokens_per_chunk` (note).** The UI never exposes it; `assembleChunkParams`
    sends only `similarity_threshold` for semantic
    (`frontend/src/slices/agents/composables/useChunkParamsForm.ts:23-26`). The backend guard
    must compare the full effective `chunk_params` dict, not just UI-exposed fields, so a
    programmatic patch changing `max_tokens_per_chunk` is also blocked when docs exist.

## 7. Fix Design

**7.1 New domain error.** Add `ChunkParamsImmutable(KnowledgeError)` with code
`knowledge/chunk-params-immutable`
(`backend/contexts/knowledge/domain/errors.py`, add to `__all__`), and map it in
`backend/contexts/knowledge/interfaces/error_mapping.py` to **409 Conflict** (the edit is
forbidden by resource state; `RagConfigNameTaken`-style state conflicts already use 409 there
— note dimension conflicts use 422, but those are validation of a *value*, whereas this is a
*state* conflict, so 409 is the correct semantic).

**7.2 Document-count access.** `RagConfigService` does not currently wire a document repo
(`config_service.py:47-50`). Add `RagDocumentRepository(self._db)` (already imported at `:34-37`)
and a lightweight `count_for_config(config_id) -> int` on the repo (a `SELECT count(*)`; the
existing `list_for_config` at `repositories.py:303-319` is limit-capped and unsuitable for
counting). Knowledge Map already has `KnowmapDocumentRepository` available in-service
(`knowmap_config_service.py:183`); add the mirror count method there.

**7.3 File RAG update guard.** In `config_service.update` (`:157-213`), before writing: if
`"chunk_params"` is in the patch **and** the new value differs from `cfg.chunk_params` **and**
`count_for_config(config_id) > 0`, raise `ChunkParamsImmutable`. Compare full dicts (Q-2). An
identical-value patch is allowed (no-op) so the full-form save path keeps working. Keep
`chunk_params` in the mutable whitelist so it can still be set while docs == 0.

**7.4 Knowledge Map update guard.** Mirror 7.3 in `knowmap_config_service.update`
(`:117-166`) using the knowmap doc-count method.

**7.5 Frontend.** In both detail views, disable the chunk-param inputs (as `chunk_strategy`
already is) when the config has documents. The document count/list is already available via the
detail query (`RagConfigDetailView.vue` `docsQuery`); gate the `:disabled` on
`documents.length > 0`. Add an i18n hint (extend the `immutableHint` pattern,
`RagConfigDetailView.vue:546`, `KnowledgeMapConfigDetailView.vue:568`) explaining chunk params
are fixed once documents exist. All strings via `$t()`. The create form is unaffected (no docs
yet). Ensure the full-form PATCH still succeeds when nothing changed (the API allows the no-op
per Q-2), so disabling need only be a UX guard, not a payload change.

**7.6 Data repair.** None specified. Existing mixed-policy corpora are pre-existing; this fix
prevents *new* divergence. A one-off reprocess of already-mixed configs is out of scope and
recorded as FU-1.

## 8. Regression Test Plan

Backend (`backend/tests/unit/`):

1. **File RAG: changing chunk params with docs is rejected** (new/updated in
   `test_config_service` / `test_rag_*`): given a config with ≥1 document, a patch that changes
   `chunk_params` raises `ChunkParamsImmutable` (→ 409); the DB value is unchanged. Fails today
   — the patch currently succeeds (`config_service.py:180-194`).
2. **File RAG: changing chunk params with zero docs is allowed** (new): same patch on a
   document-less config succeeds.
3. **File RAG: identical `chunk_params` patch with docs is allowed** (new, Q-2): a patch echoing
   the current params (as the full-form save does) succeeds even with documents present, so
   unrelated field edits are not blocked.
4. **Knowledge Map: mirror of (1)-(3)** in the knowmap config-service tests.

Frontend (view/composable tests):

5. **Chunk-param inputs disabled when docs exist** (new): the detail view renders the
   chunk-param fields `disabled` (like `chunk_strategy`) when the documents query is non-empty,
   and enabled when empty; the immutability hint is shown.

Primary red-first test: (1).

## 9. Risks and Rollback

- **Full-form PATCH false-positives.** The detail view always sends `chunk_params`; the
  equality check (Q-2) must treat semantically-equal dicts as unchanged (key order, absent vs
  default `max_tokens_per_chunk`). Normalize before comparing, and cover with test (3).
- **Product limitation.** Designers can no longer re-tune chunking after upload without
  deleting all documents or recreating the config. This is the intended, precedent-consistent
  trade-off (accepted in Q-1); the UI hint must make it discoverable.
- **Rollback** — revert the guards, the error class/mapping, the repo count method, and the UI
  disabling. No schema migration; rollback is code-only.

## 10. Acceptance Criteria

- [ ] AC-1: The File RAG rejection test (§8.1) fails before the fix and passes after.
- [ ] AC-2: `PATCH` on a File RAG config with ≥1 document that *changes* `chunk_params` returns
  409 (`knowledge/chunk-params-immutable`) and leaves the stored params unchanged.
- [ ] AC-3: `PATCH` that changes `chunk_params` on a document-less config succeeds; an
  identical (no-op) `chunk_params` patch succeeds regardless of document count.
- [ ] AC-4: The same rejection/allow semantics hold for Knowledge Map config updates.
- [ ] AC-5: Both detail UIs disable the chunk-param inputs when the config has documents and
  show an i18n immutability hint; the create form remains fully editable.
- [ ] AC-6: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` pass in `frontend/`.

## 11. SRS Delta

[R10.04] is silent on retroactivity, which is why the finding was `plausible`. Q-1 resolves it,
so amend [R10.04] to state the immutability rule explicitly. Apply verbatim on approval:

> - **[R10.04]** Chunking strategy (Q37): user picks per RAG config:
>   - **Fixed-size**: `chunk_size_tokens` (default 512), `chunk_overlap_tokens` (default 64).
>   - **Semantic**: sentence-aware splitter (`semantic-text-splitter`) with target
>     `max_tokens_per_chunk` (default 512) and `similarity_threshold` (default 0.6).
>   - The chunking strategy and its parameters are fixed once the config has any document:
>     they describe the whole corpus, so they may be changed only while the config is empty.
>     After the first document, edits are rejected (no retroactive re-chunking).

(The existing default `similarity_threshold` wording in the SRS reads 0.6; the code default is
0.3 at `models.py:107-110`. This discrepancy predates F-20 — recorded as FU-2, not changed
here.)

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (data, out of scope):** existing configs edited before this fix may already hold
  mixed-policy chunks; a one-off reprocess/re-chunk is not built here. If a versioned reprocess
  workflow is ever desired (the rejected Q-1 option), it would supersede this immutability rule.
- **FU-2 (SRS accuracy):** [R10.04]'s stated default `similarity_threshold` (0.6) disagrees
  with the code default (0.3, `models.py:107-110`). Reconcile in a separate SRS-accuracy pass.
- **FU-3 (`chunk_strategy` parity):** confirm `chunk_strategy` is explicitly rejected (not
  silently dropped) on patch when docs exist; if only silently dropped today, align it with the
  new explicit `ChunkParamsImmutable` behavior.
