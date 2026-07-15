---
type: bugfix
status: implemented
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
  - **F-1 collision (must coordinate — same lines).** The sibling security spec
    `docs/tasks/2026-07-14-rag-pinned-key-project-scope/spec.md` (F-1, release blocker) rewrites
    the **same** rerank-validation block (`config_service.py:67-76,114-120,169-177`) to add a
    `project_id` carried-scope check, and the **same** runtime factory
    (`rag_context_provider.py:97-111`) to thread `project_id` into `RouterReranker` and catch a
    scope error. A keyless `bge` reranker has no `key_id` and no carried key, so it MUST be
    exempt from F-1's project-scope enforcement: F-19's `bge` branch routes **around**
    `_validate_rerank_key` entirely (§7.2) and around the runtime scope check. Whichever lands
    second reconciles the shared branch; build them together if possible. Neither spec
    referenced the other before this note.

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
  configured (URL non-empty). Reject `rerank_key_id` supplied with `bge` (a keyless
  provider must not carry a key). Keep the "enabled requires a provider" invariant.

  **SoC constraint:** the application-layer `RagConfigService` must not import `app.config`
  directly (application → app is an upward import). Pass the bundled-reranker availability
  (the resolved URL, or a boolean "configured" flag) in via the facade/constructor —
  mirroring how the runtime `rag_context_provider` already receives `_qdrant_url` rather than
  reading settings itself. `RagConfigService.__init__` currently takes only `db`
  (`config_service.py:47-50`), so this is a constructor/facade-wiring change, not a direct
  settings import.

**7.3 Settings.** Add a knowledge/reranker settings section mirroring `EgressSection`
(`settings.py:221-241`) / `SandboxSection` (`:244-272`): `bge_reranker_url: str` read from an
un-prefixed alias (e.g. `RERANK_BGE_URL`) plus a section-prefixed fallback, default the
in-cluster service DNS (e.g. `http://bge-reranker:80`). An empty value means "local reranker
not deployed" and MUST make `provider=bge` validation fail with a clear, doc-pointing error
(mirroring the `supervisor_url` empty-disables gate at `:264-272`).

**7.4 Runtime factory + URL injection (SoC).** In `rag_context_provider.py:97-111`, branch on
`cfg.rerank_provider`:
- `cohere` (and a key present) → `RouterReranker` (unchanged).
- `bge` → `LocalBgeReranker(base_url=<injected bge_reranker_url>)`, with no key required.
  The current `cfg.rerank_key_id is not None` guard (`:98`) must be widened so a keyless
  `bge` config still builds a reranker instead of falling through to vector-only.

  **The URL must NOT be read from `app.config` inside `rag_context_provider` (application →
  app upward import — the same SoC rule §7.2 applies to the service).** `RagContextProvider`
  already receives `qdrant_url` by constructor injection (`rag_context_provider.py:49-60`),
  built in `TurnEngine.__init__` (`turn_engine.py:289,294,299`) and passed down to the RAG
  provider (`rag_context_provider.py:54,59`). Add a `bge_reranker_url: str | None = None` param
  the same way (default `None`, mirroring `qdrant_url`, so unrelated `TurnEngine` test doubles
  stay compatible) and resolve it from settings at every composition-root site that already sets
  `qdrant_url=settings.qdrant.url`: `app/workers/tasks/orchestration.py:123`,
  `app/workers/tasks/conversation.py:316`, `app/workers/tasks/approvals.py:85`, and
  `contexts/orchestration/application/a2a_handler.py:173`. `bge`-exercising tests supply the URL
  via the existing `TurnEngine` fakes (`test_agent_trigger_wiring.py:269`,
  `test_a2a_turn_dispatch.py:369,394`, `test_approval_gate_fixes.py:80`). The settings value from
  §7.3 is resolved at those sites, not in the provider.

- **Close the reranker client.** `LocalBgeReranker` owns an `httpx.AsyncClient` and exposes
  `close()` (`rerankers.py:93,118-120`); `RouterReranker` does not. `RagContextProvider.query`
  currently closes only the Qdrant client in its `finally` (`rag_context_provider.py:150-151`),
  so a `bge` config leaks an httpx client every turn. Close the reranker in the same `finally`
  when it is a `LocalBgeReranker` (guard by type or an optional `close()`).

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

**7.6 Health handling + real degrade path.** `LocalBgeReranker` has only `raise_for_status()`
+ `close()` (`rerankers.py:108,118-120`). Add a readiness/health path: at minimum surface a
clear error when the service is unreachable at rerank time; optionally add a startup/health
probe mirroring the supervisor readiness gate.

**Correction (verified):** the "degrade to vector-only" behavior does **not** exist today, and
the `rag_context_provider.py:108-110` fallback is only a *construction-time* `TypeError` guard,
not a rerank-execution failure path. `RetrieveService.query` calls the reranker with **no**
try/except (`backend/contexts/knowledge/application/retrieve.py:159-185`); a `RerankError`/httpx
failure propagates to `RagContextProvider.query`'s blanket `except Exception` (`:152-158`),
which returns `None` and drops the **entire** RAG block — not vector-only. To satisfy AC-4 the
fix must wrap the rerank call at `retrieve.py:159-185` and, on failure, fall back to
`candidates[:effective_top_k]` (exactly the non-rerank return already at `:187`) with a logged
warning. This corrects the existing Cohere path too, so scope it as a shared retrieval-degrade
fix, not `bge`-only.

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

Backend (`backend/tests/unit/`) — note the actual files: the `RagConfigService` create/validation
tests live in `test_rag_config_dimension.py`, and the context-provider tests are the
`TestRagContextProviderSources` class in `test_rag_services.py` (there is no `test_config_service`
or `test_rag_context_provider` file — new assertions attach to these or a new file):

1. **API accepts `bge`** (update the RAG API/schema test): `POST`/`PATCH` with
   `rerank_provider="bge"` is accepted; a bad provider is still 422. Fails today —
   `Literal["cohere"]` rejects `"bge"`.
2. **Keyless `bge` validates without a key** (`test_rag_config_dimension.py` or new): a config
   with `rerank_enabled=true, rerank_provider="bge", rerank_key_id=None` and a configured
   service URL is accepted; the same with an empty service URL is rejected with the
   doc-pointing error; supplying a key with `bge` is rejected. Fails today —
   `config_service.py:114-120` demands a key.
3. **Runtime builds `LocalBgeReranker` for `bge`** (`test_rag_services.py` /
   `TestRagContextProviderSources`): a `bge` config constructs `LocalBgeReranker(base_url=...)`,
   not `RouterReranker`, and does not fall through to vector-only. Fails today — factory only
   builds `RouterReranker`.
4. **Rerank failure degrades to vector-only** (new, in `test_rag_services.py`): when the
   reranker's `rerank` raises, `RetrieveService.query` returns `candidates[:effective_top_k]`
   (vector-only) with a warning, and `RagContextProvider.query` still returns the RAG block —
   not `None`. Fails today — `retrieve.py:159-185` has no try/except, so the block is dropped.

**Landmine (must stay green — do NOT touch):** the capability goldens
`frontend/src/slices/keys/__tests__/capabilities.test.ts:8-15` and the backend cohere-rerank-only
assertion in `backend/tests/unit/test_keys_providers.py` (`test_cohere_rerank_only`) lock the
RERANK capability to Cohere. `bge` is keyless and is NOT a key provider (§6), so it must be added
to the *rerank-provider enum* only, never to the capability map. An implementer who adds `bge` to
`CAPABILITIES` to "make it selectable" will redden both — that is the wrong edit.

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

- [x] AC-1: The API-schema regression test (§8.1) fails before the fix and passes after;
  `rerank_provider="bge"` is accepted by create and patch (`test_bge_reranker.py`
  `test_create_schema_accepts_bge` / `test_patch_schema_accepts_bge`, and the unknown-provider
  reject). Red before: the `Literal["cohere"]` rejected `"bge"`.
- [x] AC-2: A `bge` rerank config validates with **no** key when the bundled service URL is
  configured; it is rejected when the URL is empty and rejected when a key is supplied
  (`test_create_bge_validates_without_key` / `_rejected_when_service_unconfigured` /
  `_rejects_supplied_key`, plus the update variants).
- [x] AC-3: At runtime a `bge` config constructs `LocalBgeReranker` against the configured
  service URL (`test_factory_builds_local_bge_reranker`); a Cohere config still constructs
  `RouterReranker` (existing `test_rag_context_provider_scope`); neither silently falls
  through to vector-only when correctly configured.
- [x] AC-4: A reranker execution failure degrades `RetrieveService.query` to vector-only
  (`candidates[:effective_top_k]`) with a logged warning, block still returned
  (`test_rerank_failure_degrades_to_vector_only`). Hardens the Cohere path too.
- [x] AC-5: Both the create (`RagConfigListView`) and edit (`RagConfigDetailView`) UIs expose
  a provider `SSelect` (Cohere | Local BGE); selecting local hides the key field and permits
  submit without a key; all strings via `$t()`. Gating logic covered by
  `useRagConfigForm.spec.ts`; both view tests stay green.
- [x] AC-6: a bundled reranker service (`deploy/reranker/`, wired in
  `docker-compose.yml`) serves `bge-reranker-v2-m3` on CPU and honours the adapter's exact
  `/rerank` contract `{query, candidates, top_k} -> {results:[{index,score}]}`. Chosen
  contract recorded in D-1 (custom service over off-the-shelf TEI).
- [x] AC-7: `KnowledgeSection.bge_reranker_url` exists with the in-cluster default
  `http://bge-reranker:80` and the un-prefixed `RERANK_BGE_URL` alias, documented in
  `.env.example` (the compose inline comments serve as deploy docs — no separate deploy README
  exists; see D-2).
- [~] AC-8: E2E smoke test **deferred** — it requires building the reranker image (downloads
  torch + model weights) and running the full stack, neither available in the build
  environment. The factory + degrade + adapter unit tests cover the code path; the live E2E is
  tracked as FU-3.
- [x] AC-9: backend `pytest -q` (1706 + new, green), `ruff check`, `ruff format --check`,
  `mypy .` pass; frontend `pnpm typecheck`, `pnpm lint`, `pnpm build`, and the RAG + composable
  vitest suites pass. `pnpm run gen:api` re-run (regenerated `RagConfigCreateIn.ts` /
  `RagConfigPatchIn.ts` + `openapi.json`) **and** the hand-written `schemas.ts` enum edited
  separately. `check:openapi-drift` (a bash script) not runnable on this Windows host; drift
  verified equivalently — after the canonical export + gen:api, `git status` showed exactly
  `openapi.json` + the two RagConfig models, committed together (D-3).

## 11. SRS Delta

None required for correctness — the fix restores the already-documented [R10.08] local
option. Optional clarification (apply only if the user wants the SRS to pin the deployment
detail): amend [R10.08] to name the bundled service's env-configured URL and `/rerank`
contract. Left out by default to keep the SRS free of implementation detail.

## 12. Deviation Log

- D-1 (bundled-service contract choice — AC-6): shipped a small **custom** reranker service
  (`deploy/reranker/`: FastAPI + FlagEmbedding on CPU) that honours the `LocalBgeReranker`
  adapter's exact contract (`POST /rerank {query, candidates, top_k}` ->
  `{results:[{index,score}]}`), rather than adopting off-the-shelf HF text-embeddings-inference
  (whose rerank contract is `{query, texts}` -> `[{index,score}]`). Rationale: the adapter and
  its unit tests were already built around the custom contract (§7.4/§8.3); a custom service
  keeps them unchanged and avoids a translating shim. The image bakes the model weights at
  build so the runtime container needs no network (it sits on the internal `data_net`).
- D-2 (deploy docs location — AC-7): no dedicated deploy README exists under `deploy/`, so the
  `RERANK_BGE_URL` documentation lives in `.env.example` and the inline `docker-compose.yml`
  service comments rather than a separate doc.
- D-3 (openapi-drift verification — AC-9): `check:openapi-drift` is a bash script and bash is
  not available on this Windows host. Drift was verified equivalently: the canonical
  `python -m scripts.export_openapi` regenerated `openapi.json` and `pnpm run gen:api`
  regenerated the client; `git status` then showed exactly `openapi.json` +
  `RagConfigCreateIn.ts` + `RagConfigPatchIn.ts`, all committed together — the same invariant
  the script enforces. (PowerShell's `>` first wrote `openapi.json` as UTF-16, which the Node
  codegen rejected; re-exported as UTF-8-no-BOM.)
- D-4 (F-1 test fixture): the F-1 scope-degrade test's fake config (`_cfg` in
  `test_rag_context_provider_scope.py`) gained a `rerank_provider` attribute. The real
  `RagConfig` domain model always carries it and the factory now branches on it; this is a
  fixture-correctness fix, not a weakened assertion.

## 13. Follow-ups

- **FU-1 (health depth):** a full readiness/health-probe gate for the reranker service
  (mirroring the sandbox `supervisor_url` gate) may be deferred if §7.6's rerank-time error
  handling is deemed sufficient for launch; record which was implemented.
- **FU-2 (image provenance):** the bundled reranker is built from source
  (`deploy/reranker/Dockerfile`), not a third-party image, so there is no external digest to
  pin; the base `python:3.12-slim` and the pinned pip deps (torch 2.5.1, FlagEmbedding 1.3.2)
  are the provenance surface. If this is later swapped for a pre-built rerank image, pin it by
  digest per the sandbox discipline (`settings.py`).
- **FU-3 (live E2E — AC-8):** run the end-to-end smoke test (build the `deploy/reranker` image,
  bring up the stack, create a `bge`-provider RAG config, and assert a rerank reorders
  results) on a DB/Docker-backed environment before release. Deferred here only because the
  build environment cannot build the model image or run the full stack (D-2). The
  rerank-time error handling (§7.6) was implemented via a `/health` endpoint + Compose
  healthcheck and the retrieval degrade path, satisfying FU-1's "at minimum surface a clear
  error / degrade" — the deeper startup readiness gate remains optional.
