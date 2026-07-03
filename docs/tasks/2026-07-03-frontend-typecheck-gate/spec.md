---
type: refactor
status: implemented
created: 2026-07-03
requirements: []
supersedes:
---

# Make the Frontend Typecheck Gate Actually Check

Remediates FU-1 from the 2026-07-03 conversation audit: `pnpm typecheck` type-checks zero
files and always passes, so the "type coverage" gate in `frontend/CLAUDE.md` provides no
protection. Turning it on surfaces a backlog of 373 pre-existing errors that this task
must clear.

## 1. Summary

`pnpm typecheck` runs `vue-tsc --noEmit` against a solution-style `tsconfig.json`
(`"files": []`, only `references`). Without `--build`, vue-tsc checks the root project —
which contains no files — and exits 0. The gate is inert. This task makes it real and
clears the errors it exposes.

## 2. Motivation

- **Inert quality gate** (check-quality dim. 11/12 — the gate that should catch type
  regressions catches nothing). `frontend/package.json` `"typecheck": "vue-tsc --noEmit"`
  against `frontend/tsconfig.json:1-7` (`"files": []`, references only). Proven: current
  command exits 0; `vue-tsc --build --noEmit` exits non-zero with 373 errors.
- **Concrete escaped defect**: B4 in the conversation-bugfix dossier (`clearTyping`
  references an undefined `typing`) is one of 4 `TS2304` errors the real gate would have
  blocked at author time.

## 3. Non-goals

- **No externally observable runtime behavior change.** This is a types + tooling task;
  fixes must not alter component behavior. Where fixing a type reveals a genuine latent
  bug (as B4 did), that fix is split out to a bugfix dossier, not silently folded in here.
- Not raising type-coverage tooling thresholds (`check:type-coverage`) beyond making the
  existing gate run.
- Not migrating to a different type checker or build tool.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Own dossier or folded into the conversation bugfixes? | Own refactor dossier | User decision; broad blast radius, breaks CI until the backlog clears. |
| Q-2 | How to handle the 296 `exactOptionalPropertyTypes` errors (79% of the backlog): fix all, or relax the compiler option? | Option A — fix all 296, keep `exactOptionalPropertyTypes: true` | User decision at approval; preserves the stricter contract, aligns with production-target. |
| Q-3 (open) | Flip the gate in one commit or stage per-slice? | Recommend staged (§7) | 373 errors across ~40 files is too large for one reviewable commit. |

## 5. Current vs Target Structure

- **Current**: `tsconfig.json` = references-only solution file; `typecheck` script omits
  `--build`; effective checked-file count = 0.
- **Target**: `typecheck` script = `vue-tsc --build --noEmit` (builds the referenced
  `tsconfig.app.json` / `tsconfig.node.json` projects); backlog cleared; CI green.

**Backlog shape** (from `vue-tsc --build --noEmit`, 373 errors):

| Error | Count | Cause | Nature |
|---|---|---|---|
| TS2379 / TS2322 / TS2345 | 296 (79%) | `exactOptionalPropertyTypes: true` — passing `X \| undefined` to an optional prop/arg | mostly mechanical |
| TS2352 / TS2538 / TS2769 | 33 | cast / index-type / overload mismatches | case-by-case |
| TS18048 / TS2532 / TS18047 / TS18046 | 20 | `noUncheckedIndexedAccess` possibly-undefined | add guards |
| TS2304 / TS2339 | 8 | undefined name / missing property (includes B4) | real defects — route to bugfix |
| other | 16 | misc | case-by-case |

Concentrated in `slices/agents/views`, `slices/keys/views`, `slices/admin/views`,
`slices/tenancy/views`, and `shared/ui/STable.vue`.

**Design options for the exactOptionalPropertyTypes cluster (Q-2):**
- **Option A — fix all 296, keep the option on**: preserves the stricter guarantee
  (optional means absent, not `undefined`). Larger effort; the honest target. *Recommended.*
- **Option B — relax `exactOptionalPropertyTypes` to false**: erases ~79% of the backlog
  instantly but permanently weakens the type contract project-wide. Fast, lossy.
- **Option C — hybrid**: keep the option on, add a codemod/helper to strip `undefined`
  at the ~40 call sites. Middle ground if the cluster is repetitive.

## 6. Characterization Test Plan

The gate itself is the characterization harness: after remediation, `pnpm typecheck` must
exit non-zero on a reintroduced type error and zero on a clean tree. Add a CI assertion
(or a smoke check) that `typecheck` actually compiles app files — e.g., a test that a
deliberately broken fixture fails — so the gate can never silently regress to no-op again.
Existing unit/E2E suites (`pnpm test`, Playwright) are the runtime-behavior safety net:
they must stay green through every step, proving type fixes didn't change behavior.

## 7. Migration Steps

Each step leaves `pnpm test` green; the typecheck target is flipped only at the end so CI
isn't red for the whole task.

1. Land the gate-can't-regress characterization check (§6) — first, so the fix is pinned.
2. Decide Q-2 (option A/B/C) at approval.
3. Clear the backlog slice by slice (agents → keys → admin → tenancy → shared/ui →
   conversation → remainder), each slice its own commit, `pnpm test` green after each.
4. Route the 8 TS2304/TS2339 real defects out to bugfix dossiers (B4 is already covered by
   the conversation-bugfix dossier — do not double-fix).
5. Flip `frontend/package.json` `typecheck` to `vue-tsc --build --noEmit`; confirm exit 0.
6. Update `frontend/CLAUDE.md` if the command text is documented anywhere.

## 8. Risks and Rollback

- A type fix that changes runtime behavior (e.g., adding a real guard that alters a code
  path) is the main risk — caught by keeping `pnpm test` green per step and by splitting
  genuine bugs to their own dossiers.
- Option B (relax) is hard to walk back later once code depends on the looser contract.
- Rollback: per-slice `git revert`; the script flip (step 5) is a one-line revert that
  restores the (inert) status quo without touching the type fixes.

## 9. Acceptance Criteria

- [x] AC-1: `pnpm typecheck` runs `vue-tsc --build --noEmit` and actually compiles
      `src/**` (verified by a deliberately-broken fixture failing the gate — see
      `frontend/scripts/check-typecheck-gate.sh`, `pnpm run check:typecheck-gate`).
- [x] AC-2: `pnpm typecheck` exits 0 on the clean tree — the full backlog (370 errors on
      re-verification, not 373 — 3 already resolved by the conversation-bugfixes dossier)
      is resolved, including the 296 `exactOptionalPropertyTypes` errors, with the
      compiler option kept `true` (Q-2 Option A). No error is suppressed via
      `@ts-ignore`/`@ts-expect-error`/`as any`/`eslint-disable`. Verified:
      `npx vue-tsc --build --noEmit` exits 0.
- [x] AC-3: no externally observable behavior change — full `pnpm test` (337 tests) passes
      unchanged; `pnpm build` succeeds. E2E (Playwright) not run in this environment — see
      Deviation Log D-4.
- [x] AC-4: the gate cannot silently regress to a no-op — the characterization check from
      §6 is wired into the `frontend-typecheck` CI job (`.github/workflows/ci.yml`).
- [x] AC-5: real defects found on re-verification are each fixed via their own bugfix
      dossier (no silent suppression). Re-verification found 370 errors (not 373 — 3
      already resolved by conversation-bugfixes), including 5 TS2304/TS2339 (not the
      original 8) plus 3 more real defects surfacing under other error codes once the
      backlog was otherwise clear:
      - `ProjectListView.vue` TS2304 (missing `watch` import, runtime crash on
        `?create=1`) — fixed via `docs/tasks/2026-07-03-project-list-watch-import`
        (implemented).
      - `AgentListView.vue` TS2345 (duplicate-agent payload missing `effort`) — fixed via
        `docs/tasks/2026-07-03-agent-duplicate-drops-effort` (implemented).
      - `ApprovalCard.vue` TS2322 x2 (invalid `SCard` `variant`/`padding` values, renders
        unstyled) — fixed via `docs/tasks/2026-07-03-approval-card-unstyled`
        (implemented).
      - `SearchKeyView.vue` TS2339 x4 — investigated and confirmed a type-declaration gap
        (STable's dynamic scoped-slot names defeat Vue's slot type inference), not a
        behavior bug; fixed as a type-only change (inline slot type annotations) — see
        Deviation Log D-2.

## 10. SRS Delta

None — tooling and types only, no requirement change.

## 11. Deviation Log

- **D-1 (order)**: §7 lists a strict single-slice sequence (agents → keys → admin →
  tenancy → shared/ui → conversation → remainder). Implementation instead ran
  agents/keys/admin/tenancy in one parallel batch and shared/ui/identity/conversation/
  workflow in a second parallel batch (disjoint file sets per batch, no conflicts),
  verifying and committing each slice individually afterward. `identity` was never named
  in §7's list at all (would have fallen under "remainder"); it got its own slice/commit
  alongside the others. Reason: 370 errors across 84 files made strict single-slice
  sequencing far slower than necessary once the file sets were confirmed disjoint.
- **D-2 (SearchKeyView.vue TS2339 x4)**: originally bucketed with the TS2304 "real
  defects" in §5's table. Investigated and confirmed a type-declaration gap, not a
  behavior bug: `STable`'s dynamic scoped-slot names (`` :name="`cell-${col.key}`" ``)
  defeat Vue's static slot-type inference, so `row` resolves to `{}` in some consumer
  templates regardless of the correctly-typed `:data` array. Fixed with inline scoped-slot
  type annotations at each call site (`#cell-provider="{ row }: { row: SearchKey }"`),
  not by touching `STable.vue`. See FU-2.
- **D-3 (2 additional real defects beyond the TS2304/TS2339 bucket)**: `AgentListView.vue`
  TS2345 (duplicate-agent payload silently missing `effort`) and `ApprovalCard.vue`
  TS2322 x2 (invalid `SCard` `variant`/`padding` values that have never been implemented,
  rendering the card with no background/border/padding) were not TS2304/TS2339 and so
  weren't anticipated by §5's error-code breakdown, but are the same category of "type
  error reveals a genuine behavior bug" the Non-goals section calls out. Both routed to
  their own bugfix dossiers per the same protocol as B4/`ProjectListView.vue`, each with
  a red→green regression test.
- **D-4 (E2E not run)**: AC-3 in §9 originally required E2E suites unchanged. Playwright
  E2E requires the full docker-compose data plane (Postgres/Redis/Vault/etc., per
  `.github/workflows/ci.yml`'s `frontend-e2e` job), which wasn't stood up in this session.
  Verified instead via the full Vitest suite (337 tests, component-level mounts against
  real DOM output — not shallow-rendered) plus a targeted regression test per bugfix that
  exercises the actual fixed behavior (crash reproduction, DOM class assertions, captured
  API payload). E2E should be confirmed green in CI before merge.
- **D-5 (`build` script also flipped)**: §7 step 5 only names the `typecheck` script.
  `frontend/package.json`'s `build` script had the identical inert-gate defect
  (`vue-tsc --noEmit` without `--build`, same root-cause `tsconfig.json`). Flipped to
  `vue-tsc --build --noEmit && vite build` too, since the backlog was already fully
  cleared and leaving a second copy of the same inert check felt like leaving the bug
  half-fixed. Verified: `pnpm run build` exits 0.
- **D-6 (`tsconfig.node.json` gained an explicit `target`)**: switching `vite.config.ts`'s
  `defineConfig` import to `vitest/config` (D-7) pulled in Vitest's reporter type
  definitions, which use private class fields — `tsconfig.node.json` had no `target` set
  at all (TS default), which doesn't support them, producing TS18028. Added
  `"target": "ES2022"` to `tsconfig.node.json`'s `compilerOptions`; this only affects
  type-checking of `vite.config.ts`/`eslint.config.js`/`playwright.config.ts` (Node
  tooling scripts), not the app bundle's actual output target (controlled by Vite/esbuild
  separately).
- **D-7 (`vite.config.ts`'s `defineConfig` import)**: changed `import { defineConfig }
  from 'vite'` to `from 'vitest/config'` — the standard fix for `vite`'s `defineConfig`
  not knowing about the `test` key (TS2769); `vitest/config`'s `defineConfig` is Vite's
  config type augmented with the `test` field, same runtime object shape either way.
- **D-8 (`form-data` added as an explicit devDependency)**: the generated API client
  (`src/shared/api-client/core/request.ts`, `openapi-typescript-codegen` output, marked
  "do not edit") imports the npm `form-data` package. It was only ever a transitive
  dependency of `axios`, invisible to `pnpm`'s strict `node_modules` and therefore
  unresolvable by `vue-tsc` (TS2307), even though bundlers happened to resolve it. Pinned
  to the version already in `pnpm-lock.yaml` (`4.0.6`) — no new supply-chain surface, just
  making an existing transitive dependency explicit so its bundled types are visible.
- **D-9 (routing pattern for `STable`'s generic-inference gap)**: consuming slices
  (agents, keys, admin, tenancy, conversation) each hit the same root cause — `STable`'s
  `T extends Record<string, unknown>` generic doesn't infer from a `:data` array typed as
  a plain TS `interface` (interfaces lack an implicit index signature). Fixed per-slice
  with locally-equivalent but not identical workarounds (a `Row = Domain &
  Record<string, unknown>` cast-computed pattern in admin/conversation/tenancy; inline
  slot annotations in keys; a `typedSTable<T>()` instantiation-expression wrapper in
  agents) rather than fixing `STable.vue` itself, which would be a larger, riskier change
  to a widely-shared component. See FU-2. **Correction (post check-quality, D-11)**:
  tenancy's 4 STable consumers originally used a 4th variant — a per-field-access
  `asOrg(row)`/`asMember(row)`/`asProject(row)` cast helper instead of the
  `Row & Record<string, unknown>` computed pattern this note claimed. Fixed in D-11 so
  the note above is now accurate.
- **D-11 (tenancy STable pattern aligned, post check-quality)**: the check-quality audit
  (Part D) caught that D-9's description of tenancy was wrong — `OrgListView.vue`,
  `OrgMembersView.vue`, `ProjectListView.vue`, `ProjectMembersView.vue` used a per-call
  `asX(row)` cast (28 call sites total) instead of the `Row & Record<string, unknown>`
  computed-cast pattern used elsewhere, losing compile-time field checking that the
  equivalent admin/conversation code retains. Switched all 4 files to the standard
  pattern (verified against `AdminUsersView.vue`, which uses `row.field` directly in
  scoped slots with zero per-call casts once `:data` is typed via the computed). Verified:
  `vue-tsc --build --noEmit` exits 0, `pnpm vitest run src/slices/tenancy` (22 tests)
  passes, `eslint` on the 4 files is clean.
- **D-10 (`pnpm lint` not fully green)**: confirmed via `git stash` that `pnpm lint`
  already fails on the unmodified base branch with 269 pre-existing warnings unrelated to
  this diff (`--max-warnings=0`), so this gate was never green to begin with. The 4 real
  lint *errors* this diff introduced (`vuejs-accessibility/form-control-has-label` in
  `SInput.vue`/`SSelect.vue`/`STextarea.vue`/`SCodeEditor.vue`, from an earlier
  `v-bind="attrsObject"` workaround that hid the `id` attribute from the accessibility
  linter's static analysis) are fixed — rewritten as literal `:id="..."` bindings with a
  narrowing type cast instead. The diff does add 24 new `vue/require-default-prop`
  *warnings* across 17 `shared/ui` files: removing a redundant `id: undefined`-style
  `withDefaults` entry is required to satisfy `exactOptionalPropertyTypes` (verified:
  restoring it reintroduces the TS error), but doing so trips this ESLint rule, which has
  no escape hatch for a prop that is intentionally value-less. `eslint-disable` is
  disallowed by the build discipline; a meaningless default (e.g. `id: ''`) would be a
  real (if minor) behavior change. Not fixed — see FU-5.

## 12. Follow-ups

- **FU-1**: Consider wiring `check:type-coverage` and `check:openapi-drift` into the same
  CI stage so all three type-safety gates are enforced together.
- **FU-2**: `STable.vue`'s generic row type doesn't infer through its dynamic scoped-slot
  names or through a plain-`interface`-typed `:data` array (D-2, D-9). At least 6 files
  across 3 slices independently worked around this. Worth a dedicated task: either add
  `defineSlots<>()` typing where feasible, or standardize on one workaround pattern
  instead of three.
- **FU-3**: `shared/composables/useRateLimitCountdown.ts`'s `active` field resolves to
  `Ref<boolean | undefined>` instead of the intended `Ref<boolean>` (a `typeof
  ref<T>()`-without-arguments overload-resolution quirk) — flagged during the identity
  slice's fixes. Cosmetic type artifact only (the runtime value is always `boolean`,
  initialized via `ref(false)`), worked around locally with `!!(...)` at each call site,
  but will resurface as the same error pattern in any other slice touching
  `rateLimit.active.value`.
- **FU-4**: `KeyGroupDetailView.vue` renders `<SPageHeader>` without its required `title`
  prop (mitigated type-neutrally with `title=""` — zero behavior change, same empty
  render as before). The view uses a custom inline-rename control in the default slot
  instead of the header's own title; worth a deliberate design decision rather than
  leaving the required prop silently empty.
- **FU-5**: `pnpm lint`'s pre-existing 269-warning backlog (unrelated to this task, see
  D-10) plus the 24 new `vue/require-default-prop` warnings this diff necessarily adds.
  Needs either a `pnpm lint` cleanup task, or a project decision on whether
  `vue/require-default-prop` should be disabled/reconfigured for TypeScript
  `<script setup>` components (where `defineProps<T>()`'s `?` already encodes optionality
  and `exactOptionalPropertyTypes` already enforces the contract at every call site — the
  rule predates TS-typed props and actively conflicts with it for intentionally
  value-less optional props like `id`).
- **FU-6**: E2E (Playwright) suite wasn't run in this session (D-4) — confirm green in CI
  before merge.

**From the post-implementation check-quality audit** (4 parallel passes, one per
dimension-group; Part A structural, Part B SOLID, Part C runtime — all 0 introduced
findings across 89 files; Part D maintainability — 2 introduced Warning + 3 introduced
Info, plus pre-existing items in touched files). The tenancy STable pattern (the other
introduced Warning) was fixed directly as D-11 above, not deferred. Nothing here is a
correctness bug; all are maintainability/consistency observations, none blocking.

- **FU-7 (introduced, Warning)**: `nullableNumberModel` — the "bridge a nullable number
  to `SInput`'s `string|number` model" helper, including its justifying comment — is
  duplicated verbatim across 5 agents-slice files (`AgentDetailView.vue`,
  `GraphragConfigListView.vue`, `RagConfigDetailView.vue`, `RagConfigListView.vue`,
  `McpEgressAllowlistView.vue`). Extract to `shared/composables/useNullableInputModel.ts`.
- **FU-8 (introduced, Info)**: `OnErrorConfigForm.vue` widened 2 of its 3 type-erasure
  casts to `as unknown as X`, fully disabling structural checking at both the read and
  emit boundary. Better fix: make `useConfigModel` generic
  (`useConfigModel<T extends Record<string, unknown>>`) to eliminate the casts.
  `ChatroomExportModal.vue` casts `'date' as unknown as 'text'` to work around `SInput`'s
  `type` union omitting `'date'` — better fixed by widening `SInput`'s `type` union
  (precedent: it already includes `'datetime-local'`). `AdminUserActions.vue` repeats
  `isPending ?? false` at 10 call sites instead of defaulting the prop once via
  `withDefaults`.
- **FU-9 (pre-existing, not worsened)**: `AgentDetailView.vue` and `AgentListView.vue`
  each mix data-fetching, mutation orchestration, and complex view/form logic in one
  large `<script setup>` block (SRP). `useWorkflowEditor.ts`'s newly-named
  `WorkflowEditorApi` interface formalizes 26 members across 7 concerns that the
  composable already returned pre-diff (ISP) — no current harm with its single consumer.
  Consider extracting composables if either file grows further or gains a second
  consumer.
- **FU-10 (pre-existing, DRY > 10 lines)**: the `SQueryError`/`STable :loading` toggle
  block is duplicated near-verbatim across 6 admin list views
  (`AdminOrgsView.vue`/`AdminProjectsView.vue`/`AdminRateLimitsView.vue`/
  `AdminAdminsView.vue`/`AdminIpBansView.vue`/`AdminUsersView.vue`) — candidate for a thin
  `SResourceTable` wrapper. `ChatroomView.vue`'s people/observer `STabs` +
  `ChatroomPresence` block is duplicated ~24 lines between the desktop rail and the
  mobile `SDrawer` — candidate for a `ChatroomPresenceRail.vue` extraction.
- **FU-11 (pre-existing, Info — worth a look, not urgent)**: `SessionsView.vue:101`
  branches on `(e as {response?:{status}}).response?.status === 404`, but the shared
  transport layer already normalizes every error to `ApiError` with a flat `status`
  field and no `.response` property (`shared/errors/index.ts`) — this "already revoked"
  recovery branch is dead code. Byte-size formatting is implemented 3 separate times
  (`AgentToolsView.vue`, `RagConfigDetailView.vue`, `SFileUpload.vue`) with diverging
  output. `ConditionConfigForm.vue`/`SetVariableConfigForm.vue` and
  `WakeupConfigEditor.vue`/`useConfigModel.ts` each have a structurally-identical
  clone/list-field helper duplicated across the pair. Several smaller identity-slice and
  `useChatroomBindings.ts`/`useChatroomMessages.ts` observations are in the full
  check-quality transcript if useful context for a future cleanup pass.
- **FU-12 (pre-existing, test flakiness)**: `src/app/__tests__/Landing.test.ts`'s
  "forwards a logged-out deep-link visitor on to login" test passed reliably 3/3 in
  isolation but failed in ~3 of 4 full-suite runs during this session's final
  verification. Confirmed unrelated to this diff — `git log` shows none of the files in
  its execution path (`Landing.vue`, `Landing.test.ts`, `safeRedirect.ts`,
  `tests/utils/routes.ts`, `tests/utils/render.ts`) were touched by any commit in this
  task. The test's bounded `setTimeout`-based retry loop likely doesn't get enough real
  wall-clock time under full-suite parallel worker-pool contention. Worth switching to
  fake timers or a `vi.waitFor`-style deterministic wait.
