---
name: build
description: Implement a change end-to-end with full engineering discipline — from an approved task dossier (docs/tasks/) or a direct request. Covers freshness re-verification, test-first bugfixes, characterization-first refactors, a complete Definition of Done (tests, lint, typecheck, build, check-quality, conditional check-security, self-audit), and write-back of deviations into the dossier. Use whenever the user asks to implement, fix, refactor, resume, or finish a change — "implement docs/tasks/...", "fix this bug", "build the feature we specced", "continue the task from yesterday" — even if they don't mention a spec.
---

## Purpose

Take a task from "decided" to "verified and committed" without the shortcuts that turn
into next month's bugs. The dossier contract in `docs/tasks/README.md` governs
everything this skill reads and writes — read it first.

## Step 1 — Route the input

**Given a dossier** (path or slug under `docs/tasks/`): load `spec.md` and check status.

- `approved` — check `depends_on` first: every listed slug must be `status: implemented`
  in its own `spec.md` (don't trust `BOARD.md` alone for this gate — it's a cache; read
  the dependency's frontmatter directly). If any dependency isn't implemented yet, refuse
  and name the blocker instead of proceeding — starting anyway risks building on code or
  assumptions that don't exist yet. Otherwise proceed.
- `in-progress` — this is a resume: unchecked ACs and the Deviation Log tell you where
  the previous session stopped. Verify the working tree state against checked ACs before
  continuing; a checked AC whose test now fails means the checkpoint is stale.
- `draft` — refuse to implement and say why: the approval gate exists so that analysis
  gets human sign-off before code exists. Offer to walk the user through approval via
  `/spec` Step 6.

**Given a verbal request** (no dossier): triage by footprint. Recommend running `/spec`
first when any of these hold — the analysis will pay for itself:

- likely touches 3+ files, or crosses a context/slice boundary
- requires a DB migration or changes an API contract
- adds a dependency or touches auth / provider keys / tenant boundaries

Otherwise clarify the goal and acceptance verbally (what must be true when done, what's
out of scope) and proceed without a dossier — small fixes don't need ceremony, they need
the same verification discipline (Step 4 applies in full either way).

## Step 2 — Verify freshness

A spec is a snapshot; the codebase may have moved since it was written. Before writing
code, spot-check the spec's `path:line` citations and key claims. Trivial drift (line
numbers shifted) — proceed. Material drift (the cited code changed behavior, a
referenced helper is gone, the design's assumption no longer holds) — stop and report
the difference before implementing. Building on a stale spec produces confident,
well-tested, wrong code.

## Step 3 — Plan

Derive the work breakdown from the ACs — every AC maps to at least one step, every step
serves some AC. Order steps so the tree stays green between them where possible.

**High-risk tasks pause here for plan approval**: anything involving a migration, an API
contract change, cross-context changes, or auth/keys/tenant surfaces. Present the
breakdown and wait. Everything else: proceed directly — spec approval already authorized
the what, and the how is this skill's job.

Set the dossier `status: in-progress` when implementation starts, and move its row in
`docs/tasks/BOARD.md` to In progress.

**Record the base commit** — `git rev-parse HEAD` before the first edit — and keep it for
Step 5. This skill commits at milestones, so by the time the audit gates run the working
tree is clean and `HEAD~1` covers only the last milestone. Without the base ref, every
gate in Step 5 silently audits a fraction of the task's diff.

## Step 4 — Implement

Type-specific discipline:

- **bugfix** — write the regression test from the spec's Regression Test Plan first and
  run it: it must fail for the documented reason before you touch the fix. Then fix,
  then run the **sibling sweep**: search for the same defect pattern elsewhere (the
  spec's Sibling Suspects section seeds this; grep beyond it). Confirmed siblings in
  scope get fixed with their own regression tests; out-of-scope ones become FU-n
  entries.
- **refactor** — write the missing characterization tests first per the spec's plan;
  only then move code, step by step, keeping the tree green after each step.
- **feature** — follow the spec's Detailed Changes and reuse inventory; check the reuse
  list before writing any helper, composable, or component.

Throughout: respect the layer boundaries in CLAUDE.md, all user-facing strings through
`$t()`, no secrets in code or logs.

### When verification fails — hard rules

The failure path is where discipline matters most:

- Never weaken an assertion, delete a test, or widen a tolerance to make a test pass.
  The test encodes an AC; if you believe the test is wrong, say so and stop.
- Never silence the type checker (`# type: ignore`, `as any`) or linter to get past a
  gate. Fix the cause or report it.
- Never skip hooks (`--no-verify`).
- Two consecutive failed fix attempts on the same AC means your model of the problem is
  wrong. Stop, write down what was tried and what the evidence shows, and report —
  don't spiral into increasingly speculative edits.

## Step 5 — Definition of Done

All gates, in order. A gate that doesn't apply gets stated as N/A with a reason, not
silently skipped.

Gates 5-7 audit **the whole task diff**, not the last commit: pass the Step 3 base commit
to each of them (`git diff --name-only <base>...HEAD` plus anything still uncommitted).
Both audit skills accept an explicit scope; give it to them rather than letting their
default scope detection run.

1. **Mechanical gates** — backend: `pytest -q`, `ruff check . && ruff format --check .`,
   `mypy .`; frontend: `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` —
   for whichever sides the diff touches.
2. **Contract gates** — migration applied (`alembic upgrade head`) and its downgrade
   path sanity-checked; `pnpm run gen:api` rerun if the API contract changed; new i18n
   keys present in all locale files.
3. **AC verification** — check off each AC in the dossier only when its mapped test (or
   documented manual check) passes. Unchecked ACs block Step 6.
4. **Behavioral verification** — if user-visible behavior changed, use the `run` skill to
   launch the app and observe the behavior; unit tests passing is not the same as the
   feature working.
5. **Quality audit** — run the `check-quality` skill on the task diff. Introduced-Critical
   findings must be fixed; Introduced-Warning findings fixed or explicitly deferred as
   FU-n with the user's knowledge; Pre-existing findings route to FU-n and never block.
6. **Security audit (conditional)** — run `check-security` on the task diff when it
   touches auth, provider keys, tenant boundaries, WebSocket, file upload, user-input
   processing, agent/LLM prompt or tool surfaces, dependency manifests, or deploy
   configs. Its verdict rules differ from quality's — do not carry gate 5's policy over:
   **CRITICAL blocks regardless of Introduced or Pre-existing** (a vulnerability does not
   age into acceptability); HIGH is fixed or deferred as FU-n only with the user's
   explicit agreement; MEDIUM and Hardening route to FU-n. A `plausible` verdict is
   treated at its stated severity, not discounted for being unconfirmed.
7. **Self-audit** — re-read the complete task diff end-to-end with fresh eyes, hunting for
   your own bugs: unhandled error paths, reactivity pitfalls, boundary conditions,
   leftover debug code. You are the last reviewer before the user.

## Step 6 — Close out

1. Append **D-n** entries for every deviation from the approved spec, with reasons. If
   implementation revealed the spec is infeasible or wrong, that is a stop-and-report
   in Step 4 — never silently redesign; the deviation log records agreed changes, not
   unilateral ones.
2. Append **FU-n** entries for out-of-scope discoveries. Do not fix them in this task
   unless they block an AC.
3. Set `status: implemented`.
4. Remove this dossier's row from `docs/tasks/BOARD.md`'s active sections (it's no longer
   Ready/Blocked/In progress), then check whether any Blocked row listed this slug in its
   `depends_on` — if so and all of *that* row's other dependencies are also implemented,
   move it to Ready and tell the user it just became unblocked. This is the moment a
   sequencing change would otherwise go unnoticed.
5. Commit following the CLAUDE.md commit discipline: commit at each completed milestone
   rather than one lump at the end (a fix and its test as separate commits, each
   migration, each refactor stage), English messages, no co-author trailer, and stage
   only the files this task changed plus `BOARD.md` — never `git add -A`/`.`/`-a`, since
   the tree may hold unrelated in-progress work. Do not push without explicit user
   confirmation.
