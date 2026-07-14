---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R10.08]
---

# F-19: The required local BGE reranker has no reachable product path

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-19).

## 1. Summary

[R10.08] promises two reranking options per RAG config — `cohere:rerank-3` (BYO Cohere
key) **or** a local `bge-reranker-v2-m3` that "uses the app's bundled reranker service (CPU
inference acceptable at 100 users)". The Cohere path is fully wired; the local path is
closed at every layer: the API and frontend schemas allow only `"cohere"`, the config
service requires a key + provider for every enabled rerank (no keyless branch), the runtime
factory constructs only `RouterReranker`, and no bundled service, URL setting, or UI
affordance exists. The `LocalBgeReranker` adapter is present but has zero callers. A
designer who wants the promised local reranker cannot select it; a hand-crafted keyless
config silently runs vector-only. The fix restores the documented capability end-to-end:
a bundled reranker service in the Compose stack, a `bge_reranker_url` setting, a `"bge"`
provider value, a keyless validation branch, a runtime factory branch, a UI provider
selector, health handling, and an E2E test.

## 2. Observed vs Expected

- **Observed** — reranking can only ever be Cohere:
  - API create/patch constrain the provider to `Literal["cohere"] | None`
    (`backend/app/api/v1/rag.py:76`, `:87`); the frontend Zod schema mirrors it with
    `z.enum(['cohere'])` (`frontend/src/slices/agents/types/schemas.ts:76`).
  - The UI has no provider selector at all — `rerank_provider` is force-set to `'cohere'`
    on enable and `null` on disable (`frontend/src/slices/agents/composables/useRagConfigForm.ts:48-59`,
    set at `:50`); create/edit views render only enable/key/model
    (`frontend/src/slices/agents/views/RagConfigListView.vue:542-570`;
    `frontend/src/slices/agents/views/RagConfigDetailView.vue:615-643`).
  - The config service requires a key **and** provider for every enabled rerank, with no
    keyless branch (create `backend/contexts/knowledge/application/config_service.py:114-120`;
    update `:169-177`; key validation `_validate_rerank_key` `:67-76`).
  - The runtime factory constructs only `RouterReranker` and only when a key exists;
    keyless configs fall through to vector-only
    (`backend/contexts/knowledge/application/rag_context_provider.py:97-111`, guard at `:98`).
  - `LocalBgeReranker` exists as an unreferenced adapter — `base_url` constructor, no key,
    POSTs `{query, candidates, top_k}` to `{base}/rerank` expecting `results[].{index,score}`
    (`backend/contexts/knowledge/infrastructure/rerankers.py:84-120`) — with zero production
    callers (repo-wide grep: definition + `__all__` + docstring only).
  - No bundled service (`deploy/compose/docker-compose.yml` has no reranker/bge service) and
    no URL setting (`backend/app/config/settings.py` has none).
  - The `COHERE` provider is the only one declaring the `RERANK` capability
    (`backend/contexts/keys/domain/providers.py:52`;
    frontend mirror `frontend/src/slices/keys/api/keys.ts:39`) — local BGE is keyless and is
    *not* a key provider, so it must bypass the capability map, not extend it.
- **Expected** — [R10.08]: a designer can choose `bge-reranker-v2-m3` per RAG config; when
  chosen, it uses the app's bundled reranker service and requires no user key. The local
  option must be selectable in the UI, validated without a key, constructed at runtime as
  `LocalBgeReranker`, and backed by a deployed service reachable via configuration.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far should the deployment side go? | **Full bundle**: add a bundled reranker service to the Compose stack, a `bge_reranker_url` setting, and the full code path (provider enum, keyless validation, runtime factory, UI selector, health handling, E2E). | [R10.08] literally says "the app's **bundled** reranker service"; SMAP is production-target / no-MVP. Wiring the code without shipping the service (operator-run) would leave the promised option inert out of the box; descoping would abandon a documented capability. |
| Q-2 | What identifies the local provider? | Provider value `"bge"`, default model `bge-reranker-v2-m3`, keyless. | Matches the SRS model name; `"bge"` is a stable non-key provider token distinct from the key-provider `"cohere"`. |

## 4. Reproduction

Preconditions: a project with a RAG config; the SRS-promised local reranker desired.

1. Open the RAG config create form. The only rerank control is an enable toggle plus a
   Cohere key/model; there is no provider choice (`RagConfigListView.vue:542-570`).
2. Attempt via API: `POST /api/rag-configs` with `rerank_enabled=true, rerank_provider="bge"`.
   Pydantic rejects `"bge"` (`rag.py:76`, `Literal["cohere"]`).
3. Attempt a keyless config `rerank_enabled=true, rerank_provider=null`: the service raises
   `CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")`
   (`config_service.py:114-120`).
4. Force a config into the DB with `rerank_enabled=true` and no key: at runtime the factory
   guard `cfg.rerank_key_id is not None` is false, so no reranker is built and retrieval runs
   vector-only (`rag_context_provider.py:98,137`).

Deterministic; no timing involved.

## 5. Root Cause Analysis

The local path was never wired — the `LocalBgeReranker` adapter was written but no layer
routes to it. There are six independent gaps, each of which alone makes the option
unreachable:

1. **Provider enum excludes `"bge"`** — API (`rag.py:76,87`) and FE schema
   (`schemas.ts:76`). Root cause of "cannot be represented".
2. **No UI affordance** — provider is hardcoded (`useRagConfigForm.ts:50`); no selector in
   either view.
3. **No keyless validation branch** — `config_service.py:114-120,169-177` unconditionally
   demand a key + capability-bearing key row.
4. **Runtime factory has no `bge` branch** and gates on a key existing
   (`rag_context_provider.py:97-111`).
5. **No deployed service** — `deploy/compose/docker-compose.yml`.
6. **No URL setting** — `backend/app/config/settings.py`; `LocalBgeReranker.base_url` has no
   configuration source.

The earliest-correcting link is the provider enum (gap 1) — it is what makes the option
*representable* — but because the gaps are independent, the fix must close all six; patching
any subset leaves the feature unreachable. This is a feature-completeness restoration of a
documented requirement, not a single-line defect.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — the entire local-reranking option is unavailable; every designer is
  silently constrained to Cohere-with-BYO-key or vector-only.
- **Sibling suspects:**
  - **Embedding subsystem (cleared, no local precedent).** All embed providers are remote
    BYO-key (`schemas.ts:72`, `rag.py:72`, `embedders.py` only `RouterEmbedder`); there is
    no local embedder to mirror. The only in-repo "local, keyless adapter" precedent is
    `LocalBgeReranker` itself. The fix must therefore establish the keyless-provider pattern
    rather than copy an existing one; it must not accidentally generalize to embeddings.
  - **Capability map (cleared, must NOT change).** BGE is keyless and not a key provider;
    do not add a `bge` entry to `ApiKeyProvider`/`CAPABILITIES`
    (`providers.py:52`, `keys.ts:39`). The rerank-key filter in the UI stays Cohere-scoped;
    the local branch simply has no key select.
  - **GraphRAG / Knowledge Map reranking (cleared, out of scope).** Only File RAG exposes a
    reranker (`rag_context_provider.py`); graph retrieval has no rerank surface, so this fix
    is File-RAG-scoped.
  - **Adapter/service contract (confirmed, in scope).** `LocalBgeReranker` POSTs
    `{query, candidates, top_k}` and reads `results[].{index, score}`
    (`rerankers.py:95-116`). The deployed service MUST honor exactly this contract, or the
    adapter must be adjusted to the service's contract — see §7.

## 7. Fix Design

**7.1 Provider value and model.** Add `"bge"` to the rerank provider enum in the API
(`rag.py:76,87` → `Literal["cohere", "bge"]`) and the FE schema
(`schemas.ts:76` → `z.enum(['cohere', 'bge'])`). Default model `bge-reranker-v2-m3` when
provider is `bge`. The domain model already types `rerank_provider` as `str | None`
(`backend/contexts/knowledge/domain/models.py:125,169`), so no domain change is needed.

**7.2 Keyless validation branch.** In `config_service` create (`:114-120`) and update
(`:169-177`), branch on provider:
- `provider == "cohere"` → existing key + capability validation (`_validate_rerank_key`).
- `provider == "bge"` → **require no key**; instead require that the bundled service is
  configured (settings URL non-empty). Reject `rerank_key_id` supplied with `bge` (a keyless
  provider must not carry a key). Keep the "enabled requires a provider" invariant.

**7.3 Settings.** Add a knowledge/reranker settings section mirroring `EgressSection`
(`settings.py:221-241`) / `SandboxSection` (`:244-272`): `bge_reranker_url: str` read from an
un-prefixed alias (e.g. `RERANK_BGE_URL`) plus a section-prefixed fallback, default the
in-cluster service DNS (e.g. `http://bge-reranker:80`). An empty value means "local reranker
not deployed" and MUST make `provider=bge` validation fail with a clear, doc-pointing error
(mirroring the `supervisor_url` empty-disables gate at `:264-272`).

**7.4 Runtime factory.** In `rag_context_provider.py:97-111`, branch on `cfg.rerank_provider`:
- `cohere` (and a key present) → `RouterReranker` (unchanged).
- `bge` → `LocalBgeReranker(base_url=<settings.bge_reranker_url>)`, with no key required.
  The current `cfg.rerank_key_id is not None` guard (`:98`) must be widened so a keyless
  `bge` config still builds a reranker instead of falling through to vector-only.

**7.5 Bundled service (deploy).** Add a reranker service to
`deploy/compose/docker-compose.yml` serving `bge-reranker-v2-m3` on CPU. The service MUST
expose the contract `LocalBgeReranker` calls: `POST /rerank` with body
`{query, candidates, top_k}` returning `{results: [{index, score}, ...]}`
(`rerankers.py:104-116`). If an off-the-shelf image is used (e.g. HuggingFace
text-embeddings-inference, whose rerank endpoint takes `{query, texts}` and returns
`[{index, score}]`), reconcile the two contracts by either (a) adapting `LocalBgeReranker`
to the deployed service's request/response shape, or (b) placing a thin translating shim in
front. Record the chosen contract explicitly. Add the service to `.env.example` and the
deploy docs; keep it off the public network (internal service only).

**7.6 Health handling.** `LocalBgeReranker` has only `raise_for_status()` + `close()`
(`rerankers.py:108,118-120`). Add a readiness/health path: at minimum surface a clear error
when the service is unreachable at rerank time; optionally add a startup/health probe
mirroring the supervisor readiness gate. A rerank-service failure must degrade to vector-only
retrieval (not a turn failure) with a logged warning, consistent with the existing
router-reranker fallback (`rag_context_provider.py:108-110`).

**7.7 UI provider selector.** Replace the hardcoded provider set:
- `useRagConfigForm.ts:48-59` — expose a `rerankProvider` selection (`cohere` | `bge`)
  instead of force-setting `cohere`; when `bge`, clear/omit `rerank_key_id` and default the
  model to `bge-reranker-v2-m3`.
- Create (`RagConfigListView.vue:542-570`) and edit (`RagConfigDetailView.vue:615-643`)
  views — add a provider `SSelect` (reuse the existing `SSelect` component used for the key
  select). Show the key select only when `provider === 'cohere'`; hide it for `bge`. Adjust
  the `rerankIncomplete`/submit gating (`RagConfigListView.vue:191`) so `bge` does not
  require a key. All new labels go through `$t()` (no hardcoded strings).

No data repair is required — existing configs are all Cohere or disabled; adding `bge` is
purely additive.

## 8. Regression Test Plan

Backend (`backend/tests/unit/`):

1. **API accepts `bge`** (update `test_rag_*` API/schema test): `POST`/`PATCH` with
   `rerank_provider="bge"` is accepted; a bad provider is still 422. Fails today —
   `Literal["cohere"]` rejects `"bge"`.
2. **Keyless `bge` validates without a key** (`test_config_service` or new): a config with
   `rerank_enabled=true, rerank_provider="bge", rerank_key_id=None` and a configured service
   URL is accepted; the same with an empty service URL is rejected with the doc-pointing
   error; supplying a key with `bge` is rejected. Fails today — `config_service.py:114-120`
   demands a key.
3. **Runtime builds `LocalBgeReranker` for `bge`** (`test_rag_context_provider` or new):
   a `bge` config constructs `LocalBgeReranker(base_url=...)`, not `RouterReranker`, and does
   not fall through to vector-only. Fails today — factory only builds `RouterReranker`.
4. **Service-down degrades to vector-only** (new): when `LocalBgeReranker.rerank` raises,
   retrieval returns vector-only results with a warning, not a turn failure.

Frontend (`frontend/src/slices/agents/**/__tests__` or view tests):

5. **Provider selector renders and gates** (new): selecting `bge` hides the key select and
   allows submit without a key; selecting `cohere` requires a key. Fails today — no selector
   exists.

E2E (deploy): a smoke test that a `bge`-provider config performs a rerank against the
bundled service and returns reordered results.

The primary red-first test is (1) (API schema) or (3) (runtime factory).

## 9. Risks and Rollback

- **Service/adapter contract drift** — the deployed service and `LocalBgeReranker` must
  agree on `/rerank` request/response shape (§7.5). Mismatched fields silently produce empty
  or wrong rerank results. Mitigated by test (3)/(4) and the E2E smoke test.
- **Resource footprint** — a CPU BGE model adds memory/CPU to the stack; acceptable per
  [R10.08] at 100 users, but document minimum resources.
- **Vector-only fallback masking outages** — if the service is down, retrieval silently
  degrades; ensure the warning is logged and, optionally, surfaced.
- **Rollback** — the change is additive: revert the enum values, factory branch, validation
  branch, settings, UI selector, and remove the Compose service. Existing Cohere/vector-only
  configs are unaffected; no schema migration.

## 10. Acceptance Criteria

- [ ] AC-1: The API-schema regression test (§8.1) fails before the fix and passes after;
  `rerank_provider="bge"` is accepted by create and patch.
- [ ] AC-2: A `bge` rerank config validates with **no** key when the bundled service URL is
  configured; it is rejected when the URL is empty and rejected when a key is supplied.
- [ ] AC-3: At runtime a `bge` config constructs `LocalBgeReranker` against the configured
  service URL; a Cohere config still constructs `RouterReranker`; neither keyless-`bge` nor
  Cohere silently falls through to vector-only when correctly configured.
- [ ] AC-4: A reranker-service failure degrades retrieval to vector-only with a logged
  warning, never failing the Agent turn.
- [ ] AC-5: Both the create and edit UIs expose a provider selector (`cohere` | local BGE);
  selecting local hides the key field and permits submit without a key; all strings via `$t()`.
- [ ] AC-6: `deploy/compose/docker-compose.yml` includes a bundled reranker service serving
  `bge-reranker-v2-m3`, reachable at the configured URL, honoring the adapter's `/rerank`
  contract (or the adapter is adjusted to the service's contract, with the choice recorded).
- [ ] AC-7: A `bge_reranker_url` setting exists with a sensible in-cluster default and an
  un-prefixed env alias, documented in `.env.example` and deploy docs.
- [ ] AC-8: An E2E smoke test exercises a `bge`-provider rerank end-to-end against the
  bundled service.
- [ ] AC-9: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build` pass in `frontend/`;
  `pnpm run gen:api` re-run if API types changed.

## 11. SRS Delta

None required for correctness — the fix restores the already-documented [R10.08] local
option. Optional clarification (apply only if the user wants the SRS to pin the deployment
detail): amend [R10.08] to name the bundled service's env-configured URL and `/rerank`
contract. Left out by default to keep the SRS free of implementation detail.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (health depth):** a full readiness/health-probe gate for the reranker service
  (mirroring the sandbox `supervisor_url` gate) may be deferred if §7.6's rerank-time error
  handling is deemed sufficient for launch; record which was implemented.
- **FU-2 (image provenance):** if a third-party rerank image is bundled, pin it by digest
  and record it in the CI build job, consistent with the sandbox image-pinning discipline
  (`settings.py:244-263`).
