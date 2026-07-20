---
name: check-quality
description: Professional-grade code quality audit — 12 dimensions covering structural integrity, SOLID principles, runtime safety, and maintainability. Use when finishing a feature, before committing, as the quality gate in /build's Definition of Done, or whenever the user asks to review code quality, check for code smells, verify architecture/layer boundaries, or asks "is this code clean" about recent changes.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree
is clean) for code quality issues across 12 dimensions. Produce a single structured
report of verified findings.

This skill is report-only — it changes no files and makes no commits. When it runs as a
gate inside `/build`, that skill owns the commits per the CLAUDE.md commit discipline.

The 12 dimensions live in `references/dimensions.md` — read it in full before auditing.
The rules below govern how findings from it are judged and reported, and they outrank any
individual dimension.

## Ground Rules

These four rules control the signal-to-noise ratio of the whole audit.

1. **Verify before reporting.** Every finding cites `path:line` and survives an attempt
   to refute it: read the actual import, trace the actual chain, check whether a guard
   or abstraction you missed makes the code correct. A pattern that merely looks like a
   violation is not reportable — false positives train readers to ignore the report.
2. **Classify Introduced vs Pre-existing.** An issue caused by this change is
   *Introduced* and gates the commit. An issue that was already present in touched code
   is *Pre-existing*: report it in its own section so it can route to a follow-up
   (FU-n in the task dossier, if one exists) instead of blocking today's work. When
   this change makes a pre-existing problem worse, that worsening is Introduced.
3. **Don't duplicate the mechanical toolchain.** ruff, mypy, eslint, and vue-tsc already
   catch unused imports, formatting, and obvious type gaps deterministically — assume
   they run and skip hand-auditing what they cover. Do flag what silences them
   (`# type: ignore` without justification, `as any`, eslint-disable) and what they
   cannot see: architecture, semantics, resource lifecycles, duplicated intent.
4. **Numeric thresholds are calibration points, not tripwires.** A 55-line function
   with one linear responsibility can be fine; a 30-line function mixing validation,
   persistence, and notification is not. When a threshold triggers, judge whether the
   underlying design problem is actually present, and report the problem — not the
   number.

## Scope Detection

0. **Explicit scope wins.** If the caller supplied a file list or a base ref, use it —
   `git diff --name-only <base>...HEAD` plus `git status --porcelain` for anything still
   uncommitted. `/build` passes its task base commit here because it commits at
   milestones; the default detection below would then see only the final milestone and
   report a clean bill of health for a diff it never read. State the resolved scope in
   the report either way.
1. Otherwise collect changed files: `git status --porcelain` (captures staged, unstaged,
   AND untracked files — new files are the most common place quality issues hide). If the
   tree is clean, use `git diff --name-only HEAD~1 HEAD` — and say so in the report, since
   that window is one commit wide and may under-cover multi-commit work.
2. Filter to `.py`, `.ts`, `.vue`. Exclude deleted files and generated code (the
   generated api-client under `frontend/src/shared/`, alembic version stubs' boilerplate
   — though migration *content* is in scope for dimension 10).
3. Read each changed file in full; for each, read the direct imports needed to verify
   dependency direction and reuse claims.
4. **Large scope** (more than ~10 changed files): fan out subagents — one per Part
   (A–D of `references/dimensions.md`), or one per area for very wide changes — then
   merge, dedupe, and apply Ground Rule 1 to the merged set in the main context.

## Dimensions

Read `references/dimensions.md`:

- **Part A — Structural Integrity**: 1 upward dependency, 2 circular dependency,
  3 abstraction leak, 4 separation of concerns.
- **Part B — SOLID**: 5 single responsibility, 6 open/closed, 7 dependency inversion,
  8 interface segregation.
- **Part C — Runtime Quality**: 9 side effects and mutability, 10 resource management and
  persistence consistency, 11 error handling quality.
- **Part D — Maintainability**: 12 code hygiene (DRY, complexity, type safety, dead code,
  API consistency).

## Output Format

```markdown
## Code Quality Report

**Scope:** N files checked (list files), resolved from <base ref / working tree / HEAD~1>.
Not covered: <excluded/generated/skipped, if any>

### Introduced by this change

#### Critical (must fix before commit)
- [Upward Dep] file:line — `infrastructure/foo.py` imports from `app/api/v1/bar.py` (lower layer depends on upper). Fix: invert via interface in application layer.

#### Warning (fix, or defer explicitly)
- [SRP] file:line — `FooService` mixes key validation and usage metering. Fix: extract metering into its own service.

#### Info (consider improving)
- [Complexity] file:line — `process_data` nests 5 levels deep. Fix: early returns.

### Pre-existing in touched code
- [Abstraction Leak] file:line — ORM model returned from facade (predates this change). Route to follow-up.

### Summary
| Dimension | Critical | Warning | Info |
|-----------|----------|---------|------|
| Structural (1-4) | 0 | 0 | 0 |
| SOLID (5-8) | 0 | 0 | 0 |
| Runtime (9-11) | 0 | 0 | 0 |
| Maintainability (12) | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** |
```

Every finding carries a one-clause fix direction — a finding without a direction forces
the reader to redo the analysis.

**Clean result:** if no findings survive verification, say so explicitly and state what
was checked — "12 dimensions over N files, no verified findings" is a meaningful result;
an empty report is not.

**Severity rules:**
- **Critical**: upward dependency, circular dependency, abstraction leak across API boundary, silently swallowed security-relevant errors, ORM/migration type mismatch.
- **Warning**: SRP/OCP/DIP violations, missing error handling, side effects, resource leaks, DRY violations > 10 lines.
- **Info**: complexity, dead code, type safety gaps, API inconsistency, minor DRY.

Consumers: `/build`'s Definition of Done treats Introduced-Critical as blocking and
Introduced-Warning as fix-or-defer-as-FU-n; Pre-existing findings inform but never block.
`/spec`'s quality lens reads `references/dimensions.md` as a checklist when analyzing an
area it is about to change.
