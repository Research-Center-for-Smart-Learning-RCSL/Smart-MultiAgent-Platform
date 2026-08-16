---
type: bugfix
status: draft
created: 2026-08-16
requirements: [R30.02, R30.27, R30.32]
depends_on: []
---

# An unknown course key returns 500, and `min_filled` is never checked against the schema it scores

## 1. Summary

Two defects in the activities validation-and-error contract, both of which let a client-side
mistake become either a server error or a permanently broken activity.

- **F-6**: `POST /api/admin/activity-examples/{course_key}/install` with a mistyped or unknown
  key returns **500**, not 404. `CourseFileInvalid` is a bare `ValueError`, so the context's
  error handler never sees it and it falls through to the global catch-all, logged as an
  unhandled exception with a stack trace. The sibling agent-pack route does the same job
  correctly.
- **F-7**: the `schema_config_validator` hook exists, is registered, and is called by the course
  loader - but never by `type_service`. So `POST /api/projects/{id}/activity-types` accepts
  `min_filled: 99` on a three-property schema, and every subsequent submission fails
  `too_few_filled` forever. Recorded as FU-5 of
  `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:727-733`, which called closing
  it "a three-line change"; that estimate is accurate for the body.

Both are F-6 and F-7 of
`docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`. Grouped because they
are the same layer, the same two write paths, and the same test files.

## 2. Observed vs Expected

### F-6 - unknown course key returns 500

**Observed.** `CourseFileInvalid` is the only error class in the course catalogue
(`backend/contexts/activities/infrastructure/examples/catalogue.py:66`,
`class CourseFileInvalid(ValueError)`), and it is raised for six distinct causes, only two of
which are the client's fault:

| Cause | Line | Fault |
|---|---|---|
| bad key shape / traversal attempt | `catalogue.py:290-293` | client |
| no such course in the catalogue | `:298-300` | client, or a packaging failure |
| file not UTF-8 | `:311` | server (bad artifact) |
| file unreadable | `:313` | server |
| invalid JSON | `:318` | server |
| schema / validator / field violation | via `_fail`, `:102-103` | server |

`install_course` loads unconditionally with no pre-check
(`backend/contexts/activities/application/example_service.py:178`), the route adds no guard
(`backend/app/api/v1/admin_activities.py:368-393`; `FPath(..., max_length=_MAX_COURSE_KEY)` at
`:370`, no `pattern=`), and `_MAP` has no row for it
(`backend/contexts/activities/interfaces/error_mapping.py:15-98`). The handler is registered on
`errors.ActivitiesError` (`:114-115`), and `ActivitiesError(Exception)`
(`backend/contexts/activities/domain/errors.py:10-11`) is not a `ValueError`, so no MRO path
rescues it. The response is produced by `_unhandled_handler`
(`backend/shared_kernel/errors/handlers.py:124-133`, registered `:141`): **500**, title
"Internal Server Error", plus a logged stack trace. Reproduced empirically during the audit.

**Expected.** 404 with a distinct RFC 7807 code for an unknown key; a malformed *shipped* file
remains a 500, because that is a defect in the deployed artifact and reporting it as "not found"
sends an operator looking in the wrong place.

**Intent source.** The agent-pack route, which is the exemplar this one should have followed.
`backend/contexts/agents/application/example_service.py:193-197` pre-checks `available_packs()`
and raises the mapped `AgentPackNotFound`, whose own docstring
(`backend/contexts/agents/domain/errors.py:47-55`) states the distinction verbatim. [R30.32]
requires the catalogue to be "validated on read - so a malformed course file is diagnosed rather
than installed"; a 500 with no detail is not a diagnosis.

### F-7 - `min_filled` unchecked at the API

**Observed.** The hook exists (`backend/app/plugins/activity_validators.py:132-153`,
`validate_filled_count_against_schema`), is registered
(`:168`, via `schema_config_validator=`), and is reachable
(`backend/contexts/activities/application/validators/registry.py:100-108`). Its **only**
production caller is the course loader (`catalogue.py:185-187`); a repo-wide grep for
`get_schema_config_validator` returns the definition, `__all__`, `catalogue.py:35` and `:185`,
and tests.

`ActivityTypeService._validate_validator_config`
(`backend/contexts/activities/application/type_service.py:315-344`) is a `@staticmethod` taking
`(kind, config)` only, so it **structurally cannot** cross-check against `payload_schema`. It
reaches `get_config_validator` (`:324-326`) and nothing else, and it is the sole validator gate
for `register` (`:60`) and `update` (`:150`). The API request models declare both fields as
`dict[str, Any]` with no validators (`backend/app/api/v1/activities.py:69-77`, `:80-91`).

So a three-property schema with `{"validator_id": "filled_count", "min_filled": 99}` is
accepted: `validate_filled_count_config` (`activity_validators.py:119-129`) checks only that the
value is a non-negative int. Every submission then computes `filled <= 3 < 99` and returns
`is_valid=False, error_class="too_few_filled"` (`:109-116`).

**Expected.** Registration and update refuse a `min_filled` above the declared property count,
with the same 422 every other validator-config refusal produces. The frontend already enforces
this and its comment names the backend gap
(`frontend/src/slices/activities/types/schemas.ts:138-149`).

**Intent source.** [R30.27] defines `filled_count` as a completeness measure; a threshold no
submission can reach measures nothing. FU-5 of the platform-example dossier.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | For F-6: pre-check the catalogue, or promote `CourseFileInvalid` to an `ActivitiesError`? | **Pre-check**, mirroring the agent path exactly. Do not promote. | Not a user question. Promoting would map all six causes in the table above to one status, turning a broken deployment artifact into a 404 - the opposite of what [R30.32] asks for. It would also put a domain error class in `infrastructure/`, the wrong layer. The agents context made this call deliberately: `PackFileInvalid` is *also* a bare `ValueError` and *also* unmapped (`backend/contexts/agents/infrastructure/examples/catalogue.py:77`), and only the not-found case was lifted into the domain. |
| Q-2 | Should the 404 body carry the loader's "available: none" diagnosis, or just the key like the agents path? | **Carry the diagnosis.** | The route is `require_admin`-gated, so the audience is a platform admin who is entitled to know which courses shipped. The loader already composes the string (`catalogue.py:300`) and a bare-key 404 throws it away - which matters most in the realistic packaging-failure case, where "available: none" is the whole diagnosis. Deliberately diverging from the agents path by one field; recorded so the divergence is intentional rather than drift. |
| Q-3 | For F-7: split `_validate_validator_config` into two methods, or add a parameter it ignores for two of three kinds? | **Add a keyword-only `payload_schema` parameter.** | Not a user question. Splitting means two call-site edits at each of `type_service.py:60` and `:150` and doubles the chance a future write path wires up one and forgets the other. The parameter is unused by the `WEBHOOK` and `MCP` branches (`:327-344`), which is a small structural cost paid once, against a correctness risk paid on every future change. |
| Q-4 | Should `_validate_validator_config` stay a `@staticmethod`? | **Yes.** | Not a user question. It touches no instance state, only the module-level registry, and `backend/tests/unit/test_smap_examples_cli.py:154` and `:162` call it unbound off the class. Making it an instance method breaks those for no gain. |
| Q-5 | Does closing F-7 break anything already stored? | **No, and no data migration is needed.** | Verified four ways in §6. The decisive one: `update` runs the validator gate only inside `if behavioral_changed:` (`type_service.py:143-152`), so a stored violating type still accepts a rename, a retention change, or a governance-flag flip. |
| Q-6 | Should an operator diagnostic be added for pre-existing violating rows? | **No.** | Such a row can only exist if someone bypassed the UI, it is already silently unpassable, and the fix stops new ones. Building a one-off report for a population that may be empty is not justified; the query is recorded in FU-2 for anyone who wants it. |
| Q-7 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches `contexts/activities`. Among the sibling dossiers from this audit, `2026-08-16-activity-type-key-collision-across-scopes` (F-5) also edits `type_service.register`'s pre-flight region. Different concern, adjacent lines; rebase rather than sequence, and see that dossier's §9. |

## 4. Reproduction

**F-6.** As a platform admin:
`POST /api/admin/activity-examples/creative-thinkin/install` (typo), or
`/Creative-Thinking/install` (uppercase, rejected by the anchored key regex at
`catalogue.py:290-293` before any filesystem access). Both return **500** with
`title: "Internal Server Error"` and are logged as unhandled exceptions. A key longer than 128
characters is the one sub-case that behaves correctly, rejected as a 422 by
`FPath(max_length=...)` before reaching the service.

**F-7.** As a Project Owner, `POST /api/projects/{pid}/activity-types` with
`payload_schema` declaring three string properties and
`validator_config: {"validator_id": "filled_count", "min_filled": 99}`. The type is created.
Activate it, submit a fully completed form, and observe `is_valid=False`,
`error_class="too_few_filled"`. No submission can ever pass. `PATCH` on an existing type has the
identical hole.

Both are deterministic and need no special data.

## 5. Root Cause Analysis

**F-6.** One link. `install_course` (`example_service.py:178`) calls the loader without first
asking whether the key names a shipped course, so a client-supplied key reaches a function whose
error type is not part of the context's error contract. The sibling `list_catalogue` shows the
author was aware of the distinction - it wraps each load in
`except CourseFileInvalid: logger.warning(...); continue` (`:129-134`) so a bad file is skipped
and logged - but the install path, which is the only one taking a key from a client, has no
guard at all.

**F-7.** Also one link, and it is a signature rather than a missing call.
`_validate_validator_config` never receives `payload_schema`, so no amount of logic inside it
could perform the cross-check; the hook that exists for exactly this purpose is unreachable from
the only gate on the write paths. Both `register` (`:44`) and `update` (`:108`) have the schema
in scope and have already validated it (`:59`, `:149`) by the time the gate runs, so the data is
available and simply not passed.

**Why F-7 shipped despite the hook being written for it.** The hook was added by the
platform-example dossier to let the *course loader* enforce a rule the config validator could
not express, and that dossier recorded closing the API-side gap as FU-5 rather than doing it
(`spec.md:727-733`). It was correctly identified and deliberately deferred; this dossier closes
it.

## 6. Blast Radius and Sibling Suspects

**F-6 blast radius.** The admin install endpoint only, behind `require_admin`. Near-unreachable
through the UI, which offers only valid keys; reachable by any script. The traversal guard
itself holds (`_COURSE_KEY_RE` is anchored and runs before any filesystem access), and the 500
body leaks nothing. The real costs are a wrong status on a public API contract and observability
pollution: every admin typo emits a stack trace logged as an unhandled exception, which is the
signal operators page on.

**F-7 blast radius.** Any project whose owner calls the API directly. Self-inflicted and
project-scoped; no cross-tenant or privilege dimension. The UI blocks it
(`schemas.ts:138-149`) and the course loader blocks it (`catalogue.py:185-187`), so the
population is API-only clients.

**Sibling suspects - F-6.** Every other route that reads a catalogue, each checked:

| Route | Verdict |
|---|---|
| `GET /api/admin/activity-examples` (`admin_activities.py:339-365`) | **cleared** - keys come from the directory listing, so a bad key is unreachable, and a bad file is caught and skipped (`example_service.py:129-134`). |
| `GET /api/projects/{id}/activity-examples` (`activities.py:463-489`) | **cleared by construction** - never touches the catalogue; reads `list_platform()` plus `list_for_project()` (`example_service.py:229-238`). |
| `GET /api/projects/{id}/example-packs` (`agents.py:516-555`) | **cleared** - `except PackFileInvalid: ... continue` (`backend/contexts/agents/application/example_service.py:135-137`). |
| `POST /api/projects/{id}/example-packs/{pack_key}/install` (`agents.py:558-603`) | **cleared - this is the exemplar** (`example_service.py:193-196`). |

`POST /api/admin/activity-examples/{course_key}/install` is the sole hole in the tree, confirmed
by grepping every `load_course` and `available_courses` call site: outside tests and the CLI the
only callers are `example_service.py:58` and `:129`, and the CLI already catches
`CourseFileInvalid` (`backend/smap/examples/__main__.py:63-71`).

**Sibling suspects - F-7.** Every write path that accepts a `validator_config`:

| Site | Verdict |
|---|---|
| `type_service.register` (`:60`) | **confirmed** |
| `type_service.update` (`:150`) | **confirmed** |
| `type_service.update_platform_type` (`:193-275`) | **cleared, and load-bearing for Q-5** - it forwards `existing.payload_schema` and `existing.validator_config` unchanged (`:239-241`) and validates only the policy, by design (AC-8 of the platform-example dossier limits admin edits to four fields). It cannot introduce a violation. |
| The course loader (`catalogue.py:185-187`) | **cleared** - already calls the hook; this is where the rule works today. |

**Q-5's evidence, in full.** (a) The shipped course is compliant, and guaranteed so rather than
lucky, because the loader already runs the hook: `mandala-9grid` 4/9,
`time-traveler-next-steps` 1/1 (equal is allowed - the check uses `>`),
`emotion-desk-three-emotions` 2/6, `six-hats-emotion-desk` 3/6. (b) No test fixture registers a
violating type through `register`/`update`. (c) A stored violating type still accepts
metadata-only edits, because the gate runs only when `payload_schema`, `validator_kind` or
`validator_config` actually change (`type_service.py:143-152`). (d) A *narrowing* edit to such a
type is refused unless `min_filled` is lowered in the same request - which is correct, and which
the UI already enforces client-side.

## 7. Fix Design

### 7.1 F-6

- New domain error in `backend/contexts/activities/domain/errors.py`:
  `ExampleCourseNotFound(ActivitiesError)` with code `activities/example-course-not-found`,
  modelled on `AgentPackNotFound` including its docstring's reasoning about why a malformed
  shipped file is a different thing. **It must be added to `__all__`** - note the pre-existing
  omission recorded as FU-3 of the platform-example dossier, where three policy errors are
  already missing (`errors.py:116-131`); adding a fourth silently would deepen it. Fix the
  omission here or record it explicitly; see FU-1.
- New `_MAP` row following the shape at `error_mapping.py:93-97`:
  `("activities/example-course-not-found", 404, "No shipped course with that key")`.
- In `example_service.install_course`, before `_load_cached` (`:178`): raise
  `ExampleCourseNotFound` when `course_key not in available_courses()`, carrying the loader's
  diagnosis string per Q-2. `available_courses` is already imported at `:35`.
- The route needs no `pattern=` after this - the pre-check subsumes it, exactly as the agents
  route relies on `install_pack`'s check (`agents.py:562`, `Path(..., max_length=...)` with no
  pattern).

### 7.2 F-7

- `_validate_validator_config` gains a keyword-only `payload_schema: dict[str, Any]` (Q-3),
  stays a `@staticmethod` (Q-4), and gains three lines inside its `IN_PROCESS` branch after
  `:324-326`, mirroring `catalogue.py:185-187`: fetch `get_schema_config_validator(vid)` and,
  when present, call it with `(config, payload_schema)`.
- Four call sites, all of which have the schema in scope: `type_service.py:60` (`register`, the
  parameter is at `:44`), `type_service.py:150` (`update`, `:108`), and
  `backend/tests/unit/test_smap_examples_cli.py:154` and `:162`. No others exist.
- No new error and no new mapping: `validate_filled_count_against_schema` raises
  `ValidatorConfigInvalid` (`backend/contexts/activities/domain/errors.py:58-62`), already
  mapped to `("activities/validator-config-invalid", 422, ...)` (`error_mapping.py:61-65`).
  **The resulting status is 422 on both write paths**, identical to every other validator-config
  refusal, so no frontend error-handling change is required.

**Why neither masks a symptom.** F-6's symptom is a wrong status and its cause is an unmapped
error type reaching a client-supplied input; the pre-check removes the input from that path
entirely while leaving genuine artifact failures loud. F-7's symptom is an unpassable activity
and its cause is a gate that cannot see the data it must compare against; passing the data is
the fix, not a workaround.

**Data repair.** None for either (Q-5, Q-6).

## 8. Regression Test Plan

**8.1 F-6 failing test.** In `backend/tests/unit/test_activity_examples_service.py`, class
`TestInstallCourse`: assert `install_course` with an unknown key raises `ExampleCourseNotFound`.
Fails today - it raises `CourseFileInvalid`.

**8.2 F-6, an existing test that must change, deliberately.**
`test_a_traversal_course_key_never_reaches_the_filesystem` (`:207-215`) currently asserts
`CourseFileInvalid` with `match="not a valid course key"`. After the pre-check the traversal key
is simply not in `available_courses()`, so it must assert `ExampleCourseNotFound` - mirroring
`backend/tests/unit/test_agent_example_service.py:294-299` exactly. **The traversal guard itself
must still be asserted**: the test's point is that the filesystem is never touched, and that
must remain provable.

**8.3 F-6 route level.** In `backend/tests/unit/test_admin_activities_routes.py`, assert the
mapped status is 404, following the patch style at `:401`.

**8.4 F-7 failing tests.** In `backend/tests/unit/test_activities_services.py`, beside
`test_in_process_filled_count_bad_config_rejected` (`:310`): a `register` with
`min_filled: 99` against the single-property `_SCHEMA` must raise `ValidatorConfigInvalid`
matching `min_filled`, **and** `_repo.create` must not be awaited. Its `update` twin, modelled
on `test_edit_to_bad_filled_count_config_rejected` (`:331-362`), asserting
`_repo.update.assert_not_awaited()`. Both fail today because the type is created.

**8.5 F-7's migration-risk assertion, which is a passing guard rather than a regression test.**
A metadata-only edit to a stored violating type still succeeds. This pins the
`behavioral_changed` gate (`type_service.py:143-152`) that makes Q-5's answer true, so that a
future refactor of `update` cannot silently make stored violating types uneditable.

**8.6 Signature update.** `test_smap_examples_cli.py:154` and `:162` call
`_validate_validator_config` unbound and must pass `course_type.payload_schema`. This is a
mechanical change, but it is also the check that Q-4's staticmethod decision holds.

**8.7 Must stay green unmodified.** `TestFilledCountSchemaConfigValidator`
(`test_activities_services.py:536-584`), which already covers the hook directly, and every
existing `register`/`update` config test.

## 9. Risks and Rollback

- **F-7 narrows what the API accepts.** Any API client currently registering an
  over-threshold `min_filled` starts getting a 422. That is the point, and §6's evidence shows
  no shipped content, fixture, or UI path produces one - but it is a contract narrowing and
  belongs in release notes rather than being discovered.
- **F-6 changes a status code from 500 to 404.** Strictly an improvement, and no client can
  reasonably depend on the 500.
- **The `__all__` omission.** `errors.py` already omits three error classes from `__all__`;
  adding a fourth without noticing deepens a known problem (FU-3 of the platform-example
  dossier). AC-3 makes this explicit.
- **Adjacent-line contention** with `2026-08-16-activity-type-key-collision-across-scopes` in
  `type_service.register`'s pre-flight region (Q-7). Different concerns; rebase.
- **Rollback**: `git revert` per commit. The two halves are independently revertable - F-6
  touches the example service and error map, F-7 touches the type service - so they should be
  separate commits even though they share a dossier.

## 10. Acceptance Criteria

- [ ] AC-1: The test from §8.1 fails before the fix and passes after; an unknown or malformed
  `course_key` on the admin install route returns **404** with code
  `activities/example-course-not-found`, and no unhandled exception is logged.
- [ ] AC-2: A course file that exists but does not parse still produces a 500 and is still
  logged as a server fault - the not-found lift must not swallow artifact failures.
- [ ] AC-3: `ExampleCourseNotFound` is in `errors.__all__`, and the three pre-existing omissions
  are either fixed in the same change or recorded explicitly in the deviation log.
- [ ] AC-4: The 404 detail names the available course keys, including the "none" case (Q-2).
- [ ] AC-5: The traversal guard still prevents any filesystem access for a key containing path
  separators or `..`, still asserted by §8.2's rewritten test.
- [ ] AC-6: The tests from §8.4 fail before the fix and pass after: `register` and `update` both
  refuse `min_filled` above the declared property count with **422**
  `activities/validator-config-invalid`, and neither writes.
- [ ] AC-7: `min_filled` equal to the property count is still accepted (the check uses `>`).
- [ ] AC-8: A metadata-only edit to a stored violating type still succeeds (§8.5).
- [ ] AC-9: `_validate_validator_config` is still a `@staticmethod` and all four call sites pass
  `payload_schema`.
- [ ] AC-10: The shipped course still installs unchanged, and
  `TestFilledCountSchemaConfigValidator` passes unmodified.
- [ ] AC-11: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`.
  No `gen:api` run is required - no response model changes shape.

## 11. SRS Delta

**None.** [R30.32] already requires the catalogue to be "validated on read - so a malformed
course file is diagnosed rather than installed", which the F-6 fix serves by distinguishing the
two failure kinds. [R30.27] already defines `filled_count` as a completeness measure, which the
F-7 fix makes enforceable at the boundary where the type is created rather than only where a
course file is parsed. Neither requirement changes.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: `backend/contexts/activities/domain/errors.py`'s `__all__` (`:116-131`) omits
  `ActivityTypeViolatesPolicy`, `ActivityPolicyVersionMismatch` and `ActivityPolicyInconsistent`
  - FU-3 of the platform-example dossier, still open. AC-3 forces a decision rather than another
  silent addition.
- **FU-2**: No diagnostic exists for a pre-existing activity type whose `min_filled` exceeds its
  property count (Q-6). Such a type is silently unpassable. The query an operator can run is a
  join of `activity_types` against the declared property count of its `payload_schema`; recorded
  here rather than built, since the population may well be empty.
- **FU-3**: `CourseFileInvalid` covers six causes across two fault domains (§2's table). Splitting
  it into a client-fault and a server-fault error would make the catalogue's own error surface
  as clear as the route's is about to become. The same is true of `PackFileInvalid`
  (`backend/contexts/agents/infrastructure/examples/catalogue.py:77`).
- **FU-4**: `validate_filled_count_against_schema` returns early rather than raising when
  `min_filled` is a bool or a non-int (`activity_validators.py:147-148`), deferring to
  `validate_filled_count_config` so the client-facing message stays stable. Correct, but it
  means the hook silently no-ops for a class of bad input, which is worth a comment at the call
  site so a reader does not assume it is the only gate.
