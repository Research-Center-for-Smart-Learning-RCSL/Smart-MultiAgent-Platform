---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.23, R30.31, R30.32]
depends_on: []
---

# The admin Edit action on an installed platform example is offered, enabled, and does nothing

## 1. Summary

`ActivityExamplesSection` resolves the row to edit by scanning **one 200-row page** of an admin
listing that spans every project's activity types, newest first. Platform examples are installed
at setup, so they are the oldest rows and the first to fall off that page. When the row is not
found, the Edit button is still offered (it keys off the catalogue, not the listing), the dialog
still opens, the form seeds hardcoded defaults instead of the stored values, Save re-enables as
soon as the admin types a name, and clicking it returns early with **no request, no error, and
no toast**.

This is the exact path Q-4 of
`docs/tasks/2026-08-09-platform-example-activity-types/spec.md:76` exists for: an admin editing a
policy-locked example back into compliance. On a deployment past 200 activity types it dead-ends
silently. F-4 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`, the
strongest of the frontend findings.

## 2. Observed vs Expected

**Observed.**

- `frontend/src/slices/admin/components/ActivityExamplesSection.vue:147` -
  `TYPES_PAGE_LIMIT = 200`; `:160-163` fetches one page with no `enabled` gate and no cursor;
  `:165-167` - `editRow = (typesQuery.data.value ?? []).find((r) => r.id === editTypeId.value) ?? null`.
- `:96-104` - the Edit button's **only** condition is `v-if="unit.installed_type_id !== null"`,
  read from the *catalogue* response, not from `typesQuery`. No `:disabled`, no loading guard,
  no null guard.
- `:110-115` - the dialog is rendered with `:open="editTypeId !== null"` and `:row="editRow"`,
  so it opens on `editTypeId` alone, independent of whether the row resolved.
- `frontend/src/slices/admin/components/PlatformActivityTypeDialog.vue:147-159` - the seeding
  watch, with `row === null`, sets `name=''`, `expose_payload_to_agent=true`,
  `echo_includes_content=false`, `retention=''`: plausible-looking defaults that are not the
  stored row's.
- `:75` - Save is disabled only on `retentionInvalid || form.name.trim() === ''`, so typing a
  name re-enables it.
- `:200-201` - `onSubmit` returns early on `props.row === null`, **before** clearing the refusal
  state and before the mutation. No toast, no request, no state change of any kind. The guard
  cannot simply be deleted: `mutationFn` (`:183-184`) falls back to `props.row?.id ?? ''`, which
  would PATCH an empty id.
- **The listing is not scope-filtered.** `backend/app/api/v1/admin_activities.py:258-266` is
  documented as "Every live activity type across every project, newest first", and
  `backend/contexts/activities/infrastructure/repositories/type_repo.py:152-166` says "Unscoped
  by design". So the page fills with every tenant's types, not the handful of platform ones.
- The codebase already anticipates this exact truncation elsewhere:
  `frontend/src/slices/admin/views/AdminActivitiesView.vue:202-207` reasons about "the newest 50
  of 300 types" and renders a warning at `:149-155` using an i18n key that already exists
  (`frontend/src/slices/admin/locales/en.json:254`, `admin.activities.truncated`).
  `ActivityExamplesSection` shares the limit and has no warning.

**Expected.** The Edit action opens a form seeded from the stored row and saves it, for every
installed platform example, regardless of how many activity types exist platform-wide.

**Intent sources.** Q-4 and AC-8 of
`docs/tasks/2026-08-09-platform-example-activity-types/spec.md:76`, `:534-538`: a platform admin
may edit an installed platform type's `name`, `retention_days` and the two governance flags,
because "under a strictly read-only model the two shipped examples become permanently
unactivatable with no actor able to fix them". [R30.23] and [R30.31] carry the same capability.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the admin surface obtain the row: a scope-filtered listing, a by-id read, or a UI guard only? | **A new admin route listing platform types**, `GET /api/admin/platform-activity-types`. | User decision. The backing methods already exist and their docstrings say this is what they are for: `type_repo.list_platform()` (`type_repo.py:219-239`, "Unbounded on purpose: platform types are installed one course at a time by an admin, so the population is bounded by deliberate acts rather than by tenant traffic") and `ActivitiesFacade.list_platform_types` (`facade.py:188-190`). No admin HTTP route surfaces them today. Correctness is unconditional - the "not in the first 200" class of bug cannot recur - and it fixes the section's listing staleness in the same stroke (Q-3). |
| Q-2 | Why not a by-id read, or a `scope` query parameter on the existing listing? | Both rejected. | A by-id read (`GET /api/admin/activity-types/{type_id}`) is the more reusable primitive and `facade.get_type` already exists (`facade.py:310-314`), but it adds a 404 path the UI must render and does not fix Q-3's staleness. A `scope` parameter on the existing listing is worse than both: the section deliberately shares `adminKeys.activityTypes()` with the governance table (`ActivityExamplesSection.vue:145-146`, `AdminActivitiesView.vue:209-212`), so a scope-filtered fetch under the same key would poison that table's cache with a platform-only list, forcing the key factory to become parameterised and dragging `AdminActivitiesView` into the change. |
| Q-3 | Should the section display the **stored** values for installed units instead of the shipped ones? | **Yes**, since Q-1 makes it nearly free. | The catalogue card is built from the course *file*: `CatalogueTypeEntry` takes `name`, `expose_payload_to_agent`, `echo_includes_content` and `retention_days` from `course.activity_types` and only `installed_type_id` from the database (`backend/contexts/activities/application/example_service.py:144-153`), rendered at `ActivityExamplesSection.vue:78-79`. So an admin who edits a type sees their edit reflected in the dialog but not in the card behind it. The component's own comment at `:156-159` acknowledges the discrepancy and works around it for the dialog only. This is the same staleness class as the dialog's, and fixing one while leaving the other would be half a fix. |
| Q-4 | Is a UI guard (disable Edit until the row resolves) sufficient on its own? | **No, but it is required as well.** | Q-4 of the source dossier exists precisely because a read-only model leaves the shipped examples "permanently unactivatable with no actor able to fix them". A guard that honestly disables Edit **re-creates that dead end** on any platform past 200 types - the admin is told they cannot fix it rather than lied to, which is better but is still the failure the criterion was written to eliminate. The guard is needed for the in-flight window (§7.3); it is not the fix. |
| Q-5 | Does any unfinished dossier conflict? | **No `depends_on`, one file to coordinate.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches the admin slice. `2026-08-16-example-dialog-pending-and-optout` (F-10) also edits `ActivityExamplesSection.vue`, in a different region (the `installingKey` pending state versus row resolution here). Rebase rather than sequence. |

## 4. Reproduction

**Preconditions.** A platform admin has installed `creative-thinking`. Projects across the
deployment have since created **more than 200** activity types in total, so the newest-first
admin listing no longer contains the platform rows.

**Steps.**

1. Open `/admin/activities` and scroll to the shipped-examples section.
2. Click **Edit** beside 單元二 時空旅人（曼陀羅九宮格）.
3. Type any name into the Name field.
4. Click **Save**.

**Actual.** Step 2 opens a dialog whose Name field is **blank** and whose two governance
switches show `expose_payload_to_agent = true` and `echo_includes_content = false` - defaults,
not the stored values. Step 4 does nothing: no network request, no error, no toast, the dialog
stays open.

**Second, commoner variant** (no 200-type precondition): the examples query and the types query
both start at mount with no gating, and the examples query is the smaller response. An admin who
clicks Edit and begins typing before the types page lands has the form **reseeded from under
them** when it does, discarding what they typed.

## 5. Root Cause Analysis

1. **Root cause.** `editRow` (`ActivityExamplesSection.vue:165-167`) resolves the row by scanning
   a bounded page of an unbounded, unscoped listing. Replacing that source with one that always
   contains every platform type (Q-1) prevents the symptom.
2. The Edit button is gated on a *different* data source from the one that resolves the row
   (`:96-104` reads the catalogue's `installed_type_id`; `:165-167` reads `typesQuery`), so the
   action is offered in states where it cannot work. This is the second link and is why the UI
   guard in §7.3 is still needed even after Q-1.
3. The dialog treats "no row" as "new row" rather than as an error state
   (`PlatformActivityTypeDialog.vue:147-159`), which converts a resolution failure into a
   plausible-looking form. Third link.
4. `onSubmit`'s early return (`:200-201`) then makes the failure **silent**. This is a defect in
   its own right independent of row resolution: an enabled control that performs no action and
   reports nothing has no correct excuse.

**The reseed variant's mechanism, which is worse than it looks.** The watch source
(`:148`) is a getter returning a **fresh array literal**, `() => [props.open, props.row?.id ?? null] as const`.
Vue treats `isMultiSource` as true only when the source *itself* is an array; here it is a
function, so the change test is `!Object.is(newArray, oldArray)` - a new array identity every
evaluation, so the value comparison **always reports "changed"** and only effect dirtiness gates
the callback. Two consequences: the null-to-id transition genuinely reseeds, and the protection
the comment at `:142-146` claims (keying on `props.row?.id` so a refetch with identical contents
does not reseed) **does not hold** - a `refetchOnWindowFocus` on `adminKeys.activityTypes()`
reseeds an open form today. Same root cause, independently testable.

**Why no test caught it.** All six tests in
`frontend/src/slices/admin/__tests__/ActivityExamplesSection.test.ts` stub the types endpoint
with the matching row present (`:118`, `:135`, `:171`, `:204`; `:79` and `:95` stub `[]` but
those cases have `installed_type_id: null`, so no Edit button renders). The null branch is never
exercised.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Platform admins on any deployment where the total live activity-type count
across all projects exceeds 200 - which grows with tenant activity, not with anything the admin
controls. The consequence is that the governance escape hatch of Q-4 becomes unreachable
exactly when the platform is busiest. The reseed variant affects every deployment regardless of
size.

No data is corrupted: the failure is a write that does not happen.

**Sibling suspects** - other components resolving an entity from a bounded page:

| Site | Verdict |
|---|---|
| `AdminActivitiesView.vue:202-219` | **cleared** - shares the 200 limit but only *displays* rows; it resolves nothing by id, and it renders a truncation warning (`:149-155`). It is the exemplar this fix borrows from. |
| `ActivityExamplesSection.vue:165-167` | **confirmed** - this defect. |
| `PlatformActivityTypeDialog.vue:147-159` | **confirmed** - the null-row-as-new-row treatment and the always-changed watch source. |
| The audit's FU-10 concern (the two components thrashing one cache entry) | **cleared** - both limits really are 200 (`ActivityExamplesSection.vue:147`, `AdminActivitiesView.vue:207`), so the shared `adminKeys.activityTypes()` entry is not thrashed. Q-1's new route uses a **new** key precisely to keep it that way. |

**Systemic reading.** The pattern is "offer an action from data source A, resolve it from data
source B, where B is bounded and A is not". One instance found; the general form is worth a
sweep, recorded as FU-1.

## 7. Fix Design

**7.1 A platform-types admin route.** `GET /api/admin/platform-activity-types`, gated on
`require_admin`, returning `[_type_out(at, project_name=None) for at in await
ActivitiesFacade(db).list_platform_types()]`. The repository method (`type_repo.py:219-239`) and
facade method (`facade.py:188-190`) already exist; the response model `AdminActivityTypeOut`
(`admin_activities.py:61-87`) and its builder `_type_out` (`:148-163`) are reused unchanged.
Unbounded, matching the documented rationale of the method it wraps.

**The new route must be added to the parametrized admin-gate test** at
`backend/tests/unit/test_admin_activities_routes.py:128-134` - that test is the only thing
pinning `require_admin` onto each handler.

**7.2 The section uses it.** `ActivityExamplesSection.vue` replaces its `typesQuery` with one
against the new endpoint under a **new** query key,
`adminKeys.platformActivityTypes()`, following the flat `['admin', …] as const` convention
(`frontend/src/slices/admin/queries/index.ts:3-30`). Deliberately not a variant of
`activityTypes()`, so the governance table's shared cache entry is untouched (Q-2). A new
`adminApi` wrapper follows the pattern at
`frontend/src/slices/admin/api/admin.ts:137-141`.

`editRow` then resolves against a list that always contains every platform type, and `:78-79`
renders the **stored** values for installed units rather than the course file's (Q-3), removing
the discrepancy the comment at `:156-159` documents.

**7.3 Guard the action and the reseed.** Two guards, both required:

- The Edit button (`:96-104`) gains a `:disabled` on the row being unresolved, so the action is
  never offered in a state where it cannot work. `SButton` merges `disabled` and `loading`
  (`frontend/src/shared/ui/SButton.vue:29`, `:38`).
- The dialog's watch (`PlatformActivityTypeDialog.vue:147-159`) gets a **scalar** source, so its
  change test is meaningful - e.g. keying on the id alone rather than on a freshly-allocated
  array. Without this the refocus-reseed defect (§5) survives the row-resolution fix.

Also add the truncation warning to the section for consistency with `AdminActivitiesView`, using
the existing `admin.activities.truncated` key (`admin/locales/en.json:254`) - a sibling of the
`examples` block, so no new key is needed. This is belt-and-braces once 7.1 lands, and it is what
makes the component honest if the new route is ever paginated.

**7.4 Make `onSubmit` say something.** Keep the `props.row === null` guard (`:200-201`) - it
protects `mutationFn`'s `?? ''` fallback - but pair it with a `toast.warning`, the established
in-slice idiom for a client-side guard stopping a submit
(`frontend/src/slices/admin/views/AdminAuditView.vue:242-246`,
`AdminRateLimitsView.vue:141-150`). `useToast` is already imported and instantiated in the dialog
(`:104`, `:121`). Additionally disable Save on `row === null` by extending `:75`, so the state is
prevented rather than only reported. One new i18n key in both locale files under
`admin.activities.examples`, alongside `saveFailed` (`en.json:277`).

**Why this does not mask the symptom.** The symptom is an action that does nothing; the cause is
that the row it needs is absent from the only place the component looks. 7.1 and 7.2 make it
present. 7.3 and 7.4 handle the residual states honestly rather than substituting for the fix -
which is Q-4's whole point.

**Data repair.** None.

## 8. Regression Test Plan

The failing test comes first.

**8.1 The failing test.** In
`frontend/src/slices/admin/__tests__/ActivityExamplesSection.test.ts`, named for the rule:
"opens the edit form with the stored values even when the type is not in the types page".

- Arrange: the catalogue returns one installed unit (`installed('at_1')`, helper at `:37-43`);
  the types listing returns 200 rows **not containing** `at_1`, mirroring the truncation fixture
  at `AdminActivitiesView.test.ts:122`; the PATCH handler captures its body (pattern at `:136-139`).
- Act: render, click Edit, type a name, submit.
- Assert: (1) the name input pre-fills with the stored row's name, not `''`; (2) the captured
  PATCH body is non-null and its `expose_payload_to_agent` is the stored value rather than the
  `true` default from `PlatformActivityTypeDialog.vue:154`; (3) with the row query still pending,
  the Edit button is disabled.
- **Why it fails today**: `editRow` finds nothing, the dialog opens anyway, the watch seeds
  defaults so assertion 1 sees `''`, and `onSubmit` returns at `:201` so assertion 2 sees `null`.
  Assertion 3 fails because the button has no `:disabled` at all.

**8.2 The reseed variant.** Assert that with the dialog open and the form dirty, a refetch
returning an identical row does **not** reseed the form. Fails today because the watch's array
source always compares as changed.

**8.3 Backend.** A route test beside `TestExampleCatalogueRoutes`
(`backend/tests/unit/test_admin_activities_routes.py:381`) asserting the new route returns every
platform row unpaginated, **plus** adding it to the parametrized admin-gate list at `:128-134`.

**8.4 Existing tests that change, and why.** All six tests in `ActivityExamplesSection.test.ts`
need the new platform-types endpoint stubbed, or MSW emits unhandled-request warnings - the same
adjustment `AdminActivitiesView.test.ts:42-44` already had to make when the examples endpoint was
added. Test 4 (`:129-166`, "edits only the four permitted fields", AC-8) is the one whose
*substance* moves: its stored row must now come from the new endpoint. Its intent survives
unchanged; only its stub moves. `AdminActivitiesView.test.ts`'s `stub()` helper (`:38-46`) needs
the same addition.

**8.5 Must stay green.** `AdminActivitiesView.test.ts:79-85` asserts
`wrapper.findAll('tbody button').length === 0` (the governance tables are read-only). The
section's controls live in `<li>` elements, not `<tbody>`, so it stays green - but if the fix
puts any control inside a table it breaks, which is the intended signal.

## 9. Risks and Rollback

- **OpenAPI contract change.** A new route means `pnpm run gen:api` and
  `pnpm run check:openapi-drift`. The source dossier records this gate as a known trap (D-8,
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:785-791`).
- **An unbounded admin response.** Every other listing in `admin_activities.py` is keyset
  paginated (`:47-50`); this one is not, matching `list_platform()`'s documented rationale that
  the population is bounded by deliberate admin installs. If that assumption ever breaks, the
  route needs the same pagination - and the truncation warning added in §7.3 is what would make
  the breakage visible rather than silent.
- **Q-3 changes what the admin sees.** Cards for installed units will show stored values instead
  of shipped ones, so an admin who previously edited a type will see different text than before.
  That is the correction, but it is a visible change to an existing screen.
- **File contention** with `2026-08-16-example-dialog-pending-and-optout` in
  `ActivityExamplesSection.vue` (Q-5). Different regions; rebase.
- **Rollback**: `git revert`. The new route would have no consumer; nothing else depends on it.

## 10. Acceptance Criteria

- [x] AC-1: The test from §8.1 fails before the fix and passes after.
  *Observed red first: `expected '' to be 'Mandala (renamed by an admin)'`, then green.*
- [x] AC-2: Editing an installed platform example works regardless of how many activity types
  exist platform-wide: the form seeds from the stored row and the PATCH is sent.
  *The §8.1 test keeps the 200-row cross-project fixture that does **not** contain the installed
  type, so it stays pinned against the exact truncation that caused the defect.*
- [x] AC-3: The Edit action is disabled while the row is unresolved, so it is never offered in a
  state where it cannot work.
  *Its own test, gated on a deferred response, asserting both that Edit is disabled while the
  request is in flight and that it is enabled once the row lands - a guard that never lifts
  would be the same dead end in a different costume.*
- [x] AC-4: Clicking Save with no resolved row surfaces a toast rather than doing nothing
  silently, and Save is disabled in that state.
- [x] AC-5: A refetch returning an identical row does not reseed an open, dirty form (§8.2).
  *Two tests, and the split matters - see **D-5**. The identical-row case passed before this fix
  as well, because vue-query's structural sharing never hands the dialog a new object for a
  deeply-equal refetch. The case that reaches the watch, and that failed first, is a refetch
  whose **contents** changed.*
- [x] AC-6: The catalogue cards show the **stored** values for installed units, not the course
  file's (Q-3).
- [x] AC-7: `GET /api/admin/platform-activity-types` returns every live platform type, is gated
  on `require_admin`, and is covered by the parametrized admin-gate test at
  `test_admin_activities_routes.py:128-134`.
  *The gate half landed in `35a0a47`; the "returns every platform row unpaginated" half of §8.3
  did not, and was added now - see **D-7**.*
- [x] AC-8: The governance table's `adminKeys.activityTypes()` cache entry is untouched - the new
  query uses its own key, and `AdminActivitiesView`'s tests pass unmodified.
  *Unmodified in substance: no assertion changed. Its `stub()` helper gained the one MSW line
  §8.4 called for, without which every test in that file logs an unmocked request.*
- [x] AC-9: ~~The section renders the truncation warning using the existing
  `admin.activities.truncated` key~~ — **superseded by D-4.** The section warns when an installed
  example's stored row is absent from the listing, under a new key; any genuinely new string
  exists in both `en.json` and `zh-TW.json`.
  *Key parity re-checked programmatically over the whole admin bundle: zero keys on either side
  only.*
- [x] AC-10: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`,
  `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm run check:bundle-size`,
  `pnpm run check:type-coverage`, `pnpm run check:boundaries-enforced`.
  *`ruff` clean over 943 files (one file needed `ruff format`, applied), `mypy` clean over 938,
  frontend 1126 tests over 181 files, lint/typecheck/build clean, and the three budget gates
  passed. `check:openapi-drift` could **not** run on this host and was verified another way -
  see **D-8**.*

## 11. SRS Delta

**None.** [R30.23] and [R30.31] already grant the platform admin the edit capability this fix
makes reachable, and [R30.32] already governs the install surface. Nothing new is defined; a
documented capability is restored.

## 12. Deviation Log

- **D-1** — **The OpenAPI spec must be exported with `python -m scripts.export_openapi`, not by
  hand.** A first attempt built the spec with an ad-hoc `get_openapi(...)` call and produced a
  **21915-insertion / 21894-deletion whole-file rewrite** — different indent, key order and
  escaping from whatever generated the committed spec. Reverted and re-exported through the
  project's own script (`frontend/scripts/check-openapi-drift.sh` names it), which yields a
  purely additive 27-line diff. This is D-8 of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md` recurring, and it will
  recur again for anyone who does not find the script first.
- **D-2** — **On Windows the codegen rewrites all ~280 api-client files with CRLF.**
  `git status` therefore lists the whole tree as modified after `gen:api`, while
  `git diff --numstat` correctly reports content changes in only `AdminService.ts` (+26, the
  new method). The line-ending-only churn was discarded with `git checkout -- ` over the
  codegen tree after staging the two genuine changes. Worth knowing before someone commits 280
  files of noise, or — worse — concludes the generator broke something.
- **D-3** — **Verified the concurrent build's work was not clobbered.**
  `agent-pack-install-report-fidelity` landed mid-task and owns
  `ExamplePackInstallReportOut.ts`; after the regeneration that file showed as modified but
  `git diff` reported no content change, and its `group_created` field is intact.
- **D-4** — **§7.3's truncation warning is replaced by an unresolved-row warning, under a new
  i18n key.** The two are in tension: Q-1 chose an **unbounded** route, which leaves the section
  no page limit to key a truncation warning on, and `admin.activities.truncated` reads
  "Showing the most recent {count}. More exist than are listed here." — a sentence that cannot be
  true of a response that carries everything. Keying it on a sentinel count of 200 would have
  been literal compliance and a lie in both directions: it would fire falsely at 200 genuine
  platform types and stay silent if the route were later paginated at any other limit. The
  section instead warns when an installed example's stored row is **absent from the listing** —
  which is exactly the condition that disables Edit, exactly what a future pagination of this
  route would cause, and the thing the admin actually needs told. New key
  `admin.activities.examples.storedRowUnavailable` in both locale files. User decision; AC-9
  amended above rather than silently reinterpreted.
- **D-5** — **§5's reseed mechanism is right about the watch and wrong about the trigger, and
  the first test written for it passed for the wrong reason.** The array-literal source really
  does defeat the change test exactly as §5 describes. But the trigger §5 names —
  `refetchOnWindowFocus` returning a row with identical contents — cannot reach it:
  vue-query's `structuralSharing` (on by default) returns the **previous** object for a
  deeply-equal refetch, so `props.row` never changes identity and the watcher's effect is never
  even triggered. §8.2 written literally therefore passed against the pre-fix code. What does
  reach the watch is a refetch whose contents **changed** — another admin's edit, or the refetch
  after this admin's own save — and there the pre-fix dialog reseeded, observed as
  `expected 'Mandala (edit 1)' to be 'half-typed name'`. Both cases are now tests: the
  identical-row one pins the property AC-5 states, the changed-row one is the regression. Worth
  knowing generally — this codebase already carries a memory about ref-array reactivity, and
  structural sharing is the mirror-image trap: it *suppresses* identity changes you were
  counting on to fire.
- **D-6** — **No behavioural verification (gate 4).** Docker is unavailable on this host and
  there is no local PostgreSQL, so the admin activities screen was never opened in a browser.
  Four user-visible behaviours changed: the card values, the disabled Edit button, the
  unresolved-row warning and the refused-save toast. jsdom asserts all four, but none has been
  seen. This is the sixth consecutive dossier in this area closed without a manual pass; confirm
  on the first deployed build.
- **D-7** — **§8.3's route test was missing from the backend half.** `35a0a47` added the route
  to the parametrized admin-gate list but not the test asserting what it returns, though §8.3
  asks for both and AC-7 gates on both. Added here: 250 rows come back whole with no cursor or
  limit reaching the facade, a platform row triggers no project-name lookup, and the four
  governance fields the form seeds from survive the response mapping. Easy to miss because the
  gate test's `ids=[...]` change *looks* like coverage.
- **D-8** — **`pnpm run check:openapi-drift` cannot run on this host**, and was verified another
  way. The script shells out to bare `python`, which is not on this machine's bash PATH
  (`scripts/check-openapi-drift.sh: line 22: python: command not found`); it fails before
  touching the tracked spec, so nothing was damaged. Substituted: the spec was re-exported with
  the venv interpreter via `python -m scripts.export_openapi` (D-1's rule) and compared against
  `backend/openapi.json` as **parsed JSON** — identical, no path added or removed. A byte
  comparison is useless here, since PowerShell's redirect adds a BOM and CRLF. `gen:api` was
  deliberately **not** re-run: no route changed this session, and D-2 records that it rewrites
  all ~280 api-client files with CRLF on Windows. CI runs the real gate.

## 12a. RESUME NOTE — CLOSED

*Kept for the record; every item below is now done. The frontend half landed in `4a4b3a5` and
the missing route test in `53bc1cb`. Read §12's D-4 through D-8 for what changed against the
plan on the way.*

Stopped deliberately at a green checkpoint, not blocked. **`main` is green**: the backend half
is committed and the frontend half was never started, so nothing is half-wired.

**Done and committed** (`35a0a47`):

- `GET /api/admin/platform-activity-types` in `app/api/v1/admin_activities.py`, `require_admin`,
  unbounded, returning `_type_out(at, project_name=None)` over the existing
  `ActivitiesFacade.list_platform_types()`. No new repository or facade method was needed.
- The route added to the parametrized admin-gate test
  (`tests/unit/test_admin_activities_routes.py`), which is the only thing pinning
  `require_admin` onto each handler and previously listed two routes.
- `backend/openapi.json` re-exported (additive, 27 lines) and
  `frontend/src/shared/api-client/services/AdminService.ts` regenerated (+26 lines,
  `listPlatformActivityTypesApiAdminPlatformActivityTypesGet`).

**Not started — the whole frontend half.** In dependency order:

1. `adminKeys.platformActivityTypes()` in `slices/admin/queries/index.ts` — a **new** key, per
   Q-2 deliberately *not* a variant of `activityTypes()`, which `AdminActivitiesView:209-212`
   shares; a scope-filtered fetch under that key would poison the governance table's cache.
2. An `adminApi` wrapper in `slices/admin/api/admin.ts`, pattern at `:137-141`.
3. `ActivityExamplesSection.vue`: resolve `editRow` (`:165-167`) from the new unbounded query
   instead of the 200-row page, and render **stored** values on the cards (Q-3) rather than the
   course file's.
4. Both §7.3 guards. The second is easy to skip and must not be: the dialog watch source
   (`PlatformActivityTypeDialog.vue:148`) is a fresh array literal every evaluation, so
   `Object.is` always reports "changed" and the id-keying its comment claims never gates
   anything — meaning `refetchOnWindowFocus` reseeds an open, dirty form **today**, independent
   of this task's headline defect. It needs a scalar source.
5. §7.4: `onSubmit`'s `props.row === null` branch toasts instead of returning silently, and Save
   is disabled in that state. **Keep the guard** — `mutationFn` (`:183-184`) falls back to
   `props.row?.id ?? ''`, which would PATCH an empty id.
6. The truncation warning, reusing the existing `admin.activities.truncated` key
   (`admin/locales/en.json:254`) — no new key needed.

**The two failing-first tests were written, confirmed failing for the documented reasons, then
reverted rather than committed** — committing red tests would break `main` for everyone, and
skipping them would rot. Recreate them from §8.1; both are fully specified there. Their observed
failures were `expected '' to be 'Mandala (renamed by an admin)'` (the blank form) and
`expected undefined to be defined` (no `disabled` attribute), with the other six passing. The
fixture that matters: the types listing returns 200 rows that do **not** contain the installed
type, and a second `stubPlatformTypes` helper stubs `/api/admin/platform-activity-types`.

**Still to run when resumed**: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`,
`pnpm run check:openapi-drift`, plus gates 5-7 over the whole task diff from base `1b45ebb`.

## 13. Follow-ups

- **FU-1**: The general shape - offer an action from one data source, resolve it from another
  that is bounded - is worth a sweep across the admin slice. This dossier fixes the one instance
  the audit found.
- **FU-2**: `adminApi.deletePlatformActivityType`
  (`frontend/src/slices/admin/api/admin.ts:183-184`) is wired to the generated client but **no
  component calls it**. The "remove an installed example" capability described at
  `AdminActivitiesView.vue:163-165` has no UI at all. Out of scope here; worth its own ticket,
  and note that its behaviour is the subject of
  `docs/tasks/2026-08-16-platform-type-delete-optin-lifecycle`.
- **FU-3**: `PlatformActivityTypeDialog.vue:164-180` is a hand copy of
  `frontend/src/slices/activities/composables/usePolicyRefusal.ts:26-53`, forced by the
  `admin` / `activities` slice boundary (`frontend/eslint.config.js` `SLICE_DEPS`). Correct under
  the current rules, but it means a fix to the refusal decoding has to be made twice. If a third
  copy is ever needed, the logic belongs in `@shared`.
- **FU-4**: The watch-source-as-array-literal pattern (§5) defeats Vue's change detection
  silently and its accompanying comment asserts a protection that does not hold. Worth grepping
  for `watch(() => [` across the frontend: any other instance has the same always-fires
  behaviour, which is usually harmless and occasionally exactly this defect. **Sharpened by
  D-5**: the sweep should look for the *changed-contents* trigger, not the refocus one — under
  vue-query's structural sharing a deeply-equal refetch never reaches the watcher at all, so an
  instance that looks harmless under an alt-tab test can still be live.
- **FU-5**: **The platform-types query has no retry affordance of its own.** When it errors the
  section now says so (D-4's warning), but the only remedy offered is "reload the page", while
  the examples query directly above it renders an `SQueryError` with a Retry button. The
  asymmetry is cosmetic today — the failure is rare and reloading works — but two adjacent
  failures on one screen should not be reported two different ways.
