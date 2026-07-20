---
type: bugfix
status: draft
created: YYYY-MM-DD
requirements: []
depends_on: []
---

# <Title>

## 1. Summary

One paragraph: the defect and its user-visible impact.

## 2. Observed vs Expected

- **Observed** — what actually happens, with evidence (`path:line`, logs, screenshots).
- **Expected** — what should happen, citing the intent source: `[Rxx.yy]`, an approved
  spec dossier, or documented behavior. If no intent source exists, the expected
  behavior must be confirmed with the user in §3 — a fix without an agreed "expected"
  is a guess.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | ... | ... | ... |

## 4. Reproduction

Minimal, deterministic steps. Preconditions (data, roles, tenancy setup) included.
If not reliably reproducible, document the closest attempt and the hypothesis for
nondeterminism (timing, ordering, concurrency).

## 5. Root Cause Analysis

The causal chain from trigger to symptom, each link cited with `path:line`. The root
cause is the earliest link whose correction prevents the symptom — name it explicitly.
Distinguish root cause from aggravating factors.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — what else the defect affects (other endpoints, tenants, data
  already written).
- **Sibling suspects** — other places the same defect pattern plausibly exists, each
  checked and marked confirmed / cleared, with evidence. A fix that patches one
  instance of a systemic mistake is half a fix.

## 7. Fix Design

The change that corrects the root cause, and why it does not merely mask the symptom.
Data repair plan if bad data was already persisted.

## 8. Regression Test Plan

The failing test comes first: which test file, what it asserts, and why it fails
against current code. /build implements this test before touching the fix.

## 9. Risks and Rollback

What could the fix break; rollback path.

## 10. Acceptance Criteria

- [ ] AC-1: the regression test from §8 fails before the fix and passes after.
- [ ] AC-2: ...

## 11. SRS Delta

Usually "None" — a bugfix restores documented behavior. If analysis revealed the SRS
itself is wrong or ambiguous, draft the correction here.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

Out-of-scope discoveries (FU-n), including cleared-but-fragile sibling sites worth
hardening later.
