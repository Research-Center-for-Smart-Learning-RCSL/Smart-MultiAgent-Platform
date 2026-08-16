---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.02, R30.09, R30.28, R30.33]
depends_on: []
---

# Two live activity types can share one key inside a project's usable set, and every key-based consumer resolves both

## 1. Summary

Since migration 0076, a project's usable set is the union of the types it owns and the platform
types it opted into. The two partial-unique indexes that guard `key` do not span that union, so a
project can hold its own `mandala-9grid` while opted into the platform `mandala-9grid`. Both rows
pass the tenancy gate independently and both are dispatchable by id - but every consumer that
resolves a type by its **key string** rather than its id sees one identifier and two answers:
workflow reactive rules fire for both, the bundled plugin renders both, the async validator
envelope cannot tell them apart, and the agent-facing activity block labels both identically.

The condition requires a deliberate owner action today. It stops being deliberate once
`docs/tasks/2026-08-16-example-cli-seeder-scope-leak/` lands, because the documented CLI path
then creates exactly this state.

F-5 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`. **Unusually for a
bugfix, this dossier carries an SRS Delta** - see §11.

## 2. Observed vs Expected

**Observed - the two indexes do not overlap.**

- `backend/alembic/versions/0049_activities.py:78-79` - `(project_id, key) WHERE deleted_at IS NULL`
- `backend/alembic/versions/0076_platform_activity_types.py:68-70` - `(key) WHERE project_id IS NULL AND deleted_at IS NULL`

Neither spans the project-versus-platform pair, and `0076`'s own docstring (`:14-19`) records the
NULL-distinctness reasoning that made the second index necessary.

**Observed - no guard anywhere above the database.**
`ActivityTypeService.register` (`backend/contexts/activities/application/type_service.py:38-79`)
validates schema, validator config and policy, then calls `_repo.create` (`:68`). The route
(`backend/app/api/v1/activities.py:309-335`) adds an owner check and an MCP binding check. No key
lookup on either path.

**Observed - the reverse direction is equally open.**
`ActivityExampleService.opt_in`
(`backend/contexts/activities/application/example_service.py:240-278`) does exactly two things
before writing: load the type and refuse it unless it is a live platform row (`:255-257`), then
`optin_repo.add` with `ON CONFLICT DO NOTHING` (`:259-263`). **There is no key read at all.** So a
project that already owns `foo` may opt into a platform `foo` with one click - cheaper to reach
than authoring a type. The only refusal pinned by tests is scope
(`backend/tests/unit/test_activity_examples_service.py:305-320`).

**Observed - what resolves by key rather than id.**

| Consumer | Evidence | Behaviour under collision |
|---|---|---|
| Workflow reactive rules | `backend/contexts/workflow/application/event_dispatch.py:86-116` (`matches_activity` compares `chatroom_id`, `activity_type_key`, `validation_status` - no id, no scope), fed from `backend/contexts/activities/application/submission_service.py:250` and `:337-341` via `backend/app/workers/tasks/workflow_signals.py:336-354` | **Identical signals.** Every rule fires for both types. |
| Agent activity context | `backend/contexts/activities/infrastructure/repositories/submission_repo.py:270` projects `key AS type_key`; `backend/contexts/activities/application/activity_context_provider.py:84` renders it into the block given to every agent | Two different activities appear under one label. |
| Async validator envelope | `backend/app/workers/tasks/activities.py:58` sends `{"payload": …, "activity_type_key": …}` | A remote validator cannot tell which type it is scoring. Bites only the project row, since platform types must be `in_process` (`example_service.py:180-191`). |
| Frontend plugin registry | `frontend/src/slices/activities/plugins/registry.ts:7`, `:13-14`; `frontend/src/slices/activities/components/ActivityHost.vue:44` | Both resolve to the same plugin. |
| Facilitator picker | `frontend/src/slices/activities/components/ActivityPanel.vue:44` - `value: t.id` (correct), `label: t.name` alone | Two indistinguishable entries if the names match. |
| Agent pack `binds_activity_types` | `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:20-24`, `:44-48`, `:68-72` | Advisory only (`agents.py:460`: "installing binds no room, so nothing enforces it"), so a misleading catalogue label rather than a wrong binding. |

**Mitigating facts, established during research and worth recording so severity is not
overstated.** `ActivityHost.vue:45-47` prefers `plugin.schema ?? activityType.payload_schema`,
and the bundled mandala plugin declares **no** `schema`
(`frontend/src/slices/activities/plugins/mandala9grid/index.ts:17-23`), so each row renders its
own schema; and `MandalaGrid.vue:28-35` degrades to a single column for any schema that is not
nine fields. The plugin arm is therefore presentation-only and degrades gracefully. The workflow
arm is the real defect surface.

**Expected.** Either the collision is prevented, or it is permitted and every surface that
presents or selects by key can distinguish the two. The user chose the second (Q-1).

**Intent source.** [R30.02] states key uniqueness *per scope* and is **silent** on uniqueness
within a project's usable set - which is the heart of this dossier and the reason §11 is not
"None". `frontend/src/slices/activities/plugins/mandala9grid/index.ts:1-7` and
`docs/examples/creative-thinking-course.md:312-314` document the one-plugin-per-key limitation on
the stated premise that "`ActivityType.key` is unique only per project"; the 0076 widening
falsified that premise.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should a project be allowed to author a type whose key matches a platform type it opted into? | **Yes - permitted, with the collision made visible.** | User decision. Refusing at `register` is technically the cleanest fix and needs no new SQL, but it would **overturn an already-approved decision**: `docs/tasks/2026-08-16-example-cli-seeder-scope-leak/spec.md:73` (Q-2) records the user choosing "create the copy, and warn loudly per colliding key", because "creating is what the operator invoked the command to do, so refusing would block a legitimate case". The CLI seeder reaches `register` through `facade.register_type` (`_seeding.py:52`), so a refusal there would make that approved AC-2/AC-5 unbuildable. Permitting is also what the platform already does; this dossier makes it honest rather than accidental. |
| Q-2 | Why not globally unique keys? | **Not safely writable.** Rejected. | `python -m smap.examples creative-thinking-course --project-id <any>` registers four fixed keys into **any** project, so two projects each owning `mandala-9grid` is the designed outcome of the documented operator path, not an edge case. The codebase says so at `backend/tests/unit/test_activity_repos.py:294-295`: "Without the NULL project guard the install idempotency check would see another tenant's identically-keyed project type and skip the install." A `UNIQUE (key) WHERE deleted_at IS NULL` migration would fail on any deployment where the seeder ran for more than one project, and the only resolutions would be renaming another tenant's keys (breaking their plugin binding and their rules) or refusing the upgrade. |
| Q-3 | For workflow rules: rewrite existing rules to pin a type id, or add an optional scope filter? | **Optional additive filter. Do not rewrite stored rules.** | User decision (the "matches both is acceptable for existing rules" arm). A breaking key-to-id change is defeated by state a migration cannot reach: `backend/contexts/workflow/application/executors/wait_for_event.py:63-75` copies `"match": dict(config)` into Redis at `wf:wait:{run_id}:{node_id}` with a TTL up to 86400s, so every in-flight `activity_in_room` wait would silently break. Rule configs live in `workflows.definition` JSONB (`backend/contexts/workflow/infrastructure/tables.py:28`, read at `event_dispatch.py:212-226`) - there is no relational rule table to migrate either. Crucially, both `trigger_config` (`docs/workflow.schema.json:179-184`) and `wait_for_event_config` (`:344-350`) **lack `additionalProperties: false`**, so an optional field is schema-legal today and leaves every existing rule matching exactly as it does now. |
| Q-4 | Should `opt_in` also guard, or only `register`? | **Both, symmetrically - warn, do not refuse.** | Not a user question once Q-1 is settled: a fix that only touches `register` leaves the cheaper door wide open (§2), and `opt_in` is a one-click UI action. Symmetry also keeps the rule statable in one sentence for §11's delta. |
| Q-5 | Does this need an SRS Delta? | **Yes.** | Not a user question - it follows from the analysis. Both sentences of [R30.02] are *satisfied* by the current code; what is wrong is that the requirement is silent on the union [R30.33] creates. There is no stated intent to restore, so whatever this dossier decides it is **establishing** a rule. That is true of the permissive answer as much as the restrictive one: today a reader of [R30.02] cannot determine which is true. The sibling dossier flagged this at `spec.md:339-341`. |
| Q-6 | Does any unfinished dossier conflict? | **No `depends_on`, two to coordinate.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches this area. `2026-08-16-example-cli-seeder-scope-leak` (approved) shares a root cause but no files - it changes `smap/examples/` plus an additive repository method, while this changes `type_service`, `example_service` and the workflow signal. `2026-08-16-activities-install-error-contract` edits `type_service.register`'s pre-flight region on adjacent lines. Rebase rather than sequence in both cases. |

## 4. Reproduction

**Direction A - author over an opted-in platform key.**

1. A platform admin installs `creative-thinking`, producing platform type `mandala-9grid`.
2. Alice, owner of project P, opts P in.
3. Alice `POST /api/projects/P/activity-types` with `key: "mandala-9grid"` and her own schema.

**Actual.** The insert succeeds - the project index is `(P, key)` and the platform index requires
`project_id IS NULL`, so neither is violated. `GET /projects/P/activity-types` returns two rows
with the same key. A workflow rule matching `{"activity_type_key": "mandala-9grid"}` fires for
submissions to either. The facilitator's picker shows two entries distinguished only by `name`.

**Direction B - opt in over a key you already own.** Reverse steps 2 and 3. Same end state,
reached with one click and no guard at all (`example_service.py:240-278` performs no key read).

**Expected after this fix.** Both remain possible, but Alice is warned at the point of each
action, the picker and the type list distinguish the two, and a new workflow rule can pin scope.

## 5. Root Cause Analysis

1. **Root cause.** `type_repo.list_for_project` (`type_repo.py:187-217`) was widened to union two
   populations while `key` remained unique only *within* each. Its docstring at `:191-193` says
   "no de-duplication is needed", which is true of the row **shape** and false of the **key**.
   Nothing above it re-establishes uniqueness over the union, and nothing below it needs to.
2. Every consumer listed in §2 predates the widening and was written when
   `frontend/src/slices/activities/plugins/mandala9grid/index.ts:1-7`'s premise - "`ActivityType.key`
   is unique only per project" - was true. Those consumers are not individually wrong; they were
   correct against the older invariant.
3. **[R30.02] was amended by the platform-example dossier to add the scope concept but not to say
   what happens to the union.** `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:604-613`
   drafted the amendment covering per-scope uniqueness; the usable-set question was never asked,
   which is why §11 exists.

**Why no test caught it.** Nothing pins the current behaviour as intended, in either direction: no
test asserts a project may register a key already live on a platform type, and none asserts a
project may opt into a colliding platform type. Every `register()` test uses the placeholder key
`"k"` against a fully mocked repository. The one test touching cross-scope key semantics,
`test_activity_repos.py:289-296` (`test_list_platform_by_keys_is_scoped_to_platform_rows`),
**presupposes** cross-scope collisions are legal without asserting anything about them.

**Related coverage gap worth naming**: `uq_activity_types_project_key_active`
(`0049_activities.py:78-79`) has **zero** tests, and the `type_repo.py:111-114` arm that maps it
to `ActivityTypeKeyConflict` is untested - only the platform arm is
(`test_activity_repos.py:347-374`).

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Any project that both opts into a platform example and authors a type under the
same key. Today that needs a deliberate act; after
`2026-08-16-example-cli-seeder-scope-leak` lands it is what the documented CLI path produces, so
the population grows from "someone who did this on purpose" to "any operator following the
walkthrough". That change in reachability, not the state itself, is what makes this worth fixing
now.

Severity is bounded by the mitigating facts in §2: activation is by id and is correct; the plugin
arm is presentation-only and degrades; only the workflow arm produces a genuinely wrong outcome
(a rule firing for a submission it was not written for).

**Sibling suspects** - other identifiers assumed unique within a project's usable set:

| Site | Verdict |
|---|---|
| `list_platform_by_keys` (`type_repo.py:241-258`) | **cleared** - filters `project_id IS NULL`, so the install idempotency read never crosses scopes. This is the guard `test_activity_repos.py:294-295` documents. |
| `example_service.install_course` idempotency (`:193-201`) | **cleared** - keyed on the platform population only. |
| Agent names (`uq_agents_project_name_active`, `backend/alembic/versions/0011_agents.py:103-105`) | **cleared** - agents have no platform scope, so no union exists. |
| Agent group names (`uq_agent_groups_project_name_active`, `0043_graphrag_owner.py:52-55`) | **cleared** - same reason. |
| `smap/examples/_seeding.py:47` | **confirmed, and owned elsewhere** - the same root cause, fixed by the approved sibling dossier. |

**Systemic reading.** `activity_types` is the only entity in the tree with two scopes, so it is
the only place this union exists. The fix is therefore specific, not a sweep - but §11's rule is
what a future second dual-scope entity would need to follow.

## 7. Fix Design

Four parts. None prevents the collision; together they make it impossible to hit unknowingly and
possible to disambiguate where it matters.

**7.1 Warn at both doors (Q-1, Q-4).**

- `type_service.register` gains a pre-flight read: when registering a **project-scoped** type
  (guarded on `scope is PROJECT` / `project_id is not None`, so the platform-install path at
  `example_service.py:204-218` is unaffected), check whether the project has opted into a live
  platform type with this key. Composed from two existing methods - `list_platform_by_keys([key])`
  (`type_repo.py:241-258`) then `optin_repo.exists(...)` (`optin_repo.py:47-64`) - so no new SQL.
  `ActivityTypeService.__init__` (`type_service.py:32-36`) gains
  `ProjectActivityTypeOptInRepository` as a collaborator; `example_service.py:114` and
  `submission_service.py:64` already construct it from the same layer, so the idiom is
  established. Place the check **after** the policy gate (`:63-67`) so existing
  early-rejection test ordering is preserved.
- `example_service.opt_in` gains the mirror check against the project's own live types.
- **Both warn rather than refuse.** The mechanism is a response field plus a UI notice, not an
  exception - a new error class would tempt a future caller into treating it as a refusal, and
  `frontend/src/slices/activities/components/ActivityTypeForm.vue:347-351` maps *any* non-policy
  409 to "An activity type with this key already exists", so a new 409 slug would silently
  inherit a message describing a different situation.

**7.2 Make it visible where a human chooses (Q-1).**

- The facilitator picker (`ActivityPanel.vue:44`) labels by `name` alone; it must distinguish
  platform from project rows. Reuse the badge idiom already in the slice:
  `frontend/src/slices/activities/views/ActivityTypesView.vue:50-55` (`isPlatform(row) => row.scope === 'platform'`)
  and `:223-230` (the `SBadge` render). The data is already present - `_type_out`
  (`app/api/v1/activities.py:200-214`) emits both `key` and `scope`.
- `ActivityTypeForm` surfaces the §7.1 warning before submit. The form already holds the types
  list, so this needs no additional API call.

**7.3 Let a new workflow rule disambiguate (Q-3).**

- `_assemble_activity_signal` (`submission_service.py:396-428`) emits `activity_type_key` at
  `:416`; add `activity_type_id` and `activity_type_scope` alongside it, following the docstring's
  established "always present, never `None`" discipline so an SEL rule can dereference them
  safely.
- `matches_activity` (`event_dispatch.py:86-116`) gains an **optional** `activity_type_scope`
  filter: absent means match either, exactly as today.
- `docs/workflow.schema.json:231-243` and `:379-392` gain the optional property. Both parent
  objects lack `additionalProperties: false`, so this is additive and no stored rule changes
  meaning.
- `frontend/src/slices/workflow/components/config/TriggerConfigForm.vue:265-276` takes
  `activity_type_key` as free text with no validation; it gains the optional scope selector.
  Turning the free-text field into a real picker is out of scope - recorded as FU-2.

**7.4 Correct the documentation premise.**
`docs/examples/creative-thinking-course.md:312-314` states the one-plugin-per-key limitation on
the premise that keys are unique per project. That premise is false; the paragraph must say that a
project's usable set can hold two types under one key, and that the plugin binds to the key rather
than to either row.
`frontend/src/slices/activities/plugins/mandala9grid/index.ts:1-7` carries the same premise in a
comment and needs the same correction.

**Why this does not mask the symptom.** The symptom is one identifier resolving to two rows. The
fix does not remove the ambiguity - Q-1 and Q-2 explain why removing it is either forbidden by an
approved decision or unsafe to migrate - it removes every case where the ambiguity is
*undetectable*: the user is warned when creating it, sees it in every list that presents both, and
can pin scope on any rule written from now on. What remains, and is stated plainly in §9, is that
an **already-stored** rule naming only `activity_type_key` still matches both.

**Data repair.** None. No stored data is wrong; the fix adds fields and warnings.

## 8. Regression Test Plan

**8.1 The failing tests - the warnings.** New tests asserting that `register` returns the
collision warning when the project has opted into a live platform type with that key, and that
`opt_in` returns it when the project already owns that key. Both fail today: neither path performs
a key read.

Note the harness consequence: several `register` tests build `ActivityTypeService(MagicMock())`
and then replace `_repo` with a bare `MagicMock`
(`backend/tests/unit/test_activity_policy_enforcement.py:73-76`), so awaiting a new
`list_platform_by_keys` raises `TypeError`. Affected sites to update:
`test_activity_policy_enforcement.py:111`, `test_activities_services.py:191`, `:233`, `:277`.
Scope-conditioning the check (§7.1) keeps `test_activity_examples_service.py:137`, `:164`, `:194`
untouched, and **that the platform-install path is unaffected is itself an assertion worth
making**.

**8.2 The negative cases.** No warning when the project has not opted in; no warning when the keys
differ; no warning on the platform-install path.

**8.3 Workflow signal and matcher.** Assert the signal now carries `activity_type_id` and
`activity_type_scope`; assert `matches_activity` with no scope filter behaves **identically** to
today (`backend/tests/unit/test_workflow_signals.py:150-165`, `TestMatchesActivity` - every
existing case must pass unchanged, which is the point of making the filter optional); assert a
scope-filtered rule matches only the intended row.

**8.4 Frontend.** The picker distinguishes a platform row from a project row of the same key and
name; the authoring form surfaces the warning before submit.

**8.5 The coverage gap this exposes (§5).** Add the missing test for
`uq_activity_types_project_key_active` and its `ActivityTypeKeyConflict` mapping
(`type_repo.py:111-114`), currently untested. Not strictly required by this fix, but it is the
sibling of the platform arm the fix relies on, and it was found while establishing the blast
radius.

**8.6 Must stay green.** `test_activity_repos.py:266-276` (the union query),
`test_platform_activity_type_schema.py:160-171` (platform key conflict), and every existing
`TestMatchesActivity` case.

## 9. Risks and Rollback

- **The fix does not prevent the collision.** Stated plainly because it is the central trade of
  Q-1: two rows still coexist, and an **already-stored** workflow rule that names only
  `activity_type_key` still matches both. The additive filter helps only rules written after the
  fix. An operator with existing `activity_event` rules and a colliding key must edit those rules
  by hand.
- **Interaction with the approved sibling.** `2026-08-16-example-cli-seeder-scope-leak` makes this
  state reachable through the documented CLI path. That dossier's Q-2 already commits to warning
  per colliding key, so the two warnings must not contradict each other in wording. Whichever
  lands second should re-read the other's warning text.
- **A new collaborator on `ActivityTypeService`.** Adding `ProjectActivityTypeOptInRepository`
  widens a constructor used in many tests (§8.1). Mechanical, but it is the largest source of
  churn in this change.
- **Schema additions to `workflow.schema.json`.** Additive and legal because both parent objects
  omit `additionalProperties: false` - but that omission is itself load-bearing and worth a
  comment, since adding the constraint later would retroactively invalidate these fields.
- **Rollback**: `git revert`. The signal fields are additive and unread by old rules; the warnings
  are advisory. Nothing depends on the new behaviour.

## 10. Acceptance Criteria

- [x] AC-1: The tests from §8.1 fail before the fix and pass after: authoring a type whose key
  matches an opted-in platform type produces a warning, and the type **is still created**.
  *`TestCrossScopeKeyCollisionWarning::test_it_warns_and_still_creates_the_type` asserts both
  halves (`shadowed_by_platform is True` **and** `_repo.create.assert_awaited_once()`);
  confirmed failing with the collision read stubbed out. The route half is
  `TestRegisterResponseRelaysTheCollisionWarning`, and the form half is
  `ActivityTypeForm.test.ts`'s "never blocks the submit".*
- [x] AC-2: Opting into a platform type whose key the project already owns produces the mirror
  warning, and the opt-in **still succeeds**.
  *`test_opting_into_a_key_the_project_already_owns_warns_and_succeeds`, plus
  `test_a_repeat_optin_still_reports_a_standing_collision` (see D-3). Route:
  `test_opt_in_relays_the_collision_warning`. UI: `ExampleImportDialog.test.ts`'s
  "warns when enabling shadows a key the project already owns".*
- [x] AC-3: Neither path raises, and no new 409 slug is introduced (§7.1).
  *`test_no_new_error_slug_is_raised`; `contexts/activities/domain/errors.py` gains exactly one
  class in the whole task diff (`ExampleCourseNotFound`, a 404 belonging to the sibling
  dossier), and `error_mapping.py` gains exactly one row.*
- [x] AC-4: The platform-install path (`example_service.install_course` calling `register` with
  `project_id=None`) is unaffected and produces no warning.
  *`test_the_platform_install_path_is_unaffected` asserts the check is not merely falsy but
  **not reached**: `list_platform_by_keys.assert_not_awaited()` and
  `optin_repo.exists.assert_not_awaited()`. `test_activity_examples_service.py`'s four
  pre-existing `TestInstallCourse` tests are untouched, which is the same claim from the
  other side.*
- [x] AC-5: The facilitator picker and the project type list distinguish a platform row from a
  project row sharing a key and a name, using `scope` rather than a null `project_id`.
  *Picker: `ActivityPanel.vue:44-58` + `ActivityPanel.test.ts`'s
  `TestMatchesActivityScope`-equivalent block, which uses a pair sharing **both** key and name.
  The type list already carried the `scope === 'platform'` badge
  (`ActivityTypesView.vue:53-55`, `:223-231`) and needed no change - see D-2.*
- [x] AC-6: The activity signal carries `activity_type_id` and `activity_type_scope`, both always
  present.
  *Three tests in `TestBuildActivitySignal`, including the vanished-type case where both degrade
  to `""`/the submission's own id rather than to `None`.*
- [x] AC-7: `matches_activity` with no scope filter behaves identically to today - every existing
  `TestMatchesActivity` case passes unmodified - and a scope-filtered rule matches only the
  intended row.
  *`TestMatchesActivity._m` still calls the matcher without the new keyword, and all 11 of its
  cases are unmodified in the diff. `TestMatchesActivityScope` adds five cases including the
  no-scope-on-the-signal case (D-4).*
- [x] AC-8: `docs/examples/creative-thinking-course.md:312-314` and
  `plugins/mandala9grid/index.ts:1-7` no longer claim keys are unique per project.
- [x] AC-9: The SRS Delta in §11 is applied to `REQUIREMENTS.md` on approval.
  *Verified already applied at `REQUIREMENTS.md:2161`; the appended text matches §11 verbatim.*
- [x] AC-10: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`,
  `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm run gen:api` +
  `pnpm run check:openapi-drift` (the signal payload and any response field change the contract),
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`.
  *ruff, mypy, `pnpm lint`, `pnpm typecheck`, `pnpm test` (181 files / 1119 tests) and
  `pnpm build` all green. `check:boundaries-enforced`, `check:type-coverage` (98.57%) and
  `check:bundle-size` all pass. `gen:api` rerun and its 4 genuinely-changed files committed
  (D-5); `check:openapi-drift` itself cannot execute here (D-7) but its assertion was verified
  by hand - re-exporting the spec and re-running `gen:api` leaves both trees byte-identical to
  what is committed. `pytest -q`: unit tier green, other tiers unrunnable - see D-6.*

## 11. SRS Delta

**Required** - see Q-5. This is the atypical bugfix: the code matches [R30.02] as written, and
what is wrong is that [R30.02] is silent on the union [R30.33] creates. Whatever this dossier
decides, it establishes a rule rather than restoring one, so the requirement must say which.

Apply verbatim on approval.

**Amend [R30.02]** (append, leaving the existing text intact):

> Key uniqueness is per scope, not per project's usable set. A project may author a type whose
> `key` matches a platform-scoped type it has opted into ([R30.33]); the platform permits this
> because a project-scoped copy of a shipped example is a supported outcome ([R30.28]). Both
> actions warn the acting Project Owner, and neither is refused. `scope` is the disambiguator:
> any surface that presents both populations together must distinguish them, and any rule,
> plugin, or validator that selects a type by `key` alone selects both.

No amendment to [R30.33], [R30.09] or [R30.28] is required; they are cited because the amended
sentence depends on them.

## 12. Deviation Log

- **D-1**: §7.2 states "`ActivityTypeForm` surfaces the §7.1 warning before submit. The form
  already holds the types list, so this needs no additional API call." **The premise is false**:
  `ActivityTypeForm.vue` takes only `projectId` and `editType`; the list lives in
  `ActivityTypesView.vue:63`. The conclusion survives - it is passed down as a new optional
  `existingTypes` prop rather than re-fetched, so there is still no additional API call - but
  the mechanism differs from the spec's description. The prop is optional so the admin slice's
  sibling dialog is unaffected.
- **D-2**: AC-5 asks that "the facilitator picker **and the project type list**" distinguish the
  two rows. The type list already did: `ActivityTypesView.vue:53-55` defines
  `isPlatform(row) => row.scope === 'platform'` and `:223-231` renders the `SBadge`, which the
  spec itself cites at §7.2 as the idiom to reuse. Only the picker needed changing. Recorded so
  a reader does not look for a diff that is correctly absent.
- **D-3**: The warning `opt_in` returns describes **state, not this call's effect**. The natural
  reading of AC-2 would have returned the flag only when the opt-in actually inserted a row, but
  `optin_repo.add` uses `ON CONFLICT DO NOTHING` and a repeat opt-in returns early. Reporting
  nothing on the second click would read as though the collision had been resolved, so the flag
  is computed before the write and returned on both arms. Pinned by
  `test_a_repeat_optin_still_reports_a_standing_collision`. This matches the reasoning
  `smap/examples/_seeding.py:68-71` already applies to its own `shadowed_by_platform` report,
  which §9 asked the two warnings to agree on - and the field name is deliberately identical.
- **D-4**: `matches_activity`'s new parameter defaults to `""`, and a scope-filtered rule
  **does not match** a signal carrying `""`. This case is not in §8.3 but is reachable: a signal
  enqueued before this change and still parked in `wf:wait:{run_id}:{node_id}` (TTL up to
  86400s) has no `activity_type_scope`. Refusing rather than guessing is the conservative arm -
  a rule that asked for `project` never fires on a row whose scope is unknown - and it is
  pinned by `test_a_scoped_rule_does_not_match_a_signal_carrying_no_scope`. Only rules written
  after this change can be scope-filtered at all, so no stored rule is affected either way.
- **D-5**: `pnpm run gen:api` rewrote all ~280 api-client files with CRLF on this Windows host
  while only 4 genuinely changed (`index.ts`, `services/ActivitiesService.ts`, and two new
  models). Git's own line-ending normalization collapses the noise on `git add`, so the commit
  contains exactly the 4. This is the same trap `2026-08-16-admin-platform-type-edit-unreachable`
  recorded; also note `python -m scripts.export_openapi` writes to **stdout**, so the spec must
  be redirected to `backend/openapi.json` rather than written in place.
- **D-6**: **`pytest -q` was not run to completion on the full suite** - identical cause and
  evidence to D-2 of the sibling `2026-08-16-activities-install-error-contract` dossier: the
  `integration`/`wiring`/`db` tiers need a live PostgreSQL and fail at connect with
  `socket.gaierror: [Errno 11001] getaddrinfo failed`. The `unit` tier, which holds every test
  this dossier writes or modifies, is green. CI is authoritative.
- **D-7**: `pnpm run check:openapi-drift` could not execute: it is a bash script and the
  available `bash.exe` (WSL) has no `python` on its PATH. Its assertion was verified by hand
  instead - re-exporting the spec and re-running `gen:api` leaves `backend/openapi.json` and
  `frontend/src/shared/api-client/` byte-identical to the committed state. The script would also
  be a false positive on this host anyway, since its `git status --porcelain` check sees the
  CRLF churn of D-5.
- **D-8**: **No behavioural verification was performed.** This dossier changes four user-visible
  surfaces - the facilitator picker's labels, a pre-submit notice in the authoring form, two
  warning toasts, and a new selector in the trigger config form - and none has been seen in a
  browser. The app needs PostgreSQL, Redis and Vault; Docker is unavailable on this host (same
  cause as D-6), and a frontend-only dev server cannot exercise any of it because every one of
  these paths is driven by a server response. jsdom covers the logic (which label renders, which
  toast fires, on which flag) but not the appearance or placement. Confirm on the first deployed
  build. The most likely thing to be wrong is cosmetic: the picker suffix lengthening an option
  past the select's width, or the form notice sitting oddly between the key and name fields.
- **D-9**: Three Info-severity quality findings were raised against this change; one was fixed
  (`ExampleImportDialog.vue:60` now optional-chains the response, so a frontend running against
  a pre-change backend that still answers 204 gets no toast rather than a `TypeError`), and two
  were routed to FU-6 and FU-7. The security audit found nothing at any severity across all 13
  dimensions, with AuthZ traced end-to-end for all three modified endpoints.

## 13. Follow-ups

- **FU-1**: Existing workflow rules cannot be disambiguated retroactively (§9). A one-off report
  listing rules whose `activity_type_key` currently matches more than one live type in the
  workflow's project would tell an operator exactly which rules to edit. Cheap to write once the
  signal carries scope.
- **FU-2**: `TriggerConfigForm.vue:265-276` takes `activity_type_key` as **free text** with no
  validation against real types, so a typo produces a rule that silently never fires. That is a
  defect in its own right, independent of this collision, and is the reason a scope selector is
  the most this dossier can add. A real picker fed by the project's type listing would fix both.
- **FU-3**: ~~`uq_activity_types_project_key_active` has no test at all, and neither does the
  `type_repo.py:111-114` arm mapping it to `ActivityTypeKeyConflict`.~~ **Closed in this
  change** - `test_create_maps_the_project_key_index_to_a_domain_conflict`
  (`test_activity_repos.py`), which also states in its docstring what the project index does
  and does not constrain, since that silence is what let the cross-scope question go unasked.
- **FU-4**: `activity_context_provider.py:84` renders `type_key` into the block every agent
  reads, so two colliding types are indistinguishable to an LLM. Emitting the type `name`, or the
  scope alongside the key, would fix it - but it changes the prompt input of every existing
  deployment, which is the same reason FU-3/FU-13 of the agent-packs dossier deferred the digest
  format. Worth resolving with those, not separately.
- **FU-5**: `docs/workflow.schema.json`'s `trigger_config` and `wait_for_event_config` omit
  `additionalProperties: false`. This fix depends on that omission being intentional. If it is
  not, adding the constraint later would retroactively invalidate the optional fields added
  here - worth deciding deliberately. **Partially addressed**: both `$defs` now carry a
  `$comment` saying the omission is load-bearing and why, so the next reader has to decide
  rather than discover. The decision itself is still open.
- **FU-6**: `WaitForEventConfigForm.vue:22`'s `EVENT_TYPES` omits `activity_in_room` entirely,
  so a wait kind the backend dispatcher, the JSON Schema and `matches_activity` all support has
  **no authoring UI at all**. This change added `activity_type_scope` to `wait_for_event_config`
  for parity with the trigger, but neither it nor the pre-existing `activity_type_key` is
  reachable from the builder. Pre-existing and wider than this dossier; belongs with FU-2's
  picker work.
- **FU-7**: The `role="note"` inline advisory block (warning border + heroicon + one line of
  text) is now written out longhand at five sites: `ActivityTypeForm.vue`,
  `ExampleImportDialog.vue`, and three times in `agents/components/AgentPackInstallDialog.vue`.
  It is deliberately distinct from `SAlert` (`role="alert"`, assertive and transient), so the
  answer is a second small component - `SNotice` - rather than reusing that one.
- **FU-8**: `example_service.opt_in`'s mirror check reads the project's whole owned-type list to
  answer a single-key question, while its register-side twin uses two index-narrow reads. Kept
  as-is because it matches how `smap/examples/_seeding.py:60` answers the identical question and
  the population is small, but the asymmetry is worth closing if a project's type count ever
  grows.
