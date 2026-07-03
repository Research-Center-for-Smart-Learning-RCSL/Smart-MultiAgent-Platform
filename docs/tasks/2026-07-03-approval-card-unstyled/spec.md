---
type: bugfix
status: approved
created: 2026-07-03
requirements: []
supersedes:
---

# ApprovalCard renders with no background, border, or padding

Discovered while clearing the backlog surfaced by
`docs/tasks/2026-07-03-frontend-typecheck-gate` (TS2322 — `"surface"`/`"compact"` are not
assignable to `SCard`'s `variant`/`padding` unions). Same disposition as
`docs/tasks/2026-07-03-project-list-watch-import` and
`docs/tasks/2026-07-03-agent-duplicate-drops-effort`: a genuine visual/behavioral defect
the inert typecheck gate never caught, fixed in its own small dossier.

## 1. Summary

`ApprovalCard.vue` passes `variant="surface"` and `padding="compact"` to `SCard`
(`frontend/src/slices/workflow/components/ApprovalCard.vue:78-80`). Neither value has
ever been a valid `SCard` option
(`frontend/src/shared/ui/SCard.vue:2-5`: `variant` is
`'default' | 'elevated' | 'bordered' | 'flat'`, `padding` is `'none' | 'sm' | 'md' | 'lg'`).
`SCard`'s template builds its CSS class from the raw prop value
(`` `s-card--${variant ?? 'default'}` ``, `` `s-card--pad-${padding ?? 'md'}` ``,
`SCard.vue:11-14`) with no runtime validation, so an invalid value produces a CSS class
(`s-card--surface`, `s-card--pad-compact`) that doesn't exist in `SCard.vue`'s `<style>`
block. No CSS rule matches those classes, so the card currently renders with no
background, no border, and no padding.

## 2. Observed vs Expected

- **Observed**: an approval-gate card in the workflow run view renders as unstyled,
  edge-to-edge content with no visual card boundary.
- **Expected**: the card renders with a visible surface background and compact padding,
  consistent with every other `SCard` usage in the codebase.

## 3. Root Cause

Invalid prop values that were never implemented. `variant="surface"` most plausibly
intends the `flat` variant — `SCard.vue:40-42`'s `.s-card--flat` rule is
`background: var(--color-surface)`, i.e. exactly the "surface" background the name
suggests, just under a different variant name. `padding="compact"` has no exact analog;
`sm` (`SCard.vue:45`, 12px) is the smallest defined option and the closest match to
"compact" among the four real values (`none`/`sm`/`md`/`lg`).

## 4. Regression Test Plan

Add a test to `frontend/src/slices/workflow/__tests__/ApprovalCard.test.ts` (or extend an
existing one covering this component) that mounts `ApprovalCard` and asserts the rendered
root carries `s-card--flat` and `s-card--pad-sm` (the classes `SCard` actually
implements), not `s-card--surface`/`s-card--pad-compact`. Confirmed the test fails for the
documented reason (asserting the wrong/nonexistent classes) against the current code
before the fix.

## 5. Fix

Change `frontend/src/slices/workflow/components/ApprovalCard.vue:78-80` from
`variant="surface"` / `padding="compact"` to `variant="flat"` / `padding="sm"`.

## 6. Acceptance Criteria

- [ ] AC-1: `ApprovalCard` renders with the `s-card--flat` and `s-card--pad-sm` classes;
      verified by the new regression test.
- [ ] AC-2: `pnpm typecheck` no longer reports TS2322 for these two bindings.

## 7. SRS Delta

None — visual bugfix, no requirement change.

## 8. Deviation Log

Appended by `/build`.

## 9. Follow-ups

None.
