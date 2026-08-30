---
type: bugfix
status: draft
created: 2026-08-30
requirements: [R9.03a, R24.35]
depends_on: []
---

# Runtime contract integrity

## 1. Summary

Close two recent, user-visible contract failures as one boundary-hardening change:

- FU-5 of `2026-08-20-orchestration-room-scoped-reads`: two frontend recovery branches test
  against the generated `ApiError`, while the transport throws `@shared/errors.ApiError`; their
  green tests construct the same wrong class.
- FU-4 of `2026-08-27-provider-model-capability-table`: the Agent form persists a `seed`, runtime
  payloads carry it, but no adapter currently forwards it. The UI incorrectly says it applies to
  OpenAI and is ignored by Gemini, while the current official Gemini GenerationConfig exposes
  `seed` and the OpenAI Responses request does not.

Both failures are contracts that look typed/configured but are inert at the runtime boundary. The
fix makes the actual thrown error and per-parameter provider capability authoritative and adds
negative lint/adapter tests so fixtures cannot preserve the lie.

Freshness was re-verified against `main` at `73125821` (2026-08-28), plus official provider API
references on 2026-08-30. The source files and official URLs are recorded below.

## 2. Observed vs Expected

### Wrong typed-error identity

- **Observed.** `useChatroomMessages.ts:11,187-203` and
  `usePromptAssistantSocket.ts:3,93-105` import generated `@shared/api-client.ApiError` and use
  `instanceof`. Their tests import and construct that class too. The transport instead throws the
  hierarchy created by `frontend/src/shared/transport/problem-json.ts:34-68` and exported from
  `@shared/errors`.
- **Expected.** Runtime branches and their tests use the one hierarchy promised by [R24.35]. A
  generated `ApiError` named import is rejected in production and tests.

### Seed capability

- **Observed.** `_sampling_payload` carries non-null `seed`
  (`backend/contexts/agents/application/runtime/turn_engine.py:179-195`), and the form always renders
  an enabled field (`frontend/src/slices/agents/views/AgentDetailView.vue:1033-1044`). Yet the base
  contract says no adapter forwards it and `accepts_sampling` gates temperature/top-p/seed together
  (`backend/contexts/keys/infrastructure/adapters/base.py:41-58`). OpenAI deliberately omits it from
  Responses (`adapters/openai.py:238-245`), Gemini explicitly drops it (`adapters/gemini.py:120-135`),
  and Anthropic never maps it (`adapters/anthropic.py:145-159`). English help claims the opposite
  provider matrix (`frontend/src/slices/agents/locales/en.json:145-154`).
- **Expected.** [R9.03a]'s per-request-parameter capability drives adapter shaping and UI honesty.
  `seed` is independently enabled only when the selected model's endpoint accepts it. Current
  official references establish:
  - Gemini `GenerationConfig.seed` is an optional integer:
    `https://ai.google.dev/api/generate-content` (GenerationConfig, verified 2026-08-30).
  - OpenAI Responses create exposes temperature/top-p but no seed:
    `https://developers.openai.com/api/reference/cli/resources/responses/methods/create`
    (verified 2026-08-30).
  - Anthropic Messages exposes no seed:
    `https://docs.anthropic.com/en/api/messages` (verified 2026-08-30).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which follow-ups form this change? | Wrong-`ApiError` FU-5 plus provider-capability FU-4; clipboard migration returns to follow-up. | The two retained defects break runtime contracts and recovery/config correctness. Clipboard is lower value and was the only reason to overlap ChatroomView. |
| Q-2 | Does this depend on another dossier? | No; `depends_on: []`. | Removing clipboard removes all ChatroomView overlap. Error sites are separate composables/tests; seed touches agents model specs, key adapters, generated catalogue output and AgentDetailView. Active graphrag/large-artifact dossiers touch neither region. |
| Q-3 | Should error consumers use structural status checks? | No. Import `ApiError` from `@shared/errors` and retain `instanceof` plus status checks. | R24.35 promises one typed hierarchy. Structural checks would hide a future transport regression. |
| Q-4 | How is the generated error import prevented in tests when tests disable `no-restricted-imports`? | Add a global `no-restricted-syntax` AST selector for `ImportSpecifier[imported.name='ApiError']` under an `ImportDeclaration` whose source is `@shared/api-client`; exclude only the generated tree. Add a negative ESLint fixture test. | The current test override turns `no-restricted-imports` off (`frontend/eslint.config.js:344-350`). A separate selector remains active in production and test overrides without re-enabling unrelated slice restrictions in fixtures. |
| Q-5 | Extend `accepts_sampling` or add a seed capability? | Add `accepts_seed`; define `accepts_sampling` as temperature/top-p only. | Providers accept these parameters independently. One coarse flag is the root cause of both UI and adapter dishonesty. |
| Q-6 | Which catalogued models accept seed? | Catalogued Gemini rows set `accepts_seed: true`; OpenAI Responses and Anthropic rows set false; unknown/custom models use false. | This is the conservative-floor rule of R9.03a applied to current official endpoint contracts. |
| Q-7 | What happens to an existing stored seed on an unsupported model? | Preserve it when loading an existing Agent but render disabled with truthful help; clear it only on a user-initiated provider/model change to an unsupported target. | Matches the capability-table handling for pre-existing inert effort/sampling values and avoids destructive edit-load side effects. |
| Q-8 | Does this change reproducibility guarantees? | No. Help text describes seed as a decoding input that can reduce variance, not bit-identical output. | Provider-side changes and nondeterministic execution remain possible; the field must not promise more than the API contract. |

## 4. Reproduction

### R1 — deleted message anchor recovery is dead

Return a real transport 422 while `useChatroomMessages` loads earlier history. The generated-class
`instanceof` is false, so the stale anchor is not cleared/reconciled/retried even though the test
using the generated class is green.

### R2 — expired prompt session recovery is dead

Return a real transport 404 from prompt-assistant resume/send. The generated-class branch is false,
so the session is not marked expired and the expected recovery feedback does not run.

### R3 — seed is inert and mislabeled

1. Configure an Agent with a Gemini model and seed.
2. Inspect `_chat_body`: `generationConfig` contains temperature/topP but no seed.
3. The UI says Gemini ignores it even though the current official GenerationConfig accepts it.
4. Select OpenAI: the same enabled field persists while Responses has no seed request parameter.

## 5. Root Cause Analysis

### Error class

The generated client includes its own transport exception, but SMAP's axios/problem-json layer
normalizes failures into a different hierarchy. Imports from the generated barrel make the wrong
class easy to discover, and tests repeated the production import instead of exercising the actual
transport shape. Because both classes expose `status`, types and happy-path tests did not expose the
identity mismatch.

### Provider seed

The capability-table migration introduced a model record but retained one `accepts_sampling` boolean
for multiple independent parameters (`model_specs.py:63-71,319-328`). Adapters already special-case
seed by omission, while the UI gates only temperature/top-p (`AgentDetailView.vue:292-305,385-394`)
and renders seed unconditionally. A stale Gemini comment and stale English help then inverted the
real current provider support.

## 6. Blast Radius and Sibling Suspects

- Error paths: history pagination after a 422 stale anchor and prompt-assistant 404 expiry only.
  Other conversation error consumers already import `@shared/errors`.
- Seed: every Agent create/edit payload, all three chat adapters, model catalogue output and the
  Agent form. Agent persistence schema does not change.
- Temperature/top-p behavior remains under `accepts_sampling`; effort, vision and token fields are
  unchanged.
- Clipboard FU-12 remains real at `useEntityLifecycle`, `ChatroomSettingsView` and `ChatroomView`,
  but is deliberately deferred to avoid lowering this runtime-contract PR's value and reintroducing
  an overlap dependency.

## 7. Fix Design

1. Replace the two production and two test generated `ApiError` imports with `@shared/errors`.
   Regression fixtures construct the shared class using the real RFC 7807 shape so the failing
   branch is red before the import fix.
2. Add the global ESLint AST restriction from Q-4. Keep the existing test override for unrelated
   cross-slice fixtures. A config test lints virtual production and test snippets, rejecting the
   generated named import and accepting generated models/services plus `@shared/errors.ApiError`.
3. Add `accepts_seed: bool` to `ChatModelSpec`, conservative resolution, catalogue/API output,
   cross-context payload flags and `CapabilityFlags`. Document that `accepts_sampling` means only
   temperature/top-p. Gemini rows are true; OpenAI/Anthropic/unknown are false.
4. In Gemini `_chat_body`, add `generationConfig.seed` when `caps.accepts_seed` and payload seed are
   non-null. Do not nest it under `accepts_sampling`. OpenAI Responses and Anthropic continue to
   omit seed, now because an explicit flag is false rather than an undocumented special case.
5. Add `seedDisabled` and truthful help to AgentDetailView. Seed enablement follows
   `selectedModelSpec.accepts_seed` independently from temperature/top-p. Extend the existing
   user-initiated capability-clear function without clearing values during edit-load/catalog load.
6. Regenerate OpenAPI/client catalogue types and update English/zh-TW copy. Correct base/adapter/
   turn-engine comments so no code still says Gemini lacks seed or OpenAI receives it.

## 8. Regression Test Plan

- **T-1 message recovery** — throw `@shared/errors.ApiError` 422, assert stale anchor removal,
  cache reconciliation and retry. The current test fixture fails before the import fix.
- **T-2 prompt expiry** — throw the shared 404 and assert expired state/recovery feedback.
- **T-3 lint negative fixtures** — virtual production and `*.test.ts` imports from generated
  `ApiError` both fail; shared error and other generated imports pass.
- **T-4 domain catalogue** — every spec carries a boolean seed capability; unknown/custom is false;
  Gemini true and OpenAI/Anthropic false.
- **T-5 adapter request shaping** — fixed seed appears as Gemini `generationConfig.seed` for
  accepted rows and is absent when the flag is false; OpenAI/Anthropic never receive it. A separate
  case proves seed forwarding is independent of temperature/top-p acceptance.
- **T-6 Agent form** — Gemini enables seed with accurate help; OpenAI/Claude/unknown disable it;
  user model changes clear unsupported seed while edit-load preserves a legacy value; temperature/
  top-p enablement is unchanged.
- **T-7 contract generation** — OpenAPI and frontend catalogue types include `accepts_seed`; drift
  gate is clean.

## 9. Risks and Rollback

- **Provider drift.** Capability rows carry source and verification date; unknown models remain
  false. The existing model-catalog reconciliation process remains the operator freshness path.
- **Over-broad lint selector.** Negative/positive fixtures prove only the named generated class is
  rejected; generated DTO/service imports and shared errors remain legal.
- **Unexpected Gemini model refusal.** Adapter tests pin shaping, and the conservative table enables
  only catalogued rows. A live-key smoke may be recorded at implementation but is not required for
  deterministic CI.
- **Edit data loss.** Capability clearing runs only on user-initiated changes, never initial reset.
- **Rollback.** Revert UI/catalogue/adapter generation and then error imports/lint rule. Persistence
  columns are unchanged; a stored seed remains inert as before.

## 10. Acceptance Criteria

- [ ] AC-1: a shared transport 422 reaches message-anchor recovery, clears the poisoned anchor,
  reconciles cache and retries; the test fails before the fix.
- [ ] AC-2: a shared transport 404 marks the prompt session expired and produces the intended
  recovery state; the test fails before the fix.
- [ ] AC-3: no production or test file outside `src/shared/api-client/**` imports generated
  `ApiError`; ESLint rejects a virtual violation in both production and test override contexts.
- [ ] AC-4: the lint restriction permits other generated API symbols and `@shared/errors.ApiError`,
  and existing slice/store/session boundary fixtures continue passing.
- [ ] AC-5: `ChatModelSpec`, API output and adapter payload flags expose independent
  `accepts_seed`; unknown models resolve false.
- [ ] AC-6: catalogued Gemini request bodies forward a configured seed exactly once as
  `generationConfig.seed`; OpenAI Responses and Anthropic bodies omit it.
- [ ] AC-7: seed forwarding is independent of temperature/top-p capability; changing one flag does
  not silently govern the other parameter family.
- [ ] AC-8: the Agent form enables seed only for selected models whose row accepts it, preserves a
  legacy stored value on load, clears it on a user switch to unsupported, and displays truthful
  translated help in both locales.
- [ ] AC-9: stale comments/help claiming Gemini ignores seed or OpenAI accepts it are gone; help does
  not promise bit-identical determinism.
- [ ] AC-10: OpenAPI/client regeneration and focused backend/frontend tests, full frontend test,
  lint, current typecheck and build pass.

## 11. SRS Delta

None. The change restores [R9.03a]'s existing per-request-parameter capability authority and
[R24.35]'s existing single typed-error hierarchy. It does not add a provider capability not present
in the verified official endpoint contract.

## 12. Security Considerations

- Provider keys continue through the existing envelope-decrypt adapter path and are never logged,
  surfaced to the frontend or added to test fixtures.
- Seed is bounded by the existing typed Agent request model and remains a scalar configuration
  value; it never affects URL construction, authorization or tenant scoping.
- Error fixtures use synthetic problem details and contain no request body, token or provider key.

## 13. Quality Notes

- **Existing debt** — the generated barrel exposes an error class that SMAP transport does not
  throw; this task guards consumers rather than editing generated code. `accepts_sampling` is an
  imprecise historical name retained for compatibility and explicitly narrowed in documentation.
- **Patterns** — shared typed errors, one capability table, conservative unknown-model floor,
  adapter shaping from passed flags, user-action-only clearing in AgentDetailView.
- **Reuse** — `problem-json`, `@shared/errors`, `resolve_spec`, `capability_payload`,
  `CapabilityFlags`, existing Agent form capability computeds and ESLint flat config.

## 14. Deviation Log

None — implementation has not started.

## 15. Follow-ups

- FU-1: migrate the three hand-rolled clipboard callers to `useClipboard`, fix false feedback and
  add a direct-clipboard lint guard after current ChatroomView work settles (source onboarding FU-12).
- FU-2: split/rename `accepts_sampling` to `accepts_temperature_top_p` only if another consumer needs
  the semantic name; this dossier documents it and avoids churn across generated contracts.
- FU-3: enable strict Vue templates through a staged remediation programme; the current backlog is
  too broad for this runtime fix.
- FU-4: add isolated unit/Playwright typecheck projects after their existing backlogs are classified.
