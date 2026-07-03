---
type: bugfix
status: implemented
created: 2026-07-03
requirements: []
supersedes:
---

# Duplicating an agent silently drops its reasoning-effort setting

Discovered while clearing the backlog surfaced by
`docs/tasks/2026-07-03-frontend-typecheck-gate` (TS2345 "Property 'effort' is missing").
Same disposition as `docs/tasks/2026-07-03-project-list-watch-import`: a genuine
behavioral defect the inert typecheck gate never caught, fixed in its own small dossier
rather than folded into the types-only refactor.

## 1. Summary

`AgentListView.vue`'s "duplicate" action builds the create payload for the copy from the
source agent's fields, but never includes `effort`
(`frontend/src/slices/agents/views/AgentListView.vue:203-219`). Every other configured
field (model, prompt, context mode, wakeup config, etc.) round-trips into the duplicate;
`effort` does not.

## 2. Observed vs Expected

- **Observed**: duplicating an agent whose `effort` is `"high"` (or `"low"`/`"medium"`)
  creates a copy with `effort` omitted from the create payload, which the backend
  resolves to `null` (provider default) — the copy silently loses the reasoning-effort
  override.
- **Expected**: the duplicate carries over the same `effort` value as the source agent,
  matching every other field the duplicate action already copies.

## 3. Root Cause

Missing key. The create-payload object literal at
`frontend/src/slices/agents/views/AgentListView.vue:205-219` omits `effort` entirely. The
established pattern for constructing this exact field from an existing `Agent` already
exists elsewhere in the same slice —
`frontend/src/slices/agents/views/AgentDetailView.vue:344`:
`effort: (agent.effort ?? null) as AgentCreateInput['effort']`.

## 4. Regression Test Plan

Add a test to `frontend/src/slices/agents/__tests__/AgentListView.test.ts` (or create one
if it doesn't yet cover the duplicate action) that triggers the duplicate mutation for an
agent fixture with a non-null `effort` and asserts the `agentsApi.create` call payload
includes the same `effort` value. Confirmed the test fails for the documented reason
(payload has no `effort` key) against the current code before the fix.

## 5. Fix

Add `effort: (agent.effort ?? null) as AgentCreateInput['effort']` to the create payload
at `frontend/src/slices/agents/views/AgentListView.vue:205-219`, mirroring
`AgentDetailView.vue:344`. One-line change, no behavior change beyond making the
duplicate actually carry over the field it was always supposed to.

## 6. Acceptance Criteria

- [x] AC-1: duplicating an agent with a non-null `effort` produces a create payload that
      includes the same `effort` value; verified by the new regression test.
- [x] AC-2: `pnpm typecheck` no longer reports TS2345 for this call site.

## 7. SRS Delta

None — restores already-intended behavior (parity with every other duplicated field).

## 8. Deviation Log

Appended by `/build`.

## 9. Follow-ups

None.
