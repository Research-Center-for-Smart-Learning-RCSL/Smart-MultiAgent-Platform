---
type: bugfix
status: implemented
created: 2026-08-30
approved: 2026-09-01
implemented: 2026-09-01
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
| Q-2 | Does this depend on another dossier? | No; `depends_on: []`. Two file overlaps exist and are deliberately not dependencies, so whoever builds second rebases. | Checked against all four other non-implemented dossiers. Removing clipboard removes all ChatroomView overlap with `2026-08-30-chatroom-approval-and-overlay-discoverability`, and `2026-08-30-identity-onboarding-policy-hardening` is confined to the identity/Admin surface. Error sites are separate composables/tests. The two remaining overlaps are same-file, disjoint-region: `2026-07-07-graphrag-two-axis-redesign` edits `AgentDetailView.vue`'s General and Knowledge tabs (`:820-855,935-1000`) while this dossier edits the capability computeds and sampling fields (`:292-305,385-394,1033-1044`), and `2026-07-19-large-artifacts-silently-dropped` edits `turn_engine.py`'s `_persist_artifacts` (`:1124-1171`) while this dossier only corrects the `_sampling_payload` comment (`:179-195`). Neither is a logical prerequisite; sequencing them would block two independent fixes on each other. |
| Q-3 | Should error consumers use structural status checks? | No. Import `ApiError` from `@shared/errors` and retain `instanceof` plus status checks. | R24.35 promises one typed hierarchy. Structural checks would hide a future transport regression. |
| Q-4 | How is the generated error import prevented in tests when tests disable `no-restricted-imports`? | Add two global `no-restricted-syntax` AST selectors keyed on `@shared/api-client` as the source: one for `ImportSpecifier[imported.name='ApiError']`, one for `ExportSpecifier[local.name='ApiError']`; exclude only the generated tree. Add negative and positive ESLint fixture tests. | The current test override turns `no-restricted-imports` off (`frontend/eslint.config.js:344-350`). A separate selector remains active in production and test overrides without re-enabling unrelated slice restrictions in fixtures. The second selector closes the re-export route, which would otherwise launder the same class into a slice barrel and pass. |
| Q-4a | Does the rule also catch `import * as c from '@shared/api-client'` followed by `c.ApiError`? | No, and the criteria say so rather than implying otherwise. | Catching it needs either a `MemberExpression[property.name='ApiError']` selector, which fires on every unrelated `.ApiError` including the shared one this dossier is steering people toward, or scope analysis a flat-config `no-restricted-syntax` selector cannot express. The four real sites are named imports, the barrel is not imported as a namespace anywhere in `src/`, and a rule that over-fires on the correct class would teach people to disable it. Recorded as a known gap covered by review, not as coverage the rule does not have. |
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

The generated `ApiError` is not merely the less common of two live classes: it is unreachable.
`core/request.ts:266,280` does throw it, but the generated services call the bare `axios` singleton,
and `transport/axios.ts:225-227` registers the same rejection handler on that singleton as on
`http`, so `parseProblem` converts every failure before `request.ts` can raise. No code path in the
application produces the class these four files test for, which is why no test could go red.

### Provider seed

The capability-table migration introduced a model record but retained one `accepts_sampling` boolean
for multiple independent parameters
(`backend/contexts/agents/domain/model_specs.py:63-71,319-328`). The spec table lives in the
`agents` context and reaches the `keys` adapters as an untyped payload dict, so this change has two
ends. Adapters already special-case seed by omission, while the UI gates only temperature/top-p
(`AgentDetailView.vue:292-305,385-394`) and renders seed unconditionally. A stale Gemini comment and
stale English help then inverted the real current provider support.

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
3. Add `accepts_seed: bool` to `ChatModelSpec`
   (`backend/contexts/agents/domain/model_specs.py:63-71`), conservative resolution, catalogue/API
   output, the `capability_payload` dict that crosses into the `keys` context (`:319-328`) and
   `CapabilityFlags` (`backend/contexts/keys/infrastructure/adapters/base.py:175-220`). Both ends
   move together: a flag added to the spec but not to `capability_payload` reaches no adapter, which
   is the same silent-inertness this dossier exists to remove. Document that `accepts_sampling`
   means only temperature/top-p. Gemini rows are true; OpenAI/Anthropic/unknown are false.
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
- **T-3 lint fixtures** — negative: virtual production and `*.test.ts` named imports of generated
  `ApiError`, plus a `export { ApiError } from '@shared/api-client'` re-export, all fail. Positive:
  `@shared/errors.ApiError` and other generated symbols pass, so the rule cannot be satisfied by
  deleting the shared import instead.
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
  rejected; generated DTO/service imports and shared errors remain legal. The opposite risk is
  accepted deliberately: Q-4a records that namespace-qualified access escapes both selectors, and
  AC-3 is worded to match what the rule enforces rather than what a reader might assume from it.
- **Unexpected Gemini model refusal.** Adapter tests pin shaping, and the conservative table enables
  only catalogued rows. A live-key smoke may be recorded at implementation but is not required for
  deterministic CI.
- **Edit data loss.** Capability clearing runs only on user-initiated changes, never initial reset.
- **Rollback.** Revert UI/catalogue/adapter generation and then error imports/lint rule. Persistence
  columns are unchanged; a stored seed remains inert as before.

## 10. Acceptance Criteria

- [x] AC-1: a shared transport 422 reaches message-anchor recovery, clears the poisoned anchor,
  reconciles cache and retries; the test fails before the fix.
  *Verified red first: `useChatroomMessages.test.ts` failed on the retry-loop and anchor
  assertions with the fixture switched to `@shared/errors.ValidationError` and the production
  import still generated, then green after it.*
- [x] AC-2: a shared transport 404 marks the prompt session expired and produces the intended
  recovery state; the test fails before the fix.
  *Same run: `expect(api.sessionExpired.value).toBe(true)` received `false` before the import fix.*
- [x] AC-3: no production or test file outside `src/shared/api-client/**` names generated `ApiError`
  in an import or a re-export; ESLint rejects both forms in production and test override contexts.
  Namespace-qualified access is out of the rule's reach by Q-4a and is not claimed here.
  *Four sites converted; `pnpm lint` clean. Coverage extends past `src/` to `tests/` and `e2e/`
  after D-2 — the first placement left the fixture tree outside the rule entirely.*
- [x] AC-4: the lint restriction permits other generated API symbols and `@shared/errors.ApiError`,
  and existing slice/store/session boundary fixtures continue passing.
  *Three positive probes in `check:apierror-guard`; `check:boundaries-enforced` still green.*
- [x] AC-5: `ChatModelSpec`, API output and adapter payload flags expose independent
  `accepts_seed`; unknown models resolve false.
- [x] AC-6: catalogued Gemini request bodies forward a configured seed exactly once as
  `generationConfig.seed`; OpenAI Responses and Anthropic bodies omit it.
  *`test_catalogued_*_rows_*_end_to_end` drive the real `capability_fields(resolve_spec(...))`
  through each adapter, so a flag that reached the spec but not the payload dict fails here.*
- [x] AC-7: seed forwarding is independent of temperature/top-p capability; changing one flag does
  not silently govern the other parameter family.
  *Pinned at both ends: `test_gemini_seed_forwarding_is_independent_of_sampling` (seed sent with
  sampling off) and the form's `gates seed independently of temperature and top_p`.*
- [x] AC-8: the Agent form enables seed only for selected models whose row accepts it, preserves a
  legacy stored value on load, clears it on a user switch to unsupported, and displays truthful
  translated help in both locales.
- [x] AC-9: stale comments/help claiming Gemini ignores seed or OpenAI accepts it are gone; help does
  not promise bit-identical determinism.
  *The locale test asserts the disclaimer is present rather than that a word is absent — a rewrite
  dropping the caveat is the regression an absence check would pass.*
- [x] AC-10: OpenAPI/client regeneration and focused backend/frontend tests, full frontend test,
  lint, current typecheck and build pass.
  *Local: `pnpm lint`, `pnpm typecheck`, `pnpm build`, `ruff check`, `ruff format --check`, `mypy`
  all clean; regeneration produces no drift. `pnpm test` reported 1724/1727 with two files failing
  to start a worker ("Timeout waiting for worker to respond") — both pass in isolation, a known
  Windows-host failure mode with no assertion behind it. CI is the authority on the full suites.*

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

- **D-1 (naming).** §7.3 and §13 call the cross-context payload builder
  `capability_payload`. The function is actually
  `contexts.agents.domain.model_specs.capability_fields`. No design consequence; recorded
  so a later reader does not go looking for a second function.

- **D-2 (lint rule placement, widened).** Q-4 specifies "two global `no-restricted-syntax`
  AST selectors". They were first added inside the `src/**` config object, which is what
  "global" reads as in that file. Self-audit found this leaves `frontend/tests/**` and
  `e2e/**` outside the rule, and `npx eslint tests/mocks/handlers.ts` reported *"File
  ignored because no matching configuration was supplied"* — that tree, which holds the
  MSW handlers and render helpers every suite builds on, was linted by nothing at all.
  Since AC-3 promises no test file may name the class, and a fixture constructing the
  unreachable class is the original defect, the rule moved into its own config object
  whose `files` list covers `src/`, `tests/`, `e2e/` and `e2e-csp/`. Side effect worth
  knowing: `frontend/tests/**` is now linted for the first time, by this one rule.

- **D-3 (T-3 as a CI gate script, not a config unit test).** The plan says "a config test
  lints virtual production and test snippets". Implemented instead as
  `frontend/scripts/check-generated-apierror-guard.sh` plus CI job
  `frontend-gate-apierror`, mirroring the existing `check-boundaries-enforced.sh` (gate
  #1b) written for the same reason: a rule can only be certified by running ESLint, and
  once the tree is clean a working selector and a broken one are equally silent. Four
  negative probes (production import, `*.test.ts` import, `tests/` fixture import,
  re-export) and three positive ones (`@shared/errors.ApiError`, another generated symbol,
  a shared re-export).

- **D-4 (a green test rewritten, not deleted).**
  `test_gemini_forwards_top_p_and_ignores_seed` asserted the behaviour this dossier
  changes. It would have stayed green — it passed no `accepts_seed` — while documenting
  the opposite of the new contract, so it was rewritten into a pair of cases (accepting
  row forwards, refusing row omits) rather than left as a misleading passing test.

- **D-5 (AC-1 assertion corrected).** `AgentDetailView.test.ts`'s "renders sampling
  controls" case asserted `agents.form.seedHelp`. Its fixture agent runs an OpenAI model,
  which now correctly renders `agents.form.seedDisabledReason`. The assertion was moved to
  the truthful key; the test's real subject (temperature 0 rendering as "0") is unchanged,
  and it now also witnesses Q-7 preservation of the stored seed on a disabled control.

- **D-6 (one locale key added beyond §7.5).** `seedDisabledReason` in both locales, to say
  *why* the control is disabled — the shape `samplingDisabledReason` and
  `effortDisabledReason` already use. §7.5 said "truthful help" without naming the key.

- **D-7 (row provenance deliberately not advanced).** `accepts_seed` was read against the
  live endpoint contracts on 2026-08-30, but each row's `verified_on` stays at
  `_UNVERIFIED_DATE` (2026-06-01). Moving it would claim that date's freshness for the
  lineup and the effort/sampling/vision columns, which were not re-checked. The
  column's own provenance is recorded in a comment above the table instead.

## 15. Follow-ups

- FU-1: migrate the three hand-rolled clipboard callers to `useClipboard`, fix false feedback and
  add a direct-clipboard lint guard after current ChatroomView work settles (source onboarding FU-12).
- FU-2: split/rename `accepts_sampling` to `accepts_temperature_top_p` only if another consumer needs
  the semantic name; this dossier documents it and avoids churn across generated contracts.
- FU-3: enable strict Vue templates through a staged remediation programme; the current backlog is
  too broad for this runtime fix.
- FU-4: add isolated unit/Playwright typecheck projects after their existing backlogs are classified.
- FU-5: bring `frontend/tests/**` under the full ESLint rule set. D-2 discovered that tree
  was matched by no config at all and put exactly one rule on it; the shared fixtures and
  MSW handlers there deserve the same treatment as `src/`, but the rule set that would
  apply (boundaries, restricted imports, i18n) needs its own triage pass and would have
  buried this task's diff.
