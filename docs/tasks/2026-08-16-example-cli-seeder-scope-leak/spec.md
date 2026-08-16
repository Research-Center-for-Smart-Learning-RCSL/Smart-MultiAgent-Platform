---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.02, R30.28, R30.33]
depends_on: []
---

# The example CLI seeder treats opted-in platform types as the project's own, and seeds nothing

## 1. Summary

`python -m smap.examples creative-thinking-course --project-id P --owner-user-id U` is the
documented way to obtain a **project-scoped, owner-editable copy** of a shipped course. Since
migration 0076 it silently does nothing whenever project P has opted into the same course
platform-wide: the seeder's idempotency set is built from `ActivitiesFacade.list_types`, which
now returns the project's own types **unioned with** the platform types the project opted into,
so all four course keys read as already present. The command creates no rows, reports every key
as `already_present`, and exits 0. The operator is told the work succeeded while P holds only
read-only platform rows, which is precisely what invoking the CLI was meant to avoid.

Found as F-1 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`, by two
independent investigation lenses. This dossier also folds in that audit's F-14, three stale
operator-visible statements in the same two files, one of which asserts the very idempotency
rule this defect falsifies.

## 2. Observed vs Expected

**Observed.**

- `backend/smap/examples/_seeding.py:47` builds the idempotency set as
  `existing = {t.key for t in await facade.list_types(project_id)}` - a bare set of keys, with
  no `project_id` and no `scope` predicate.
- `ActivitiesFacade.list_types` (`backend/contexts/activities/interfaces/facade.py:307-308`)
  delegates to `ActivityTypeService.list_types`
  (`backend/contexts/activities/application/type_service.py:277-278`), which calls
  `ActivityTypeRepository.list_for_project`
  (`backend/contexts/activities/infrastructure/repositories/type_repo.py:187-217`).
- That repository method unions two populations: `project_id == project_id` **or**
  `id IN (SELECT activity_type_id FROM project_activity_type_optins WHERE project_id = ...)`
  (`type_repo.py:199-201`, `:208-211`). Its own docstring says so and labels itself a
  *presentation* filter (`:188-198`).
- The CLI and the platform installer read the same course file through the same loader
  (`backend/smap/examples/_catalogue.py:15-24` is a re-export shim of
  `contexts/activities/infrastructure/examples/catalogue`, which
  `backend/contexts/activities/application/example_service.py:178` also uses), so the key
  overlap is total, not partial: all four keys pinned at
  `backend/tests/unit/test_smap_examples_cli.py:72-77`.
- The command then reports success: `backend/smap/examples/__main__.py:92-97` logs
  `created=[] already_present=[all four]` at `logger.info` and returns exit code 0, textually
  indistinguishable from a legitimate idempotent re-run.

**Expected.** The seeder registers a project-scoped copy of every course type the project does
not already **own**. Intent sources, all three explicit:

- `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:63-64` (Non-goals): "The
  `smap.examples` CLI stays. It remains the seeding path for a project-scoped copy and for
  air-gapped operators; only the catalogue *parser* relocates."
- The same dossier's AC-12 (`:555-557`): the CLI "seeds project-scoped types as before".
- `docs/examples/creative-thinking-course.md:205-210`: admin install is the primary path, and
  "The `smap.examples` CLI remains available for installing a **project-scoped copy** instead."
- [R30.28] frames the seeder as an operator tool over repository data; [R30.02] and [R30.33]
  establish that a platform-scoped row is owned by the platform and is read-only to a Project
  Owner (`PlatformActivityTypeReadOnly`,
  `backend/contexts/activities/application/type_service.py:138-139`), so a platform row is not
  a substitute for the copy the operator asked for.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the seeder ask the ownership question: a new scope-correct read, a one-line filter at the call site, or register-and-catch-conflict? | **A new scope-correct read.** Add `ActivityTypeRepository.list_owned_by_project`, expose it through the service and facade as `list_owned_types`, and have the seeder call it. | User decision. The root cause is that the seeder asks "what does this project own?" using a method that answers "what may this project use?". A call-site filter fixes this one caller and leaves the mismatch latent for the next; naming the method after the question makes the misuse hard to repeat. Register-and-catch was rejected because it converts control flow into exception handling and a conflict poisons the transaction. |
| Q-2 | Fixing this makes two live types share one key inside P's usable set (F-5 of the audit) reachable through the documented CLI path rather than only by a deliberate owner action. What should the CLI do on collision? | **Create the copy, and warn loudly per colliding key.** The report gains a third field naming the keys that now shadow an opted-in platform type. | User decision. Creating is what the operator invoked the command to do, so refusing would block a legitimate case. But the collision has real consequences (the bundled plugin binds by key, `frontend/src/slices/activities/plugins/registry.ts:13-15`; workflow reactive rules match by key with no scope or id dimension, `backend/contexts/workflow/application/event_dispatch.py:86-116`), and an operator who is told about it can opt the project out. Silence would let them walk into F-5 unknowingly. |
| Q-3 | Should this dossier `depends_on` the F-5 dossier (`2026-08-16-activity-type-key-collision-across-scopes`)? | **No - `depends_on: []`.** Ship in parallel. | User decision. Zero file overlap: this task touches `backend/smap/examples/` plus one additive repository/service/facade method, while F-5's fix lands in `type_service.register` and the uniqueness rule. Neither is a logical prerequisite for the other. Recorded here because whichever lands second must re-verify its assumption about what `list_types` returns - see §9. |
| Q-4 | Does any unfinished dossier conflict? | **No.** | Scan of `docs/tasks/BOARD.md` plus a frontmatter check: the only non-terminal dossiers are `2026-07-07-graphrag-two-axis-redesign` (approved, graphrag) and `2026-07-19-large-artifacts-silently-dropped` (in-progress; `kernel.py` / `turn_engine.py` / `attachment_service.py`). Neither touches `smap/examples`, `contexts/activities`, or the activities repositories. The eleven sibling dossiers spawned by the same audit are listed in its Hand-off table; only F-5's shares a root cause, addressed in Q-3. |

## 4. Reproduction

Deterministic; no timing or concurrency involved.

**Preconditions.**

1. A deployment on migration 0076 or later.
2. A platform admin has installed the shipped course:
   `POST /api/admin/activity-examples/creative-thinking/install`, producing four
   platform-scoped types (`mandala-9grid`, `time-traveler-next-steps`,
   `emotion-desk-three-emotions`, `six-hats-emotion-desk`).
3. Project P's owner has opted P into all four:
   `POST /api/projects/P/activity-type-optins`.
4. P owns **no** activity type of its own under any of those four keys.

**Steps.**

```
python -m smap.examples creative-thinking-course --project-id <P> --owner-user-id <U>
```

**Actual.** Exit code 0, and the log line at `__main__.py:92-97` reads
`created=[] already_present=['mandala-9grid', 'time-traveler-next-steps',
'emotion-desk-three-emotions', 'six-hats-emotion-desk']`. `SELECT id, scope FROM activity_types
WHERE project_id = '<P>'` returns zero rows. Re-running never converges.

**Expected.** Four project-scoped rows created, reported as `created`, plus a warning naming
all four as shadowing an opted-in platform type.

**Partial-overlap variant.** If P opted into only two of the four, the seeder creates the other
two and skips those two, so the damage is silent and partial rather than total. This is the
commoner shape in practice and is the case AC-3 pins.

## 5. Root Cause Analysis

The causal chain, earliest link first:

1. **Root cause.** `backend/smap/examples/_seeding.py:47` uses
   `ActivitiesFacade.list_types(project_id)` as an *ownership* query. That method answers a
   different question - "which types may this project use" - and has done so since migration
   0076 widened `ActivityTypeRepository.list_for_project` (`type_repo.py:187-217`) to union
   opted-in platform rows. The repository docstring at `:188-198` states the widened contract
   and explicitly labels the method a presentation filter; the seeder was never updated to
   match. Correcting this link alone prevents the symptom.
2. The seeder discards the only two fields that would distinguish the populations: the
   comprehension at `:47` projects to `t.key`, dropping `ActivityType.project_id`
   (`backend/contexts/activities/domain/models.py:69`) and `ActivityType.scope` (`:92`), both of
   which the domain model carries precisely so this distinction can be made.
3. `_seeding.py:49-51` then treats set membership as "already present" and `continue`s, so no
   `register_type` call is made. `register_type`
   (`backend/contexts/activities/interfaces/facade.py:102-131`) takes a `project_id` and has no
   `scope` parameter, so the two populations were never interchangeable.
4. The report conflates the two outcomes: `SeedReport` (`_seeding.py:24-27`) has only `created`
   and `already_present`, and `__main__.py:92-97` logs both at `info` with exit 0, so a total
   no-op is indistinguishable from a successful re-run.

**Aggravating factor, not the cause.** The unit tier cannot observe any of this.
`backend/tests/unit/test_smap_examples_cli.py:62-66` doubles the facade with
`MagicMock(key=k)` objects that carry no `project_id` and no `scope` attribute at all, so every
existing assertion holds identically before and after the defect was introduced. AC-12 of the
platform-example dossier was verified by "`test_smap_cli_contract.py` passes unmodified", and
that file contains no reference to `list_types`, `project_id`, or `scope`. This is why the
regression test in §8 must fix the doubles before it can fail.

**Why `list_for_project` is not the root cause and must not change.** Its widening is correct
and deliberate for its other caller, the HTTP listing at
`backend/app/api/v1/activities.py:433`, which needs exactly the usable set to drive the
facilitator's picker. `backend/tests/unit/test_activity_repos.py:266` pins that behaviour by
name (`test_list_for_project_admits_platform_types_only_through_an_optin`). The fix adds a
second question rather than changing the answer to the existing one.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- Every operator invocation of the documented CLI path against a project that has opted into any
  shipped example. Silent, with a success exit code and no error log.
- No data is corrupted and nothing is deleted; the defect is a failure to write. Recovery after
  the fix is simply re-running the command, which will then converge.
- Air-gapped operators are the population the Non-goals section named explicitly, and are the
  most likely to have no admin-install path available as an alternative.
- Not reachable from any HTTP route: the CLI is the only caller with this expectation.

**Sibling suspects.** Every other consumer of a "does this already exist" read in the example
subsystem, each checked:

| Site | Verdict | Evidence |
|---|---|---|
| `ActivityExampleService.install_course` idempotency | **cleared** | Uses `list_platform_by_keys` (`type_repo.py:241-258`), which filters `project_id IS NULL` and `deleted_at IS NULL`. Correctly scoped to the platform population it writes into. |
| `AgentExampleService.install_pack` idempotency | **cleared** | Uses `AgentRepository.list_for_project` (`backend/contexts/agents/infrastructure/repositories.py:222-244`) at `backend/contexts/agents/application/example_service.py:223`. Agents have no platform scope at all (§4.2 of the agent-packs dossier), so there is no second population to leak in. |
| `AgentExampleService.list_catalogue` installed flag | **cleared** | Same repository method, same reasoning (`example_service.py:139`). |
| `ActivityExampleService.list_for_project` | **cleared** | Reads `optin_repo.list_for_project` (`example_service.py:237`) to compute `enabled`, which is the opt-in question and is answered by the opt-in table directly. |
| `app/api/v1/activities.py:433` (HTTP type listing) | **cleared, and must stay as-is** | Wants the usable set. Changing it would remove opted-in platform types from the facilitator's picker, breaking [R30.33]. |
| `app/bootstrap/seed.py` | **cleared** | Does not touch `activity_types`; its `list_for_project` calls (`:175`, `:186`) are workspaces and agents. |

**Systemic reading.** This is the only site in the tree that asks an ownership question through
a usable-set method, so the fix is one call site plus the missing API - not a sweep. The reason
it is nonetheless worth a named method rather than an inline filter is recorded in Q-1.

## 7. Fix Design

Four changes, the first three of which are the fix and the fourth of which is F-14.

**7.1 A scope-correct read (new, additive).**

- `backend/contexts/activities/infrastructure/repositories/type_repo.py` gains
  `list_owned_by_project(project_id)`: `_TYPE_COLS` selected `WHERE project_id = :pid AND
  deleted_at IS NULL`, ordered `created_at DESC, id DESC` for consistency with its neighbours.
  Structurally this is `list_for_project` minus the opt-in arm, i.e. the pre-0076 semantics,
  and it mirrors `list_platform` (`:219-239`) in shape. Its docstring must state what it is
  **not**: the usable set, which is `list_for_project`.
- `backend/contexts/activities/application/type_service.py` gains
  `list_owned_types(project_id)`, a one-line delegation beside `list_types` (`:277-278`).
- `backend/contexts/activities/interfaces/facade.py` gains `list_owned_types(project_id)`
  beside `list_types` (`:307-308`).

This does not alter `list_for_project`, `list_types`, or any existing caller. No migration, no
schema change, no API contract change, so no `gen:api` run.

**7.2 The seeder asks both questions, each by name.**

`backend/smap/examples/_seeding.py:47` becomes two reads, because the fix and the Q-2 warning
need different answers:

- `owned = {t.key for t in await facade.list_owned_types(project_id)}` drives idempotency.
- `usable = await facade.list_types(project_id)` supplies the collision warning; the shadowing
  set is the course keys present among the platform-scoped entries of `usable`
  (`t.scope is ActivityTypeScope.PLATFORM`).

Two queries in a command that runs once per deployment is not a cost worth optimising, and each
read's name states the question it answers, which is the whole point of Q-1.

**7.3 The report distinguishes the third outcome (Q-2).**

`SeedReport` (`_seeding.py:24-27`) gains `shadowed_by_platform: list[str]`: keys this run
created that now coexist with an opted-in platform type of the same key.
`__main__.py:92-97` logs it, and logs it at `logger.warning` rather than `logger.info` when
non-empty, naming the consequence (the bundled plugin and any workflow reactive rule match by
key and cannot tell the two apart) and the remedy (opt the project out of the platform type, or
use a different key). Exit code stays 0: this is a warning about a state the operator asked
for, not a failure.

**7.4 The three stale statements (F-14).**

- `backend/smap/examples/__main__.py:43-44` - "the two-unit creative-thinking course" naming two
  types. The course has declared four since the 2026-08-13 dossier. Rewrite to describe the four
  types without hard-coding the list a second time, so the next course edit cannot re-stale it.
- `backend/smap/examples/__main__.py:49-51` - "a type whose key already exists is left
  untouched". This is the sentence the defect falsifies; it must become "a type whose key this
  project already **owns**".
- `backend/smap/examples/_seeding.py:6-7` - the module docstring states the same rule and needs
  the same correction.
- `backend/smap/examples/__init__.py:14` - still documents `courses/*.json` as living in that
  package; it moved to `contexts/activities/infrastructure/examples/`. Note `__main__.py:46-47`
  already points at the correct path, so this is the only stale copy.

**Data repair.** None required. The defect wrote nothing, so no bad rows exist. Affected
projects are repaired by re-running the command after the fix.

## 8. Regression Test Plan

The failing test comes first. It cannot be written against the current doubles, so fixing them
is part of the test change.

**8.1 The blindness must be removed first.**
`backend/tests/unit/test_smap_examples_cli.py:62-66` builds facade doubles as
`MagicMock(key=k)`. Replace `_facade` with a builder that constructs rows carrying a real
`key`, `project_id` and `scope`, so the doubles can express the difference between an owned row
and an opted-in platform row at all. Every existing test in the file keeps its current meaning
by passing project-owned rows.

**8.2 The failing test.**
New, in the same file: `TestOptedInPlatformTypesAreNotOwnership`.

- **Arrange**: `list_types` returns four rows with `project_id=None`,
  `scope=ActivityTypeScope.PLATFORM` and the four course keys; `list_owned_types` returns `[]`.
- **Assert**: `report.created == COURSE_KEYS`, `report.already_present == []`, and
  `facade.register_type` was awaited four times with `project_id=P`.
- **Why it fails today**: current `_seeding` calls `list_types`, which returns all four keys, so
  `existing` contains every key, nothing is registered, and `report.created` is `[]` while the
  assertion expects four. It fails on the assertion, not on an error, which is the right failure
  mode. `list_owned_types` is simply never called by the current code.

**8.3 Companion cases**, same file:

- Partial overlap: P owns two keys, is opted into the other two. Expect exactly the two
  opted-in-but-unowned keys created and the two owned keys in `already_present`. This is the
  §4 variant and is the case a call-site filter would also have to get right.
- Genuine idempotency is preserved: P owns all four. Expect `created == []` and
  `already_present == COURSE_KEYS`, i.e. the behaviour the fix must **not** change.
- Q-2 warning: with the §8.2 arrangement, `report.shadowed_by_platform == COURSE_KEYS`; with no
  opt-ins at all it is `[]`.

**8.4 Repository level.**
`backend/tests/unit/test_activity_repos.py` gains a compiled-SQL test for
`list_owned_by_project` in the idiom of `:266`, asserting the statement carries the
`project_id` equality and the `deleted_at IS NULL` predicate and **no** opt-in subquery. Per
`backend/CLAUDE.md` the unit tier renders `literal_binds` and cannot see a real constraint, but
this query uses no PostgreSQL-specific function or operator, so no `db`-tier test is required -
unlike the queries that motivated that rule.

**8.5 Contract test.**
`backend/tests/unit/test_smap_cli_contract.py` must keep passing unmodified; it pins the CLI's
invocation surface, which this change does not alter.

## 9. Risks and Rollback

- **The fix makes F-5 easy to reach (Q-2).** After this lands, seeding into an opted-in project
  produces two live types sharing a key in that project's usable set. This is the correct
  outcome for the operator's request and is exactly what the Q-2 warning exists to surface, but
  until `2026-08-16-activity-type-key-collision-across-scopes` lands, the workflow and plugin
  ambiguity is real. Mitigated by the warning naming the consequence rather than only the fact.
- **Cross-dossier assumption.** F-5's fix may change what `register_type` accepts when a key
  collides across scopes. If F-5 lands first and chooses to refuse such a registration, this
  seeder will raise instead of warning, and §7.3's exit-0 promise needs revisiting. Whichever
  lands second re-verifies the other's assumption; recorded in Q-3 rather than encoded as
  `depends_on`, because the file sets do not overlap.
- **A new facade method is public API for other contexts.** `list_owned_types` invites use, and
  a caller that wants the usable set could pick the wrong one, which is the mirror of today's
  defect. Mitigated by both docstrings naming the other method explicitly.
- **Low blast radius otherwise**: additive repository/service/facade methods, one changed call
  site, one dataclass field, and four comment corrections. No migration, no HTTP contract, no
  frontend.
- **Rollback**: `git revert` the commit. The new methods have exactly one caller, so nothing
  outside `smap/examples` depends on them; reverting restores the previous (defective) skip
  behaviour and writes nothing.

## 10. Acceptance Criteria

- [x] **AC-1**: The regression test from §8.2 fails against current code (on its `created`
  assertion) and passes after the fix.
- [x] **AC-2**: With project P opted into all four shipped platform types and owning none,
  the CLI creates four project-scoped rows, reports all four in `created` and none in
  `already_present`, and exits 0.
- [x] **AC-3**: With P owning two of the four keys and opted into the other two, exactly the two
  unowned keys are created and the two owned keys are reported `already_present` (§4's partial
  variant).
- [x] **AC-4**: Genuine idempotency is unchanged: with P owning all four keys, a re-run creates
  nothing and reports all four `already_present`.
- [x] **AC-5**: Every key created that shares its key with an opted-in platform type appears in
  `SeedReport.shadowed_by_platform` and is logged at `warning` level naming both the consequence
  and the remedy; the list is empty and no warning is emitted when the project has no opt-ins.
- [x] **AC-6**: `ActivityTypeRepository.list_owned_by_project` returns only rows with
  `project_id = <project>` and `deleted_at IS NULL`, with no opt-in arm, pinned by the
  compiled-SQL test in §8.4.
- [x] **AC-7**: `list_for_project`, `list_types`, and the HTTP listing at
  `app/api/v1/activities.py:433` are behaviourally unchanged;
  `test_activity_repos.py::test_list_for_project_admits_platform_types_only_through_an_optin`
  passes unmodified, as does `test_smap_cli_contract.py`.
- [x] **AC-8**: The four stale statements of §7.4 are corrected, and
  `python -m smap.examples creative-thinking-course --help` describes the four-type course and
  states the idempotency rule in terms of ownership.
- [x] **AC-9**: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`
  (unit tier locally; `db`/`integration`/`wiring` on CI, which is authoritative per the
  project's remote-CI rule).

## 11. SRS Delta

**None.** This restores behaviour [R30.28] and the platform-example dossier already document;
it defines nothing new. [R30.02] and [R30.33] are cited as the reason a platform row is not a
substitute for an owned copy, and neither needs amending.

Noted for the F-5 dossier rather than acted on here: [R30.02] states key uniqueness per scope,
and is silent on uniqueness within a project's *usable* set. That silence is F-5's subject, not
this one's.

## 12. Deviation Log

- **D-1** — **The local venv's ruff had to be upgraded to run the lint gate.** It sat at
  0.7.4 while `pyproject.toml`'s `[tool.ruff.lint]` selects `UP047`, which only the 0.16 line
  understands, so `ruff check` failed to parse the config before evaluating any rule. Upgraded
  to 0.16.3, inside the declared `ruff>=0.7,<0.17` range. No repository file changed. Recorded
  because it is the same class of trap as D-8 of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:785-791`: a gate that is
  reproducible only against a *resolved* dependency set, not a pinned one.
- **D-2** — **The `--help` text was written with markdown emphasis and had to be de-marked.**
  Typer renders the docstring straight to the terminal, so `**owns**` displayed as literal
  asterisks. Not a spec deviation in substance; recorded because it was caught by the
  behavioural gate (running the command) and by nothing else — no test asserts the rendered
  help.
- **D-3** — **The full unit tier was verified under the suite's normal ordering, not under
  forced deterministic ordering.** `pytest -q tests/unit` passes 6748 with 6 skipped. A run
  with `-p no:randomly` surfaced one failure in `tests/unit/test_agent_example_service.py`,
  which was traced to **concurrent uncommitted work for a different dossier**
  (`2026-08-16-agent-pack-install-report-fidelity`, since committed as `6999830`) present in
  the same working tree — its failing-first `group_created` assertions. With that work parked,
  the module passed 22/22 deterministically. Nothing in this task's diff is implicated. See
  FU-4.

## 13. Follow-ups

- **FU-1**: `backend/tests/unit/test_smap_examples_cli.py`'s facade doubles were
  `MagicMock(key=...)` - attribute-free mocks that silently satisfy any projection. §8.1 fixes
  this file, but the same pattern is worth a sweep: any test double built from a bare
  `MagicMock` with only the attributes the current implementation happens to read cannot fail
  when the implementation starts reading a different one. This is the mechanism that let AC-12
  be verified green against defective code.
- **FU-4**: **Two pre-existing quality findings in touched code**, from the gate-5 audit, both
  worsened by exactly one instance here and neither blocking. (a) `type_repo.py` now has six
  read methods repeating `sa.select(*_TYPE_COLS).where(...).order_by(created_at DESC, id DESC)`;
  a private `_select_types(*where)` helper would collapse them. (b)
  `contexts/activities/interfaces/facade.py` carries 37 public async methods, well past the ~20
  facade calibration; splitting by subdomain (types / activations / sessions / submissions /
  examples / policy) is the shape.
- **FU-5**: **The unit tier has a latent inter-module order dependency.** It passes under the
  suite's normal random ordering but a specific deterministic ordering can surface failures
  (D-3). That is the same class D-6 of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md` recorded for the validator
  registry. A CI job pinning one seed, or running `-p no:randomly` on a clean tree, would say
  whether any genuine dependency remains once concurrent work is excluded.
- **FU-2**: `SeedReport` is logged as a Python repr into a loguru template
  (`__main__.py:92-97`). With a third field this is getting hard to read on a terminal; a small
  formatter would make the three outcomes legible without changing the data.
- **FU-3**: The seeder still cannot express "replace my project's copy with the current course
  file", which is the same re-sync question left open as OQ-1 of the platform-example dossier
  and OQ-2 of the agent-packs dossier. Out of scope here; worth resolving once for courses,
  packs, and this path together.
