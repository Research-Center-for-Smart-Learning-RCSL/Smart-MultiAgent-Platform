---
type: refactor
status: draft
created: YYYY-MM-DD
requirements: []
depends_on: []
---

# <Title>

## 1. Summary

One paragraph: what structure changes and why now.

## 2. Motivation

The specific debt being paid, named by check-quality dimension (e.g., upward dependency,
SRP violation, abstraction leak), with `path:line` evidence. "The code is messy" is not
a motivation; a cited violation is.

## 3. Non-goals

Always includes: **no externally observable behavior change** — no API contract change,
no schema change, no user-visible difference. Plus any structural work explicitly
deferred.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | ... | ... | ... |

## 5. Current vs Target Structure

Before/after: module layout, dependency direction, ownership of responsibilities.
Show the dependency edges that change; confirm the target respects the layer order in
CLAUDE.md.

## 6. Characterization Test Plan

The behavior that must be pinned by tests **before** any code moves: which behaviors,
which test files, and current coverage gaps. If existing tests already pin the behavior,
cite them. /build writes the missing characterization tests first — they are the safety
net that makes the refactor mechanical instead of hopeful.

## 7. Migration Steps

Ordered steps, each leaving the tree green (tests, lint, typecheck pass after every
step). Steps that cannot keep the tree green must be merged or reordered until they can.

## 8. Risks and Rollback

Behavioral edges most at risk (error paths, ordering, timing); rollback is normally
`git revert` per step — note anything that would complicate that.

## 9. Acceptance Criteria

- [ ] AC-1: no externally observable behavior change — all characterization tests pass
      unmodified.
- [ ] AC-2: the motivating violation from §2 no longer exists, verified at `path:line`.
- [ ] AC-3: ...

## 10. SRS Delta

Normally "None" — behavior is unchanged by definition.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

Out-of-scope discoveries (FU-n).
