---
type: audit
status: draft
created: YYYY-MM-DD
requirements: []
---

# Audit: <Area>

## 1. Scope

- **Area** — contexts, slices, or flows examined.
- **Intent sources** — what defined correct behavior for this audit: `[Rxx.yy]` entries,
  approved spec dossiers, schema docs. If sources were thin, say so; an audit without
  intent sources can only find internal inconsistencies.
- **Depth** — quick sweep or thorough; number of investigation lenses and verification
  rounds actually run.

## 2. Coverage

What was read and what was not. Areas skipped, files sampled rather than read in full,
lenses not applied. A findings list without coverage boundaries reads as "everything else
is clean" when it isn't.

## 3. Findings

Ordered by severity. Never renumber — F-n identifiers are cited from spec dossiers.

## F-1: <one-line defect statement>

- **Severity**: critical | major | minor
- **Verdict**: confirmed | plausible
- **Evidence**: path:line, ...
- **Failure scenario**: concrete inputs/state → wrong outcome
- **Blast radius**: what/who is affected
- **Intent source**: [Rxx.yy] or spec dossier the behavior violates

## 4. Refuted Candidates

Candidates that did not survive adversarial verification, one line each, kept only where
the refutation is itself informative (a guard that is easy to miss, a test that already
pins the behavior). Prevents the same false positive being re-reported next audit.

## 5. Hand-off

Per the dossier contract, this section links the task slugs this audit spawned. A finding
with no dossier and no explicit decision to skip it is an unfinished triage.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix / defer / won't fix | `docs/tasks/YYYY-MM-DD-<slug>/` |

## 6. Out-of-scope Observations

FU-n entries for things this skill deliberately does not judge: structural quality
(route to `check-quality`), vulnerabilities (`check-security`), diff-level review
(`/code-review`).
