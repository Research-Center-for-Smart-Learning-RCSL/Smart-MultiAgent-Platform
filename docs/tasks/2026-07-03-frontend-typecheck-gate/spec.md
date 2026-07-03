---
type: refactor
status: approved
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

- [ ] AC-1: `pnpm typecheck` runs `vue-tsc --build --noEmit` and actually compiles
      `src/**` (verified by a deliberately-broken fixture failing the gate).
- [ ] AC-2: `pnpm typecheck` exits 0 on the clean tree — all 373 backlog errors resolved,
      including the 296 `exactOptionalPropertyTypes` errors, with the compiler option kept
      `true` (Q-2 Option A). No error is suppressed via `@ts-ignore`/`as any`.
- [ ] AC-3: no externally observable behavior change — full `pnpm test` and E2E suites
      pass unchanged.
- [ ] AC-4: the gate cannot silently regress to a no-op — the characterization check from
      §6 is in CI.
- [ ] AC-5: the 8 real TS2304/TS2339 defects are each fixed or have a linked bugfix
      dossier (no silent suppression via `@ts-ignore`/`as any`).

## 10. SRS Delta

None — tooling and types only, no requirement change.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- Consider wiring `check:type-coverage` and `check:openapi-drift` into the same CI stage
  so all three type-safety gates are enforced together.
