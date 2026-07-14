---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R7.04, R10.05, R10.08]
---

# F-1: Revoked or foreign pinned RAG keys can still issue billed provider calls

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-1).
Release blocker — routes through `/check-security` before merge (audit FU-1).

## 1. Summary

Pinned File-RAG embedding and rerank keys are not consistently checked against the
project's carried-key scope. An editor can attach **another project's** Cohere rerank key
to a RAG config by UUID because rerank save-validation omits the project-scope check that
embedding validation performs. Independently, at Agent-retrieval time the pinned
embed/rerank adapters call `ProviderRouter.call_single_key`, which verifies only that the
key is active and capability-matched — never that `key_projects.carried` is still true for
the config's project. A key withdrawn from the project (`carried=false`) but still active
continues to embed/rerank and **bill the withdrawn key** on every subsequent turn. The
result is cross-tenant BYO-key spend, quota consumption, and misattributed audit records.

## 2. Observed vs Expected

- **Observed (save-time, sub-defect a)**: `_validate_rerank_key`
  (`backend/contexts/knowledge/application/config_service.py:67-76`) performs only
  exists / provider-match / capability checks. It does not accept `project_id` and never
  calls `is_key_in_project_scope`. Callers `create` (`:114-120`) and `update` (`:169-177`)
  therefore accept any active, RERANK-capable Cohere key by UUID regardless of project
  carry. By contrast `_validate_embed_key` (`:52-65`) *does* gate on scope at `:56-57`.
- **Observed (runtime, sub-defect b)**: `RagContextProvider` constructs pinned adapters
  from the stored config row with only the key UUID and no project context
  (`backend/contexts/knowledge/application/rag_context_provider.py:91-96` embedder,
  `:107-111` reranker). Those adapters call
  `ProviderRouter.call_single_key(key_id=..., request=...)`
  (`backend/contexts/knowledge/infrastructure/embedders.py:68-69`,
  `backend/contexts/knowledge/infrastructure/rerankers.py:61-62`), whose only
  authorization is `get_active` (soft-delete filter) + capability
  (`backend/contexts/keys/application/provider_router.py:589-593`). There is no
  `carried`-scope check, so a withdrawn-but-active key is billed indefinitely.
- **Expected**: a pinned embed/rerank key may be saved, and may issue a billed provider
  call, **only while it is carried into the config's project** (`key_projects.carried =
  true` and the key is not soft-deleted). This is the BYO-key scope invariant the rotation
  path already enforces via `list_ordered_carried`
  (`backend/contexts/keys/infrastructure/group_repository.py:147-181`; SEC-H3 comment at
  `provider_router.py:719-724`). Intent: [R7.04] (carried-key eligibility for provider
  calls), [R10.05] (load-bearing key-scope anchor), [R10.08] (rerank key handling).
  [R10.11] scopes documents, not keys — weak secondary only.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Package F-1/F-2/F-3 together or separately? | Three separate dossiers | F-1 and F-2 are independent release blockers; separate specs keep review/merge/revert lifecycles decoupled. |
| Q-2 | Runtime behavior when a pinned key is no longer carried at retrieval time? | Degrade + audit | Skip the un-scoped call and continue the turn (no embed key -> RAG source absent; rerank un-scoped -> vector-only). Preserves availability; the withdrawn key is never billed, satisfying the security boundary. Failing the whole turn would let one withdrawn key brick every Agent on the config. |
| Q-3 | Where is the runtime carried-scope check enforced? | Router chokepoint | Thread the expected `project_id` into `ProviderRouter.call_single_key` and enforce `carried` there — the single chokepoint the audit names, protecting every current and future pinned-key caller. The knowledge layer catches the scope error to drive the degrade path. |

## 4. Reproduction

**Sub-defect (a) — foreign rerank key at save time:**
1. Projects P and Q both exist under an org; user U is an editor on P and has previously
   seen (or can enumerate) the UUID of Q's active Cohere key K_q.
2. U creates or updates a RAG config in P with `rerank_enabled=true`,
   `rerank_provider=cohere`, `rerank_key_id=K_q`.
3. Save succeeds (no `CapabilityMismatch`) because `_validate_rerank_key` never checks
   whether K_q is carried into P.

**Sub-defect (b) — withdrawn key still billed at runtime:**
1. Project P has a RAG config with a valid carried embedding/rerank key K.
2. The key owner leaves P or withdraws K, setting `key_projects.carried=false` while K's
   `api_keys.deleted_at` stays NULL (K remains active for its home project).
3. An Agent in P runs a turn that triggers RAG retrieval. `RagContextProvider` builds the
   pinned adapters and `call_single_key` embeds/reranks with K — billing the withdrawn key.

## 5. Root Cause Analysis

Two distinct root causes share one theme (pinned keys escape the carried-scope gate):

- **RC-a (save-time)**: `_validate_rerank_key` (`config_service.py:67-76`) neither accepts
  `project_id` nor calls `KeysFacade.is_key_in_project_scope`. The earliest corrective
  link is this method's signature + body; the two call sites already have the project
  available (`create` param `project_id`; `update` via `cfg.project_id`, already read at
  `config_service.py:167`).
- **RC-b (runtime)**: `ProviderRouter.call_single_key`
  (`provider_router.py:579-629`) enforces only `get_active` + capability. It has no
  `project_id` parameter, so it *cannot* check `carried` even in principle. The earliest
  corrective link is `call_single_key`'s signature — add the expected project and enforce
  scope there. The adapters (`embedders.py`, `rerankers.py`) and `RagContextProvider`
  (`rag_context_provider.py`) are aggravating links that must thread `project_id` through,
  but the authoritative gate belongs at the router chokepoint.

## 6. Blast Radius and Sibling Suspects

- **Blast radius**: every Agent and document using an affected File-RAG config — cross-
  tenant BYO-key spend, quota consumption, rate-limit contention, and audit attribution to
  the wrong tenant. Persisted configs already holding a foreign/withdrawn pinned key are
  neutralized automatically once RC-b lands (the runtime guard degrades them); RC-a
  prevents new ones and corrects an existing one on its next save.
- **Sibling suspects:**
  - **Embedding save path** — `_validate_embed_key` (`config_service.py:52-65`): CLEARED,
    already gates on `is_key_in_project_scope` at `:56-57`. `update` does not re-validate
    embed fields, but embed fields are not in the mutable set (`:180-188`), so no gap.
  - **GraphRAG / Knowledge-Map builder keys** — separate finding surface (F-13/F-14),
    out of scope here; do not fold in.
  - **Rotation path** (`call` / `call_stream` via `_load_eligible` ->
    `list_ordered_carried`, `provider_router.py:716-734`): CLEARED, already carried-scoped.
    `call_single_key` is the sole pinned-key entry point lacking the check.

## 7. Fix Design

**RC-a — rerank save validation (defense in depth + immediate feedback):**
- Give `_validate_rerank_key` a keyword `project_id: uuid.UUID` and add the scope check
  mirroring `_validate_embed_key:56-57`:
  `if not await self._keys_facade.is_key_in_project_scope(key_id, project_id): raise CapabilityMismatch(...)`.
- `create` passes its `project_id` param; `update` passes `cfg.project_id`
  (already loaded at `config_service.py:167`).

**RC-b — router chokepoint enforcement (authoritative gate):**
- Add a keyword `project_id: uuid.UUID` to `ProviderRouter.call_single_key`
  (`provider_router.py:579`). After `get_active` + capability, enforce carried scope using
  the existing predicate: reuse `is_key_in_project_scope` (or the underlying
  `CarryRepository` join) so a `carried=false` or foreign key raises a **new dedicated
  error** (e.g. `KeyProjectScopeError`) rather than being called. Do not reuse
  `KeyNotFound`/`CapabilityMismatch` — the degrade path needs to distinguish scope failure.
- Thread `project_id` through the two pinned adapters: `RouterEmbedder` /
  `router_embedder_for` (`embedders.py:61,80-88`) and `RouterReranker` (`rerankers.py:49`),
  each storing and forwarding the project. `RagContextProvider` supplies `cfg.project_id`
  when constructing them (`rag_context_provider.py:91-96`, `:107-111`).

**Degrade + audit behavior (Q-2):**
- In `RagContextProvider`, catch `KeyProjectScopeError` around the embed and rerank calls:
  an embed scope failure -> return no RAG block for this config (source absent); a rerank
  scope failure -> fall back to vector-only ranking. Emit one audit/log event per
  degradation naming config id, key id, and project — **without logging the key secret**
  (CLAUDE.md: never log keys/tokens). The router remains the hard guarantee: even if a new
  caller forgets to degrade, the out-of-scope key is never billed.

**Security considerations** (this fix is the security control):
- The router check is a fresh DB join per call (no DEK-cache dependency), so a
  mid-session withdrawal takes effect on the next turn — mirror the `list_ordered_carried`
  freshness contract (`group_repository.py:148-158`).
- Timing: scope failure and capability failure should not leak *why* a call was refused to
  the end user beyond a generic "knowledge source unavailable"; the specifics go to audit.
- Confirm no code path logs `request` payloads containing embedding input alongside key ids.

**Reuse inventory** (do not re-invent):
- `KeysFacade.is_key_in_project_scope(key_id, project_id)`
  (`backend/contexts/keys/interfaces/facade.py:147-179`) — the exact "key carried into
  project?" predicate; already the embedding gate.
- `CarryRepository` (`backend/contexts/keys/infrastructure/carry_repository.py`) and
  `CarryService.list_active_in_project` — lower-level carried joins if the router needs a
  repo-level check without the facade.
- `list_ordered_carried` (`group_repository.py:147-181`) — reference for the freshness /
  fail-closed semantics to match.

**Patterns to follow (SoC):** the carried-scope enforcement belongs in the keys context
(`provider_router` / keys repositories); the knowledge context only *supplies* the project
and *reacts* to the scope error. Do not duplicate the carry join inside the knowledge
context — call the keys port.

**Data repair:** none required. RC-b neutralizes already-persisted foreign/withdrawn
pinned keys at runtime (they degrade, never bill); RC-a corrects a config on its next
save. An optional audit sweep to surface pre-existing misconfigured configs is recorded as
FU-1, not in scope.

## 8. Regression Test Plan

Failing-first tests (each fails against current code, passes after the fix):

1. **`config_service` rerank scope (RC-a)** — new unit test: `create` and `update` with a
   `rerank_key_id` that is **not** carried into the config's project raise
   `CapabilityMismatch`. Fails today because `_validate_rerank_key` performs no scope
   check. Mirror the existing embed-scope test if one exists.
2. **`provider_router.call_single_key` scope (RC-b)** — new unit test: calling with a
   `key_id` whose `key_projects.carried=false` for the supplied `project_id` raises
   `KeyProjectScopeError`; with `carried=true` it proceeds. Fails today (no `project_id`
   param, no check).
3. **`RagContextProvider` degrade (Q-2)** — new unit test: with a withdrawn embed key the
   provider returns no RAG block and emits one audit event; with a withdrawn rerank key it
   returns vector-only results and emits one audit event. Fails today (call is billed, no
   degrade). Assert no key secret appears in the emitted event/log.

## 9. Risks and Rollback

- **Risk**: threading `project_id` through `call_single_key` touches a shared keys-context
  signature; every caller must supply the project. Mitigate by making the parameter
  required (compile/type surface flags missed callers) and running the keys + knowledge
  suites.
- **Risk**: over-degrading — a false "not carried" negative would silently drop RAG. The
  new dedicated `KeyProjectScopeError` (not a broad `except Exception`) keeps degrade
  narrow.
- **Rollback**: revert the dossier's commits; the added parameter and error type are
  additive and self-contained, so rollback restores prior behavior without migration.

## 10. Acceptance Criteria

- [ ] AC-1: The three regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: `_validate_rerank_key` rejects a rerank key not carried into the config's
  project on both `create` and `update`.
- [ ] AC-3: `ProviderRouter.call_single_key` refuses (raises `KeyProjectScopeError`) any
  key whose `key_projects.carried` is false or absent for the supplied project, for both
  embed and rerank capabilities.
- [ ] AC-4: At retrieval time a withdrawn/foreign embed key yields no RAG block and a
  withdrawn/foreign rerank key yields vector-only results; each degradation emits exactly
  one audit event containing config/key/project ids and **no** key secret.
- [ ] AC-5: The rotation/multi-key path (`call` / `call_stream`) and the embedding
  save-path behavior are unchanged (existing keys + knowledge suites still green).
- [ ] AC-6: `/check-security` review passes for the BYO-key cross-tenant billing boundary
  (audit FU-1).

## 11. SRS Delta

None — this restores the documented [R7.04]/[R10.05]/[R10.08] carried-key scope invariant.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: optional one-time audit sweep to report existing RAG configs whose pinned
  embed/rerank key is not carried into their project (surface for owner cleanup). The
  runtime guard already neutralizes them, so this is hygiene, not a correctness gap.
