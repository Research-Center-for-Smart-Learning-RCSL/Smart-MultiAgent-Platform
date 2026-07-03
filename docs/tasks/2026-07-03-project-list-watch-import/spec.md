---
type: bugfix
status: implemented
created: 2026-07-03
requirements: []
supersedes:
---

# ProjectListView crashes on `?create=1` — missing `watch` import

Discovered while clearing the backlog surfaced by
`docs/tasks/2026-07-03-frontend-typecheck-gate` (TS2304 `Cannot find name 'watch'`, one of
the real defects that task's Migration Steps route out rather than fix in place). User
decision: fix directly in this small dossier rather than defer (see that task's Deviation
Log).

## 1. Summary

`ProjectListView.vue` calls `watch(orgs, ...)` at
`frontend/src/slices/tenancy/views/ProjectListView.vue:78` but only imports `ref, computed`
from `vue` (`:1`) — `watch` is never imported. Any navigation to the project list with
`?create=1` in the query string throws `ReferenceError: watch is not defined` inside the
`<script setup>` top-level, which aborts component setup.

## 2. Observed vs Expected

- **Observed**: opening `/projects?create=1` throws `ReferenceError: watch is not defined`
  during component setup (`ProjectListView.vue:78`); the create-project modal never opens
  and the view fails to render.
- **Expected**: the view renders normally, and once the org list loads, the create-project
  modal opens automatically (pre-filling the org owner when the active tab is an org),
  matching the intent of the `route.query.create === '1'` branch at `:76-85`.

## 3. Root Cause

Missing import. `frontend/src/slices/tenancy/views/ProjectListView.vue:2` imports
`{ ref, computed }` from `'vue'`; `watch` is used at `:78` without being imported or
otherwise declared.

## 4. Regression Test Plan

Add a test to `frontend/src/slices/tenancy/__tests__/ProjectListView.test.ts` that mounts
`ProjectListView` with `initialRoute: '/projects?create=1'` (via the existing `renderView`
test helper, `frontend/tests/utils/render.ts:15`) and asserts the mount does not throw.
Confirmed the test fails for the documented reason (`ReferenceError: watch is not defined`)
against the current code before the fix.

## 5. Fix

Add `watch` to the existing `vue` import at
`frontend/src/slices/tenancy/views/ProjectListView.vue:2`. One-line change, no behavior
change beyond making the already-written `?create=1` branch actually run.

## 6. Acceptance Criteria

- [x] AC-1: mounting `ProjectListView` with `?create=1` in the route no longer throws;
      verified by the new regression test in `ProjectListView.test.ts`.
- [x] AC-2: `pnpm typecheck` no longer reports TS2304 for this file.

## 7. SRS Delta

None — restores already-intended behavior.

## 8. Deviation Log

Appended by `/build`.

## 9. Follow-ups

None.
