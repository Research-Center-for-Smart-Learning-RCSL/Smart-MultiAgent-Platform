---
type: bugfix
status: approved
created: 2026-08-16
requirements: [R30.33]
depends_on: []
---

# The shipped-examples dialog re-enables a button mid-flight, and a duplicate disable reports failure for an action that succeeded

## 1. Summary

`ExampleImportDialog` tracks in-flight work with a single `pendingId` shared by both its
mutations, and disables only the row whose id currently sits in it. Clicking a second row while
the first is in flight moves `pendingId`, which re-enables the first row's button; when the
first request settles, `onSettled` clears `pendingId` unconditionally, re-enabling the second
row's button while its own request is still outstanding. This is the exact mechanism D-14 of
`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:820-823` identified and fixed in
the sibling agents dialog, left unfixed here.

On the enable path the backend is idempotent, so the consequence is a duplicate success toast
and a button that visibly flickers back to enabled. On the disable path it is worse: a second
opt-out raises `ActivityTypeNotOptedIn`, so the user is shown a failure toast for a disable that
in fact succeeded.

F-10 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`.

## 2. Observed vs Expected

**Observed.**

- `frontend/src/slices/activities/components/ExampleImportDialog.vue:31-32` declares one
  `pendingId`, with a comment asserting the invariant it does not hold: "Which row is
  mid-request, so only its own button shows a pending state."
- `:68-70` and `:83-85`: both mutations' `onSettled` set `pendingId.value = null`
  unconditionally, with no check that the settling request is the one that set it.
- `:200` and `:209`: `:disabled="pendingId === example.id"` - only the row named by the current
  `pendingId` is disabled. Neither button consults `enableMutation.isPending` or
  `disableMutation.isPending`; those objects are never referenced in the template.
- `:89-90` and `:102-103`: `enable` and `disable` each overwrite `pendingId` before calling
  `mutate`.
- The backend is asymmetric.
  `backend/contexts/activities/application/example_service.py:264-265`: `opt_in` returns early
  when the row already exists ("already enabled; nothing changed, so nothing to audit"), backed
  by an `on_conflict_do_nothing` upsert in the repository. But `:305-307`: `opt_out` raises
  `ActivityTypeNotOptedIn` when `remove` returns false.
- `:79-82`: the disable mutation's `onError` shows `activities.examples.disableFailed`.

**Expected.** While any request from this dialog is in flight, no row's action button accepts a
click. A duplicate opt-out - which can only arise from the UI defect - must not be presented to
the user as a failure, because the state they asked for was reached.

**Intent source.** D-14 (`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:820-823`)
states the rule for this component class verbatim: "`pendingPack` is single-valued, so disabling
only the in-flight row let a second install start, overwrite it, and have the first completion
clear the pending state for the wrong pack." The fixed exemplar is
`frontend/src/slices/agents/components/AgentPackInstallDialog.vue:241-248`, which disables on
`pendingPack !== null` rather than on identity. [R30.33] governs the opt-in/opt-out semantics
the toasts report on.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Match D-14 exactly (disable all rows while any request is in flight), or give each row its own pending state so unrelated rows stay clickable? | **Match D-14: disable all action buttons while any request is in flight.** | Not a user question - D-14 already decided it for the sibling dialog after a code review, and the two dialogs should not diverge on the same mechanism. Per-row state is also wrong on the merits here: each mutation invalidates the shared `activityKeys.examples` and `activityKeys.types` queries (`ExampleImportDialog.vue:51-56`), so two concurrent requests race on the same cache entry regardless of which rows they touch. |
| Q-2 | Should the duplicate-opt-out failure be fixed in the UI (suppress the toast) or in the backend (make `opt_out` idempotent like `opt_in`)? | **UI-side, by preventing the duplicate; leave `opt_out` raising.** | Not a user question. `opt_out` is destructive - it ends the project's activations and closes its open sessions (`example_service.py:309-330`) - so "you asked to revoke something that was not granted" is a legitimate 4xx and a real signal for an API client. `opt_in`'s idempotency is safe because it writes one row. Making `opt_out` silently succeed on a no-op would hide genuine client errors to paper over a UI race that Q-1 already removes. Recorded rather than acted on: see FU-1 for the residual. |
| Q-3 | Should the same fix be applied to `ActivityExamplesSection`, which the audit noted has the identical single-valued shape? | **Yes, in this dossier.** | The audit found the same `installingKey` shape at `frontend/src/slices/admin/components/ActivityExamplesSection.vue:142`, `:62`, `:191-193`, currently masked only because exactly one course ships and the button is additionally disabled by `fully_installed`. Fixing one instance of a two-instance mistake is half a fix, and the second instance's mask is an accident of the shipped catalogue rather than a design. Note the overlap constraint this creates - see Q-4. |
| Q-4 | Does any unfinished dossier conflict? | **No `depends_on`, but one overlap to coordinate.** | `docs/tasks/BOARD.md` lists only `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches these files. Among the sibling dossiers from this audit, `2026-08-16-admin-platform-type-edit-unreachable` (F-4) edits `ActivityExamplesSection.vue` substantially. Q-3 brings this dossier into that same file. The two changes are in different regions (pending state versus the Edit action's row resolution), so they are not sequenced, but whichever builds second must rebase rather than assume. |

## 4. Reproduction

**Preconditions.** A platform admin has installed the shipped `creative-thinking` course, so the
dialog lists **four** platform examples. The acting user is a Project Owner of project P, and P
has opted into none of them.

**Steps (enable path).**

1. Open `/projects/P/activity-types` and click the shipped-examples action to open the dialog.
2. Click **Enable** on 單元二 時空旅人（曼陀羅九宮格）.
3. Before that request settles, click **Enable** on 單元四 情緒列車（六頂思考帽）.

**Actual.** At step 3 `pendingId` becomes the second row's id, so the first row's button is
enabled again while its POST is outstanding. When the first request returns, `onSettled` sets
`pendingId = null`, so the second row's button becomes clickable while its own POST is still in
flight. A further click issues a duplicate opt-in and a second success toast appears for one
logical action.

**Expected.** From the first click until the last request settles, no Enable or Disable button
accepts a click.

**Steps (disable path, the worse variant).** With two examples already enabled, click
**Disable** on one and confirm; while it is in flight, disable the other and confirm; then click
the first row's now-re-enabled Disable button again.

**Actual.** The duplicate opt-out raises `ActivityTypeNotOptedIn`
(`example_service.py:305-307`), the mutation's `onError` fires, and the user sees
`activities.examples.disableFailed` for a disable that succeeded. The confirm dialog at
`:96-101` makes this slower to reach than the enable path but does not prevent it.

## 5. Root Cause Analysis

1. **Root cause.** `pendingId` is a single-valued identity token used to answer a boolean
   question. `ExampleImportDialog.vue:200` and `:209` ask "is *this* row the pending one",
   which is only equivalent to "is anything pending" when at most one request can ever start -
   and nothing enforces that, because starting the second request is what breaks the
   equivalence. Correcting this link (disable on "anything pending" rather than on identity)
   prevents every downstream symptom.
2. `onSettled` compounds it: `:68-70` and `:83-85` clear the token without checking whether the
   settling request owns it, so the first completion releases the second request's lock. Even a
   per-row token would need this check.
3. The backend asymmetry (`opt_in` idempotent at `:264-265`, `opt_out` raising at `:305-307`)
   is not a cause - both are correct in isolation, per Q-2 - but it decides which symptom the
   user sees. It turns the enable-path defect into cosmetic noise and the disable-path defect
   into an incorrect failure message.

**Why the sibling was fixed and this was not.** D-14 was appended after `/code-review` on the
agent-packs branch, which reviewed that branch's diff. `ExampleImportDialog` was shipped by the
earlier platform-example dossier and was not in that diff, so the review that found the pattern
never saw this instance.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Project Owners using the shipped-examples dialog. No data corruption: a
duplicate opt-in is a no-op row-wise, and a duplicate opt-out is refused before any cascade
runs (`example_service.py:305-307` precedes the activation loop at `:317`). The damage is a
misleading failure toast and a UI that contradicts its own asserted invariant.

**Sibling suspects** - components using a single-valued token to gate multiple rows:

| Site | Verdict |
|---|---|
| `frontend/src/slices/admin/components/ActivityExamplesSection.vue:142`, `:62`, `:191-193` (`installingKey`) | **confirmed**, currently masked because exactly one course ships and the button is also disabled by `fully_installed`. Fixed here per Q-3. |
| `frontend/src/slices/agents/components/AgentPackInstallDialog.vue:241-248` | **cleared** - this is the fixed exemplar (D-14); it disables on `pendingPack !== null`. |

Both instances of the pattern in the example subsystem are therefore accounted for. A wider
sweep for the same shape outside this subsystem is not in scope; recorded as FU-2.

## 7. Fix Design

**7.1 `ExampleImportDialog.vue`.** Keep `pendingId` for *which row shows a spinner*, and gate
`:disabled` on whether **anything** is in flight, matching
`AgentPackInstallDialog.vue:241-248`. Concretely, both buttons' `:disabled` becomes a check that
no request is outstanding, derived from the two mutations' own `isPending` rather than from a
hand-maintained ref where possible - TanStack already tracks this, and using it removes the
second half of the root cause (the unconditional `onSettled` clear) rather than patching it.
Update the comment at `:31` so it states the invariant the code now actually holds.

**7.2 `ActivityExamplesSection.vue`.** Apply the same gate to `installingKey` (Q-3), so the
component stops depending on "only one course ships" for its correctness.

**7.3 No backend change.** Per Q-2, `opt_out` keeps raising `ActivityTypeNotOptedIn`. Once the
duplicate request cannot be issued, the misleading toast cannot be reached from the UI, and the
error remains available and correct for a direct API client.

**Why this does not mask the symptom.** The symptom is a duplicate request; the cause is a gate
that permits it. Suppressing the `disableFailed` toast would be masking - it would leave the
duplicate request being issued and merely hide the evidence, and it would also hide the toast
in the case where the error is genuine.

**Data repair.** None - no incorrect data was written.

## 8. Regression Test Plan

The failing test comes first.

**8.1** `frontend/src/slices/activities/__tests__/ExampleImportDialog.test.ts` gains
`TestConcurrentToggles`:

- Arrange: four examples listed; the opt-in call returns a promise the test controls.
- Act: click Enable on row 1, leaving its request unresolved.
- Assert: **every** action button in the dialog is disabled, not only row 1's.
- Fails today because rows 2-4 are enabled - `:disabled` is `pendingId === example.id`, which
  is false for them.

**8.2** Same file: assert the settle ordering.

- Act: start row 1's request; resolve it; while a second request would still be outstanding in
  the real sequence, assert the buttons re-enable only once nothing is pending.
- The concrete assertion that fails today: after row 1 settles, row 2's button must not become
  clickable while row 2's own request is unresolved.

**8.3** `frontend/src/slices/admin/__tests__/ActivityExamplesSection.test.ts` gains the
equivalent assertion for `installingKey` (Q-3). To make it meaningful the fixture must list
**two** courses, since the current single-course catalogue is what masks the defect; this is a
fixture change, not a production change.

**8.4** Existing tests in both files must keep passing unmodified. If any asserts that a
non-pending row is clickable during another row's request, that assertion was pinning the
defect and its change should be called out in the deviation log rather than made silently.

**8.5** No backend test changes: `opt_out`'s behaviour is unchanged, and
`ActivityTypeNotOptedIn` coverage stays as-is.

## 9. Risks and Rollback

- **Low.** Two components, template-level `:disabled` expressions plus the removal of a
  hand-maintained clear. No API, no schema, no i18n keys.
- **Over-disabling.** Gating on "anything pending" means a slow opt-out blocks the whole dialog,
  including rows the user might reasonably want to toggle. This is the trade D-14 already
  accepted for the sibling, and it is the safer direction: the alternative permits concurrent
  mutations that race on the same invalidated cache entries (Q-1).
- **File contention** with `2026-08-16-admin-platform-type-edit-unreachable` in
  `ActivityExamplesSection.vue` (Q-4). Different regions; rebase rather than sequence.
- **Rollback**: `git revert`. Nothing depends on these expressions.

## 10. Acceptance Criteria

- [ ] AC-1: The test from §8.1 fails before the fix and passes after.
- [ ] AC-2: While any opt-in or opt-out request from `ExampleImportDialog` is in flight, every
  Enable and Disable button in the dialog is disabled.
- [ ] AC-3: A request settling re-enables the buttons only when no other request from the same
  dialog is still outstanding (§8.2).
- [ ] AC-4: The same guarantee holds for `ActivityExamplesSection`'s install action, verified
  against a fixture listing at least two courses (§8.3).
- [ ] AC-5: The comment at `ExampleImportDialog.vue:31` states the invariant the code holds.
- [ ] AC-6: `opt_out` still raises `ActivityTypeNotOptedIn` for a genuine duplicate, and its
  backend tests pass unmodified.
- [ ] AC-7: Gates green: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`.

## 11. SRS Delta

**None.** [R30.33] already defines opt-in and opt-out; this restores a UI invariant the
component already claims in a comment and that D-14 established for the sibling component.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: The enable and disable paths have different idempotency semantics (`opt_in`
  tolerant at `example_service.py:264-265`, `opt_out` strict at `:305-307`). Q-2 keeps both,
  and both are individually defensible, but the asymmetry is undocumented at the API level and a
  client author would have to read the service to discover it. Worth one sentence in the route
  docstrings, or an explicit note in the OpenAPI descriptions.
- **FU-2**: This is the second instance of "single-valued pending token gating multiple rows"
  found in this codebase, and the first was found by code review rather than by a test or a
  lint rule. A sweep for the shape across all slices would say whether there is a third; it is
  the kind of defect that is invisible to every gate and only appears under a fast double
  click.
- **FU-3**: `ExampleImportDialog`'s two mutations both call `invalidate()` in `onSuccess` *and*
  `onError` (`:60-67`, `:75-82`), so a failed request still refetches two queries. Harmless and
  arguably deliberate (the server state may have changed), but it means a burst of failures
  produces a burst of refetches. Not a defect; noted because the fix touches the same block.
