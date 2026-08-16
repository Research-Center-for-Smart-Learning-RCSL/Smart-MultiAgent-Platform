---
type: audit
status: reviewed
created: 2026-08-16
requirements: [R9.02, R28.04, R30.02, R30.09, R30.23, R30.25, R30.27, R30.28, R30.30, R30.31, R30.32, R30.33, R30.35, R30.36]
---

# Audit: shipped example activity types and example agent packs

## 1. Scope

- **Area** - the example subsystem end to end, across all three layers the user asked for:
  - `backend/contexts/activities/infrastructure/examples/` (course catalogue loader plus
    `courses/creative-thinking.json`) and `application/example_service.py`
  - `backend/contexts/agents/infrastructure/examples/` (pack loader plus both `packs/*.json`)
    and `application/example_service.py`
  - `backend/smap/examples/` (the operator CLI seeder)
  - the HTTP surface: `app/api/v1/activities.py`, `admin_activities.py`, `agents.py`
  - the machinery the install path drives: `type_service`, `activation_service`,
    `session_service`, `submission_service`, `policy_service`, `reachability`, the
    activities repositories, `alembic/versions/0076_platform_activity_types.py`,
    `AgentService.create`, `AgentGroupService`, the wake-up and turn-engine paths that the
    packs' shipped `wakeup_config` is supposed to drive
  - frontend: `slices/activities` (`ExampleImportDialog`, `schemaFields.ts`,
    `plugins/mandala9grid`, `ActivityTypesView`), `slices/admin`
    (`ActivityExamplesSection`, `PlatformActivityTypeDialog`, `AdminActivitiesView`),
    `slices/agents` (`AgentPackInstallDialog`, `AgentListView`), and the locale files

- **Intent sources** - four approved dossiers plus the SRS and the operator walkthrough.
  Sources were rich, not thin, which is why most findings below are contract violations
  rather than internal inconsistencies:
  - `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md`
  - `docs/tasks/2026-08-08-activity-example-catalogue/spec.md`
  - `docs/tasks/2026-08-09-platform-example-activity-types/spec.md`
  - `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md`
  - `REQUIREMENTS.md` entries listed in the frontmatter
  - `docs/examples/creative-thinking-course.md`

- **Depth** - thorough. Six investigation lenses run in parallel (catalogue loaders and
  shipped data; tenant isolation and authorization; install lifecycle and error paths;
  frontend rendering and `x-order`; agent runtime, wake-up and digest; persistence,
  migration and governance policy), then one adversarial verification round in which every
  candidate was assigned to an independent agent whose explicit job was to refute it.
  Twenty-eight candidates were raised; eighteen survived, ten were refuted. Two findings
  (F-13, F-14) were verified by direct reading rather than delegated.

## 2. Coverage

**Baseline established before analysis.** All 187 example-related backend unit tests pass
(`test_smap_examples_catalogue`, `test_agent_example_packs`, `test_agent_example_service`,
`test_activity_examples_service`, `test_activities_examples_layering`,
`test_smap_examples_cli`, `test_smap_examples_packaging`), as do the 37 frontend component
tests across `ExampleImportDialog`, `MandalaGrid`, `AgentPackInstallDialog` and
`ActivityExamplesSection`. Every finding below is therefore something the current test tier
does not cover; several sections note exactly why the tier is blind to them.

**Read in full**: both example catalogue loaders, both example services, both facades and
error maps, `creative-thinking.json`, both pack JSON files including every `system_prompt`
string, `_seeding.py` and `__main__.py`, migration 0076, `reachability.py`, the three
room-level services, `activity_context_provider.py`, `agent_digest.py`,
`wakeup_service.py`, `WakeupConfig.from_dict`, `schemaFields.ts`, `MandalaGrid.vue`, and
all five example-related Vue components.

**Executed, not merely read**: the loader probes behind F-13 and the refutations of the
`wakeup_config` typo, `x-order` typing and `validator_config` unknown-key candidates were
run against the real shipped files in a live interpreter. The `course_key` 500 was
reproduced empirically against a FastAPI app wired with the real exception handlers.

**Not covered.**

- **No `db`, `integration`, or `wiring` tier was executed.** Docker Desktop is not running
  on this host, the same limitation recorded as D-12 of the agent-packs dossier. In
  particular `backend/tests/integration/test_activity_schema_key_order.py` (the AC-4 test
  pinning the `jsonb` key-order premise on which the whole `x-order` design rests) has
  still never been observed passing. One lens read it and judged by inspection that it
  would pass and that `x-order` is fully implemented in both consumers, but that is
  reasoning, not measurement. Migration 0076 was likewise not applied or downgraded against
  a real PostgreSQL; F-3 rests on reading the migration plus Alembic's own source.
- **No behavioural verification in a running app.** Nothing here was observed in a browser
  or against a live LLM provider. Findings F-2 and F-12 concern runtime behaviour traced
  through code, not watched.
- **Prompt behaviour is out of reach by construction.** Whether an agent obeys its shipped
  prompt is untestable statically; this is the agent-packs dossier's own OQ-1. F-12 is a
  claim about what the prompt asks for versus what the context can supply, not about
  compliance.
- **The six unseeded course units** are not audited; they are unwritten content blocked on
  the collaborating educator (FU-1 of the platform-example dossier).
- **Lenses not applied**: structural quality, performance profiling, and vulnerability
  analysis were deliberately excluded and routed as noted in section 6.
- **Sampled rather than read in full**: `turn_engine.py` (only the system-block assembly,
  activity-context and provider-resolution paths), the conversation and orchestration
  contexts (only the wake-up and presence paths), and the workflow context (only
  activity-key rule matching).

## 3. Findings

Ordered by severity. Never renumber; F-n identifiers are cited from spec dossiers.

## F-1: The CLI seeder silently creates nothing once the project has opted into the platform course

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/smap/examples/_seeding.py:47`;
  `backend/contexts/activities/interfaces/facade.py:307-308`;
  `backend/contexts/activities/application/type_service.py:277-278`;
  `backend/contexts/activities/infrastructure/repositories/type_repo.py:187-217` (opt-in
  subquery at `:199-201`, the `sa.or_` at `:208-211`);
  `backend/smap/examples/__main__.py:49-51`, `:92-97`;
  `backend/smap/examples/_catalogue.py:15-24`;
  `backend/contexts/activities/application/example_service.py:178`
- **Failure scenario**: an admin installs `creative-thinking` platform-wide and project P
  opts in. An operator then runs
  `python -m smap.examples creative-thinking-course --project-id P --owner-user-id U`,
  the documented path for obtaining a project-scoped, owner-editable copy. `_seeding.py:47`
  builds its idempotency set as `{t.key for t in await facade.list_types(project_id)}`, and
  `list_types` now returns the project's own rows unioned with the platform rows the project
  opted into. All four course keys read as already present, so the seeder creates nothing,
  reports every key as `already_present`, and exits 0. The CLI and the platform installer
  read the same course file through the same loader (`_catalogue.py` is a re-export shim),
  so the overlap is total, not partial. P never gets the editable copy; platform types are
  read-only to Project Owners (`PlatformActivityTypeReadOnly`), which is precisely what the
  operator invoked the CLI to avoid. Re-running never converges.
- **Blast radius**: every operator using the documented CLI path on a project that has
  opted into any shipped example. Silent, with a success exit code.
- **Intent source**: `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:63-64`
  (Non-goals: "It remains the seeding path for a project-scoped copy and for air-gapped
  operators") and `:555-557` (AC-12). AC-12 was verified by "`test_smap_cli_contract.py`
  passes unmodified", and that is exactly the blind spot: the CLI tests double the facade
  with `MagicMock(key=k)` objects carrying no `project_id` and no `scope`
  (`backend/tests/unit/test_smap_examples_cli.py:62-66`), so the unit tier is structurally
  incapable of seeing this. Found independently by two lenses; the refuter escalated it
  from two keys to all four.

## F-2: Activity submissions are invisible to the wake-up system, so agents read worksheet time as a lull and never react to a submission

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/activities/application/submission_service.py:205-221`
  (`_ECHO_TYPE = "activity_submission"` at `:55`);
  `backend/contexts/conversation/interfaces/facade.py:200-221` (a pure create wrapper, no
  orchestration edge); `backend/app/api/v1/activities.py:769-771` and `:801-831`
  (`_dispatch_submission` does WS emit plus two enqueues, never a wake-up evaluation);
  the only three production callers of `evaluate_message_wakeups` are
  `backend/app/api/v1/messages.py:269`,
  `backend/contexts/agents/application/runtime/turn_engine.py:3177`,
  `backend/app/api/v1/observations.py:209`;
  `backend/contexts/orchestration/application/wakeup_service.py:105`, `:107-108`, `:111`;
  shipped SA config at
  `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:37-41`
- **Failure scenario**: a facilitator starts `mandala-9grid`; 28 students spend eight
  minutes silently filling nine cells and submitting. Each submission writes a real
  transcript message, but nothing on the submit path evaluates wake-ups, so
  `touch_silence_timestamp` is never called and `increment_message_count` never counts one.
  SA (`silence_minutes {enabled: true, t_minutes: 3, autostop_rounds: 2}`) sees an untouched
  silence clock; the presence gate passes because the students hold live WebSocket
  connections; SA posts into the room at roughly T+3min, TA (`every_n_messages n=1`) replies,
  SA fires once more at roughly T+6min before its autostop caps it, TA replies again. Four
  agent messages interrupt a deliberately silent writing phase, while the 28 submissions,
  the actual content of the lesson, produce zero TA reactions.
- **Blast radius**: every room running any activity type, not only the shipped examples.
  The autostop cap bounds the noise but does not remove it.
- **Intent source**: `docs/examples/creative-thinking-course.md:160` ("TA ... responds to
  every message") and `:268-271` ("Room agents read a digest of recent structured activity,
  which is what lets TA respond to what the class actually wrote"), plus
  `wakeup_service.py:76-77`, whose own docstring says the trigger "counts all messages in
  the room". No `[Rxx.yy]`, comment, or dossier entry excludes `activity_submission`
  messages, so this is a gap rather than a documented decision.

## F-3: Migration 0076 cannot be re-run after a mid-migration failure, and its own comment claims the opposite

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/alembic/versions/0076_platform_activity_types.py:43-61` (add column,
  drop NOT NULL, two CHECK constraints, none using `IF NOT EXISTS`), `:64-65` (the
  retry-safety comment), `:66` (`autocommit_block`), `:67-71` (`CREATE UNIQUE INDEX
  CONCURRENTLY`), `:73-98` (`create_table`, outside the block);
  `backend/.venv/Lib/site-packages/alembic/runtime/migration.py:313-337` (the block
  "unconditionally commits" the preceding transaction) and `:616-635` (the version stamp is
  written after `upgrade()` returns); `backend/alembic/env.py:139-146` (no
  `transaction_per_migration`); contrast `0074_activity_admin_listing_indexes.py:35-51`,
  where the entire `upgrade()` is the autocommit block so the inherited comment is true.
- **Failure scenario**: the concurrent index build at `:67-71` hits a lock or statement
  timeout on a live `activity_types`, or the `create_table` at `:73-98` fails.
  `alembic_version` still reads `0075`, but `scope`, the nullable `project_id` and both
  CHECK constraints are already committed by the autocommit block. Re-running
  `alembic upgrade head` re-enters `upgrade()` at `:43` and dies with `DuplicateColumn`
  (42701). The operator must hand-drop the column and both constraints before any retry can
  proceed, which is precisely what the comment at `:64-65` promises they will not have to
  do.
- **Blast radius**: any deployment applying 0076 where the concurrent index build or the
  table create fails. 0076 is the only migration in the tree that puts transactional DDL
  before an autocommit block.
- **Correction (2026-08-16, from the fixing dossier's research)**: this entry originally
  proposed `transaction_per_migration=True` in `env.py` or splitting 0076 at the block
  boundary. **The first is a no-op for this defect** — `autocommit_block` commits the
  preceding transaction under either setting (`alembic/runtime/migration.py:328-337`), and
  under per-migration mode that transaction is still exactly 0076's pre-block DDL. The second
  is unsafe while no record exists of which revision staging and production are stamped at.
  The entry also missed that `downgrade()` (`0076:135-149`) carries the **same defect
  mirrored**: it drops the index and table transactionally, then enters an autocommit block,
  so a failure after that point leaves the opt-in table gone and committed at version `0076`.
  Both corrections are carried into
  `docs/tasks/2026-08-16-migration-0076-retry-safety/`.
- **Intent source**: the migration's own retry-safety comment; and AC-1 of
  `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:501-507`, which exercised
  upgrade and downgrade but not a mid-migration failure.

## F-4: The admin Edit action on an installed platform example is offered, enabled, and does nothing when the row is past the 200-row page

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/admin/components/ActivityExamplesSection.vue:147`
  (`TYPES_PAGE_LIMIT = 200`), `:160-163` (one page, no `enabled` gate), `:165-167`
  (`editRow` is a `find(...) ?? null`), `:96-104` (the Edit button's only condition is the
  catalogue's `unit.installed_type_id !== null`, with no `:disabled` and no null guard);
  `frontend/src/slices/admin/components/PlatformActivityTypeDialog.vue:147-159` (a null row
  seeds defaults, not stored values), `:75` (Save enables on a non-empty name), `:200-201`
  (`onSubmit` returns early before the mutation, with no toast and no request);
  `backend/app/api/v1/admin_activities.py:258-266` and
  `backend/contexts/activities/infrastructure/repositories/type_repo.py:152-166`, both
  documented as unscoped across every project.
- **Failure scenario**: the admin types listing is every tenant's types, newest first, not
  just the platform ones. Platform examples are installed at setup, so they are the oldest
  rows and the first to fall off page 1 as projects create types. On a deployment past 200
  types, an admin opens `/admin/activities`, clicks Edit beside
  單元二 時空旅人（曼陀羅九宮格）, and gets a dialog whose Name is blank and whose two
  governance switches show defaults rather than the stored values. They type a name, Save
  enables, they click it: no request, no error, no toast, the dialog stays open. This is
  the exact path Q-4 exists for, an admin editing a policy-locked example back into
  compliance, and it dead-ends. A second, commoner variant heals but still loses input: the
  examples query can resolve before the types query, so an admin who clicks Edit and types
  during that window has the form reseeded from under them.
- **Blast radius**: any deployment with more than 200 live activity types across all
  projects. `AdminActivitiesView.vue:202-207` already reasons about this exact case and
  renders a truncation warning (`:219`); `ActivityExamplesSection` shares the limit but has
  no warning and degrades silently.
- **Intent source**: AC-8 and Q-4 of
  `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:76`, `:534-538`.
  `ActivityExamplesSection.test.ts` stubs the types endpoint with the matching row present
  in every case (`:118`, `:135`, `:171`, `:204`), so the tier never sees the null branch.

## F-5: Two live activity types can share one key inside a single project's usable set

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/alembic/versions/0049_activities.py:78-79`
  (`(project_id, key) WHERE deleted_at IS NULL`) and
  `backend/alembic/versions/0076_platform_activity_types.py:68-70`
  (`(key) WHERE project_id IS NULL AND deleted_at IS NULL`); neither spans the
  project-versus-platform pair. `type_repo.py:187-217` unions both populations into one
  listing. No guard at route, service or repo:
  `backend/contexts/activities/application/type_service.py:38-79`;
  `backend/app/api/v1/activities.py:309-335`.
  Consumers that key on the string: `backend/contexts/workflow/application/event_dispatch.py:86-116`
  (matches on `chatroom_id`, `activity_type_key`, `validation_status`, no id and no scope),
  fed from `backend/contexts/activities/application/submission_service.py:250` and `:337-341`;
  `frontend/src/slices/activities/components/ActivityHost.vue:44`;
  `frontend/src/slices/activities/plugins/registry.ts:13-15`;
  `frontend/src/slices/activities/components/ActivityPanel.vue:44` (picker labels by `name`
  alone).
- **Failure scenario**: Alice, owner of project P, opts into the platform `mandala-9grid`,
  then registers her own type with `key: "mandala-9grid"`. Both inserts succeed because the
  two partial-unique indexes do not overlap. `GET /projects/P/activity-types` returns two
  rows with the same key; both appear in the facilitator's activation picker distinguished
  only by name; both resolve to the same bundled plugin regardless of schema; and a workflow
  reactive rule matching `{"activity_type_key": "mandala-9grid"}` fires for submissions to
  either one indiscriminately. Both rows independently pass the tenancy gate, so both are
  dispatchable by id.
- **Blast radius**: any project that both opts into a platform example and authors a type
  under the same key. Requires a deliberate owner action, which is why this is not critical.
- **Intent source**: [R30.02]'s uniqueness sentence is satisfied per scope but no longer
  per project-usable-set. `frontend/src/slices/activities/plugins/mandala9grid/index.ts:1-7`
  and `docs/examples/creative-thinking-course.md:312-314` document the one-plugin-per-key
  limitation on the stated premise that "`ActivityType.key` is unique only per project" -
  the widening falsified that premise, and no dossier claimed cross-scope key uniqueness.

## F-6: An unknown or malformed `course_key` on the admin install route returns 500 rather than 404

- **Severity**: minor
- **Verdict**: confirmed (reproduced empirically)
- **Evidence**: `backend/contexts/activities/infrastructure/examples/catalogue.py:66`
  (`CourseFileInvalid(ValueError)`, MRO verified at runtime as
  `['CourseFileInvalid', 'ValueError', 'Exception', 'BaseException', 'object']`), `:290-293`,
  `:298-300`; `backend/contexts/activities/domain/errors.py:10` (`ActivitiesError` subclasses
  `Exception`, not `ValueError`, so the reverse MRO argument fails too);
  `backend/contexts/activities/interfaces/error_mapping.py:15-98` (no row) and `:114-115`
  (handler registered on `ActivitiesError`);
  `backend/contexts/activities/application/example_service.py:178` (loads unconditionally);
  `backend/app/api/v1/admin_activities.py:368-393` (`FPath(..., max_length=128)` only, no
  `pattern=`, no try/except); `backend/shared_kernel/errors/handlers.py:124-133`, `:141`.
  Contrast the correct sibling: `backend/contexts/agents/application/example_service.py:193-196`
  pre-checks `available_packs()` and raises the mapped `AgentPackNotFound`.
- **Failure scenario**: an admin POSTs `/api/admin/activity-examples/creative-thinkin/install`
  (typo) or `/Creative-Thinking/install` (uppercase, rejected by the anchored key regex
  before any filesystem touch). Both return 500 with title "Internal Server Error" and are
  logged as unhandled exceptions with a full traceback. A client cannot distinguish "no such
  course" from "the server is broken", and every admin typo pages whoever watches 5xx rates.
  The same 500 masks the realistic packaging failure: after a deploy that shipped no
  `courses/*.json`, the loader composes a precise diagnosis ("no such course in the catalogue
  (available: none)") which is discarded.
- **Blast radius**: the admin install endpoint only, behind `require_admin`. Near-unreachable
  through the UI, which offers only valid keys; reachable by any script or curl. The
  traversal guard itself holds and nothing is leaked.
- **Intent source**: recorded as FU-12 at
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:880-884` and marked
  "Pre-existing in the activities work, not touched here". Verified still live.

## F-7: `min_filled` is never checked against the declared property count on either API write path

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the hook exists and is registered:
  `backend/app/plugins/activity_validators.py:132-153`, registered via
  `schema_config_validator=` at `:168`; accessor at
  `backend/contexts/activities/application/validators/registry.py:100-108`. Its only
  production caller in the whole tree is the course loader
  (`backend/contexts/activities/infrastructure/examples/catalogue.py:185-187`).
  `backend/contexts/activities/application/type_service.py:315-344`
  (`_validate_validator_config` is a static method taking `(kind, config)` only, so it
  structurally cannot run a cross-check against the schema) is the sole validator gate for
  `register` (`:60`) and `update` (`:150`). No Pydantic escape hatch:
  `backend/app/api/v1/activities.py:69-77` and `:80-91` declare both fields as
  `dict[str, Any]` with no validators. The frontend enforces the rule and its comment names
  the backend gap: `frontend/src/slices/activities/types/schemas.ts:138-149`.
- **Failure scenario**: a Project Owner POSTs an activity type with a three-property schema
  and `validator_config: {"validator_id": "filled_count", "min_filled": 99}`.
  `validate_filled_count_config` sees a non-negative int and passes, so the type is created.
  Every subsequent submission computes `filled <= 3 < 99` and returns
  `is_valid=False, error_class="too_few_filled"`. The activity is permanently unpassable and
  nothing in the product says why. `PATCH` has the identical hole.
- **Blast radius**: self-inflicted and project-scoped; the UI blocks it and the course loader
  blocks it, so only a direct API call reaches it. No cross-tenant or privilege dimension.
- **Intent source**: FU-5 of
  `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:727-733`, which states
  closing it is "a three-line change in `type_service._validate_validator_config`". Still
  open. No test exercises `register`/`update` with an over-threshold `min_filled`; the
  existing coverage calls the hook and the registry directly.

## F-8: Re-installing a pack after its group was renamed creates a second group and reports that nothing was created

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/example_service.py:328-351` (`_group_for`
  matches by exact name at `:343`, else creates at `:345-351`);
  `backend/contexts/agent_groups/infrastructure/tables.py:19-42` (no `pack_key`, no metadata
  column, so nothing stable exists to match on);
  `backend/alembic/versions/0043_graphrag_owner.py:52-55`
  (`uq_agent_groups_project_name_active` is on `(project_id, name)`, so once the name has
  moved the second insert collides with nothing and succeeds);
  `backend/app/api/v1/agent_groups.py:159-177` (rename is a supported Project Owner route);
  `example_service.py:99-105` and `backend/app/api/v1/agents.py:502-513`, `:590-603` (the
  report carries `created`, `already_present` and a bare `group_id`, with no created-versus-
  reused signal); `example_service.py:186-189` (the docstring claims unqualified convergence
  on the group); `frontend/src/slices/agents/components/AgentPackInstallDialog.vue:115`
  (toasts on `report.created.length` alone, never mentioning the group).
- **Failure scenario**: an owner installs `creative-thinking-room`, then renames the group
  `創造思考技法 課堂代理` to `七年三班`, a plausible act for a teacher. Later they re-install,
  the documented way to recover a deleted agent. `_group_for` finds no group under the pack's
  name, creates a second one, and adds all three agents to it while their memberships in the
  renamed group remain. The HTTP response is `{"created": [], "already_present": [three
  names], "group_id": "<new>"}`, reading exactly like AC-8's "a second install creates
  nothing", and the toast says the same. The Agents page now shows two groups holding the
  same three agents.
- **Blast radius**: one project per occurrence. No data loss, no cross-tenant leakage, and
  `concept_map_enabled` defaults to false so the duplicate does not widen retrieval.
- **Intent source**: AC-8 (`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:634-638`)
  and §7 NFR ("A partially-completed install reports created and already-present separately").
  The dossier does accept rename-fragility at `:265-270` and `:591`, but only for *agent*
  renames, and the safety net it leans on at `:592` ("the report telling the owner what it
  created") is exactly what is missing for the group. Note the AC-8 test itself
  (`backend/tests/unit/test_agent_example_service.py:177-187`) awaits `create_group` during
  its own run and asserts nothing about it.

## F-9: Admin delete of a platform type orphans every project's opt-in row, and two docstrings plus the upgrade note describe a cascade that never fires

- **Severity**: minor
- **Verdict**: confirmed (mechanism), with the originally claimed impact refuted
- **Evidence**: `backend/contexts/activities/infrastructure/repositories/type_repo.py:300-313`
  (the delete is `UPDATE ... SET deleted_at`, never a row `DELETE`);
  `backend/alembic/versions/0076_platform_activity_types.py:84` (`ON DELETE CASCADE`, which
  only a real `DELETE` triggers) and `:99-107` (an index whose comment says it "drives the
  admin delete cascade", built for a cascade that is dead code);
  `backend/contexts/activities/interfaces/facade.py:487-488` and
  `backend/app/api/v1/admin_activities.py:434` (both assert the opt-in rows disappear through
  the FK cascade); an exhaustive sweep found nothing that hard-deletes `activity_types` -
  the retention purge table list (`backend/app/workers/tasks/retention.py:60-66`) does not
  include it, and platform rows have `project_id IS NULL` so the project cascade cannot reach
  them either.
- **Failure scenario**: following the documented upgrade note, an admin deletes a platform
  type and re-installs. Re-install mints a new id (the partial unique excludes tombstones and
  no resurrection path exists), so every project's opt-in row is left pointing at the
  tombstone forever, and no project holds an opt-in for the new row. Every project that had
  enabled the example must re-enable it, which neither the upgrade note nor any audit event
  tells anyone. The rows accumulate and cannot be reclaimed even by `opt_out`, which 404s
  because the type read filters tombstones.
- **Blast radius**: bounded by projects times deleted platform types. **No authorization or
  data-exposure impact**: every read of `activity_types` filters `deleted_at IS NULL`, and
  `reachability.py:44-46` fetches the type first and 404s before the opt-in is consulted, so
  a stale row can never become effective. The 404 a facilitator sees after a delete is the
  intended, documented semantics of delete-for-everyone, not a consequence of this defect.
- **Intent source**: the two docstrings, and
  `docs/examples/creative-thinking-course.md:252-254`, which warns that activations end but
  never that projects must re-enable. The missing per-project `activity_type.opted_out`
  emission was raised against [R30.33] and is *not* upheld: R30.33 requires audit events for
  opt-in and opt-out as operations, and delete emits its own `activity_type.deleted` carrying
  scope and key.

## F-10: The shipped-examples dialog clears its pending state for the wrong row, and a duplicate disable reports failure for an action that succeeded

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/activities/components/ExampleImportDialog.vue:31-32` (one
  `pendingId` shared by both mutations, with a comment asserting only the in-flight row shows
  pending), `:68-70` and `:83-85` (both `onSettled` handlers null it unconditionally), `:200`
  and `:209` (`:disabled="pendingId === example.id"`). No `isPending` appears anywhere in the
  template, `SModal` has no busy state, and no outer element is disabled. Backend idempotency
  is split: `backend/contexts/activities/infrastructure/repositories/optin_repo.py:84-93` uses
  `on_conflict_do_nothing` so opt-in is a no-op on repeat, but
  `backend/contexts/activities/application/example_service.py:305-307` raises
  `ActivityTypeNotOptedIn` when `remove` returns False. Contrast the fixed sibling at
  `frontend/src/slices/agents/components/AgentPackInstallDialog.vue:241-248`.
- **Failure scenario**: the shipped course renders four rows, so two fast clicks are reachable
  today. An owner clicks Enable on row 1; while it is in flight they click Enable on row 2.
  `pendingId` flips, re-enabling row 1 mid-flight. When request 1 returns, `onSettled` nulls
  `pendingId`, re-enabling row 2 while its own POST is outstanding. A further click fires a
  duplicate, producing two success toasts for one action. On the disable path the duplicate
  raises `ActivityTypeNotOptedIn`, so the user sees `activities.examples.disableFailed` for a
  disable that in fact succeeded.
- **Blast radius**: the Project Owner import dialog. No data corruption. The same single-valued
  shape exists at `frontend/src/slices/admin/components/ActivityExamplesSection.vue:142`,
  `:62`, `:191-193`, currently masked because exactly one course ships and the button is
  additionally disabled by `fully_installed`.
- **Intent source**: D-14 of `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:820-823`
  identified this exact mechanism and fixed it in the agents dialog only. The fix is one
  expression.

## F-11: The pack install dialog never shows the resolved model hint or the bound activity types, and its own header comment claims it does

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/components/AgentPackInstallDialog.vue:6-8` (the
  comment: "why it shows which provider each agent will end up on"), `:256-277` (the per-agent
  list item renders `agent.name` at `:262`, `roleLabel(...)` at `:267`, and an installed badge
  at `:269-275`, and nothing else), `:112-115` (`onSuccess` reads only
  `report.created.length`). A whole-tree grep is decisive: `preferred_model_hint` and
  `binds_activity_types` occur in `frontend/src` only in the generated model
  `shared/api-client/models/ExamplePackAgentOut.ts:9,13` and a test fixture, with zero
  occurrences in any `.vue` file; `InstalledPackAgentOut.model_hint` likewise appears only in
  the model and a fixture.
- **Failure scenario**: a Project Owner whose only key group carries OpenAI keys installs
  `creative-thinking-room`, whose agents declare `preferred_model_hint: "claude"`. Before
  confirming, the dialog names no provider for any agent, so the owner cannot learn the pack's
  preference will not be served. After install the toast reads only "Installed 3 agent(s)";
  the per-agent `model_hint` the server computed and returned is discarded. The owner
  discovers the substitution only by opening each agent or reading the list column
  (`AgentListView.vue:143`, `:336-346`). Which activity types each agent was written for is
  surfaced nowhere at all, before or after.
- **Blast radius**: informational only; the install itself is correct. Partially mitigated by
  `slices/agents/locales/en.json:25`, which explains the fallback *policy* without ever naming
  a provider.
- **Intent source**: `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:434-438`
  (the dialog must list agents "with role, orchestration summary, and the activity types each
  is written for" and show "the resolved model hint before confirming") and `:427` ("so the UI
  can state what it picked rather than implying the pack chose"). The orchestration summary is
  unrepresentable because `ExamplePackAgentOut` carries no wake-up data, so that element was
  dropped at the API layer too.

## F-12: The AA prompt asks for a report on non-submitters that its context cannot ground

- **Severity**: minor
- **Verdict**: confirmed (as a prompt-content defect, not a code defect)
- **Evidence**: `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:74`
  instructs `參與的分布：誰還沒提交、誰反覆嘗試、哪個活動卡住的人最多`. The only structured input is
  the activity block: `backend/contexts/activities/application/activity_context_provider.py:31`
  (`DEFAULT_ACTIVITY_WINDOW = 30`), `:38-55`, `:79-87` (one line per submission, no roster);
  `backend/contexts/agents/application/runtime/turn_engine.py:3892-3898` passes no limit
  override and is the sole call site, so observers and normal agents get an identical block;
  `backend/contexts/activities/infrastructure/repositories/submission_repo.py:259-300` orders
  newest-first, limits to 30, filters by `chatroom_id` only and does not dedupe by subject.
  No roster exists in any system block: `turn_engine.py:814-849` enumerates them, and
  `_participant_labels` (`:3239-3261`) resolves only senders already present in history.
  The transcript labels are display names while the activity block uses truncated UUIDs, with
  no mapping between them.
- **Failure scenario**: 28 students times two activity types per unit is roughly 56 submission
  rows against a 30-row window, and `min_filled: 4` over nine properties means retries consume
  further slots. By the second activity the first activity's submissions are entirely gone
  from AA's context. Asked for 誰還沒提交, AA has no roster and a truncated window, so the
  visible evidence positively suggests early students never submitted. AA reports them as
  non-submitters to the teacher: fabricated participation data about minors.
- **Blast radius**: the shipped AA prompt in any class larger than the window. The prompt does
  carry general hedges ("沒有證據就不要寫", "你看到的是提交事件與討論發言，不是學生的內在狀態")
  but none says the window is capped or that absence of a row is not absence of a submission.
- **Intent source**: §8 and AC-10 of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:502-505`, `:642-644`, whose
  concern is precisely that a prompt must not manufacture assessment data about minors. AC-10
  asserts only the prompt's text. The 30-row bound is deliberate documented design
  (`docs/tasks/2026-07-13-activities-observer-context/spec.md:106-110`), so the gap is in the
  shipped prompt, not the code. This is a different claim from the packs dossier's OQ-1, which
  concedes only that *obedience* is untestable; here the prompt asks for something the context
  structurally cannot supply. Belongs in the pre-deployment dry-run checklist.

## F-13: The example walkthrough states that booleans always count as filled; the code states and implements the opposite

- **Severity**: minor
- **Verdict**: confirmed (verified by direct reading)
- **Evidence**: `docs/examples/creative-thinking-course.md:289-292` ("booleans always count as
  filled, because the generic form submits a value for every declared boolean property whether
  or not the participant touched it") versus
  `backend/app/plugins/activity_validators.py:85` (`if value is None or value is False: return
  False`), whose docstring at `:71-77` gives that identical premise as grounds for the opposite
  conclusion: counting an unticked box "would let a submission with nothing filled in at all
  score `filled == len(properties)` and pass any threshold".
- **Failure scenario**: a Project Owner reads the walkthrough, authors an activity type with
  six boolean checkboxes and `min_filled: 4`, and expects untouched boxes to count. Every
  participant who ticks three boxes scores `filled: 3` and is rejected with `too_few_filled`
  in front of the class.
- **Blast radius**: authors of boolean-bearing schemas who follow the walkthrough. The four
  shipped types are all-string, which the same paragraph correctly notes, so the shipped
  example is unaffected.
- **Intent source**: [R30.27] and the validator's own docstring. The doc sentence inverted the
  conclusion of the reasoning it quotes.

## F-14: The CLI help text and package docstring still describe the pre-correction layout

- **Severity**: minor
- **Verdict**: confirmed (verified by direct reading)
- **Evidence**: `backend/smap/examples/__main__.py:43-44` renders as `--help` and says
  "Defaults to the two-unit creative-thinking course: `mandala-9grid` ... and
  `six-hats-emotion-desk`", but the shipped course has declared four types since the
  2026-08-13 dossier (`backend/contexts/activities/infrastructure/examples/courses/creative-thinking.json`;
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:320-325`). Separately,
  `backend/smap/examples/__init__.py:14` still documents "`courses/*.json` - course content"
  as living in that package, though it moved to
  `contexts/activities/infrastructure/examples/`; the same file's `__main__.py:46-47` was
  updated and points at the new path correctly.
- **Failure scenario**: an air-gapped operator following
  `docs/examples/creative-thinking-course.md` runs the CLI, reads `--help`, expects two types,
  and sees a report listing four created keys, with no way to tell whether the two extra are
  correct or a defect.
- **Blast radius**: operator-visible text only. Note `__main__.py:49-51` also states the
  idempotency rule as "a type whose key already exists is left untouched", which is the
  statement F-1 shows is now false in the presence of opt-ins.
- **Intent source**: [R30.28] and the four-type course of
  `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:320-325`.

## F-15: `common.*` translation keys exist in no locale file, so parts of the example surfaces render English inside a zh-TW UI

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/activities/components/ExampleImportDialog.vue:224`
  (`t('common.close', 'Close')`, the dialog's only footer button) and
  `frontend/src/slices/activities/views/ActivityTypesView.vue:124-125` (`t('common.edit',
  'Edit')`, `t('common.delete', 'Delete')`). Flattening every `en.json` under
  `frontend/src/**/locales/` shows no `common` namespace anywhere, and
  `frontend/src/shared/i18n/index.ts:26-34` merges bundles flat, so no other source exists.
  The literal second argument is a vue-i18n default message, so the English text renders
  rather than the raw key.
- **Failure scenario**: a zh-TW Project Owner opens 活動類型 then 內建範例; every string is
  Chinese except the footer button, which reads "Close". The row action menu on a
  project-scoped type shows "Edit" and "Delete" in English amid Chinese labels.
- **Blast radius**: cosmetic. Pre-existing pattern with 17 occurrences across 6 files, two of
  which are surfaces these dossiers shipped.
- **Intent source**: AC-14 of `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:566`
  ("All user-facing strings in both slices exist in `en.json` and `zh-TW.json`"). Separately
  verified clean: the `activities` (168), `agents` (556) and `admin` (266) key sets are
  identical between en and zh-TW, and all four literal `@` characters in translation values
  are correctly escaped as `{'@'}`, so there is no production crash risk.

## F-16: The pack dialog never states DA's write-back limit, which AC-14 requires of the dialog specifically

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/locales/en.json:12-39` and `zh-TW.json:12-39` are
  the complete `agents.examplePacks` namespace; the only DA-related string is `roleDesign`
  ("Not for a class room" / 「不進上課討論室」). `nextSteps` covers creating a chatroom, binding
  and starting an activity, and nothing mentions copying a draft by hand; a search for
  `paste|hand|手動|貼上` across both files returns nothing.
  `frontend/src/shared/api-client/models/ExamplePackOut.ts` has no `description` field, so the
  sentence cannot arrive as data either. The test claiming the AC
  (`frontend/src/slices/agents/__tests__/AgentPackInstallDialog.test.ts:111-120`) asserts only
  that the `roleDesign` label appears.
- **Failure scenario**: an owner installs `creative-thinking-design`, chats with DA, receives a
  drafted TA system prompt, then looks for a way to apply it to an agent. Nothing in the dialog
  said the only path is manual copy-paste; the badge "Not for a class room" reads as a
  placement note rather than a capability limit.
- **Blast radius**: users of the design pack.
- **Intent source**: AC-14 of `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:655-657`
  ("the dialog and docs both state ... that its drafts must be pasted into an agent by hand"),
  reinforced at `:370-373` ("a 'design agent' that appears to configure agents is the obvious
  misreading"). The docs half was done; the dialog half was not.

## F-17: The provider fallback silently voids every temperature the packs ship, and nothing says so

- **Severity**: minor
- **Verdict**: confirmed (as a docs gap, not a code defect)
- **Evidence**: shipped temperatures TA 0.7, SA 0.9, AA 0.2, DA 0.6.
  `backend/contexts/agents/application/example_service.py:309-326` falls back to `usable[0]`
  and `backend/contexts/agents/domain/models.py:12-15` orders CLAUDE, OPENAI, GEMINI, so an
  OpenAI-only project gets `openai`. `install_pack` sets no `model_id` (`:243-250`), so
  `turn_engine.py:335-338` resolves `DEFAULT_CHAT_MODELS["openai"] = "gpt-5.4"`
  (`domain/models.py:36`). `_REASONING_MODEL_RE = ^(?:o\d|gpt-5)`
  (`backend/contexts/keys/infrastructure/adapters/openai.py:34`, `:42-43`) matches it, and
  every OpenAI preset in the catalog, so the adapter drops temperature and `top_p` entirely
  (`:155-160`). The Claude path is unaffected (`adapters/anthropic.py:35` does not match
  `claude-sonnet-4-6`) and Gemini forwards it.
- **Failure scenario**: a project holding only an OpenAI key installs `creative-thinking-room`.
  Install succeeds and correctly reports `openai`. Every turn then runs at the provider
  default, so SA's 0.9 divergence and AA's 0.2 near-determinism collapse to the same effective
  behaviour as TA's 0.7. The pack's orchestration survives the substitution; its sampling does
  not, and the install dialog, the report and the docs all describe the fallback purely as a
  provider substitution.
- **Blast radius**: any non-Claude installation of either pack. **The code is correct**: the
  drop is documented at `turn_engine.py:162-178` and at `openai.py:156` ("Reasoning models
  accept only the default temperature; a custom one 400s"), and the alternative is a 400 on
  every turn.
- **Intent source**: `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:261-263`
  treats temperature as one of the things installing reproduces, and §5.3 rejects Option C
  partly because a prompt template would lose it. One sentence in the walkthrough's
  Limitations list closes this.

## F-18: The mandala plugin promotes the first property to the centre cell when none is named `center`, overriding declared `x-order`

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `frontend/src/slices/activities/plugins/mandala9grid/MandalaGrid.vue:37-40`
  (`centerField` falls back to `fields.value[0]`), `:43-48` (that field is then removed from
  the ring and spliced back at index 4). [R30.36] states the platform "renders declared fields
  in that order", and the dossier's own description of the plugin
  (`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:308-310`) says only that it
  "removes the property named `center`, and splices it back at index 4", mentioning no
  fallback.
- **Failure scenario**: a project reuses the `mandala-9grid` key with its own nine-property
  schema declaring `x-order` 1 to 9 and no property literally named `center`, for example
  `q1` through `q9`. The participant sees `q2, q3, q4, q5, q1, q6, q7, q8, q9`: the author's
  first cell silently relocated to the middle, while the declared order is honoured everywhere
  else on the platform.
- **Blast radius**: projects authoring their own nine-field schema under the mandala key. The
  shipped course is unaffected, since it names a `center` property at `x-order` 5.
- **Intent source**: [R30.36]. **Marked plausible rather than confirmed** because
  `frontend/src/slices/activities/__tests__/MandalaGrid.test.ts:110-118` explicitly pins this
  behaviour ("treats the first property as the centre when none is named center") citing an
  earlier dossier's AC-8, so it may be accepted intent that [R30.36] later contradicted rather
  than a defect.
- **Triage decision (2026-08-16)**: **[R30.36] wins; the fallback is removed.** A schema with
  no property named `center` renders its declared fields in declared order with no promotion.
  `MandalaGrid.test.ts:110-118` is amended in the same change, and the fixing dossier records
  why the earlier AC-8 behaviour is being reversed rather than silently dropped. This
  supersedes the plausible verdict: with the rule conflict resolved in [R30.36]'s favour, the
  behaviour is a defect.

## 4. Refuted Candidates

Ten candidates did not survive adversarial verification. Kept because each refutation is
itself informative.

1. **"A governance policy locked before install makes the shipped examples permanently
   uninstallable, so Q-4's escape hatch is unreachable."** Refuted on interpretation, not
   mechanics: every mechanical step is real, but Q-4 scopes itself verbatim to
   install-then-tighten ("no actor able to fix them",
   `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:76`, `:161`), and
   `require_admin` gates both the policy route and the install route, so one principal can
   loosen, install, edit into compliance and re-lock. Refusing a policy-violating install is
   the documented intent (§8: "nothing in this change may add a platform exemption").
   Residual worth a test: the ordering is uncovered, and `preview_policy_impact` counts only
   live types, so an admin with nothing installed sees zero violations and is then surprised
   by the 409.
2. **"`list_for_project`'s opt-in subquery is not scope-restricted."** The asymmetry is real
   (`type_repo.py:199-201` has no scope predicate, and migration 0076 adds no CHECK on
   `activity_type_id`), but `example_service.py:255-257` is the only writer and enforces
   `scope is PLATFORM`, and every load-bearing gate goes through `reachability.py:44-53`,
   which consults the correct domain predicate and says so at `:14-15`. Unreachable through
   any HTTP path; a hardening item, not a functional defect.
3. **"A platform type's `validator_config` is gated on ownership of the calling project rather
   than the type."** Refuted by [R30.25] verbatim (`REQUIREMENTS.md:2184`): the boundary is
   owner versus non-owner *of the calling project*, and a Project Owner is exactly the persona
   the requirement admits. The code implements §8's instruction literally. Also no harm exists
   to construct: every shipped `validator_config` is `{"validator_id": "filled_count",
   "min_filled": N}`, and `install_course` refuses any non-`in_process` validator for a
   platform row, so the shapes that could carry a secret can never exist on one.
4. **"A misspelled `wakeup_config` container key silently installs an inert agent."** The
   mechanism reproduces exactly (`trigger` instead of `triggers` yields every trigger
   disabled), but `backend/tests/unit/test_agent_example_packs.py:85-108` subscripts
   `wakeup_config["triggers"]` directly and pins every `enabled` and `n` for all four shipped
   agents, so the mutation raises `KeyError` in the unit tier. The easy miss: the sibling test
   `test_no_pack_ships_call_only` uses a tolerant `.get("triggers", {})` and passes vacuously
   on the same typo.
5. **"`x-order` is never validated, so the ordering mechanism can be silently inert."** String
   and duplicate `x-order` values are indeed accepted by the loader, but [R30.36]
   (`REQUIREMENTS.md:2195`) is permissive by construction ("may declare"), non-validation is a
   recorded decision (`spec.md:293-295`), and
   `backend/tests/unit/test_smap_examples_catalogue.py:202-214` asserts
   `sorted(orders) == list(range(1, len+1))` over every shipped property, which catches
   missing, duplicate and string values (a string raises `TypeError` in the comparison).
6. **"Unknown keys inside `validator_config` defeat the loader's no-silent-default rule."** The
   grading flip is real and was reproduced (a typo'd `case_sensitve` grades
   case-insensitively), but the attribution is wrong: `validator_config` is a per-validator
   opaque payload the loader structurally cannot shape-check, which is why it delegates to the
   registry, and the registry contract asks only that a config validator reject a config
   "malformed for it". No shipped course uses `exact_match`, and the UI machine-assembles that
   config from typed Zod fields so it cannot produce the typo.
7. **"A pack agent name with surrounding whitespace makes install permanently
   non-idempotent."** Refuted by a constraint both the finder and the candidate write-up missed:
   `uq_agents_project_name_active` on `(project_id, name) WHERE deleted_at IS NULL`
   (`backend/alembic/versions/0011_agents.py:103-105`). The second install raises
   `AgentNameTaken` mapped to 409 and the whole install rolls back; you get one agent and a
   loud error, never silent duplicates.
8. **"When two live agents share a pack's name, the group silently gets the oldest one."**
   Refuted by the same index: two live agents in one project can never share a name, so the
   dict-comprehension collision never arises, and `list_for_project` filters soft-deleted rows
   so a tombstoned namesake never enters the map either. The candidate's load-bearing premise
   ("`agents.name` carries no uniqueness constraint", inherited from the dossier's §4.2) is
   false.
9. **"`install_pack` bypasses the API's `wakeup_config` bounds, so a later UI edit 422s."**
   Refuted because `AgentService.create`'s normalization *is* the bounds check:
   `merge_json_config(merged, WakeupConfig.from_dict(merged).to_dict())`
   (`agent_service.py:406-408`) clamps every documented numeric leaf at parse time
   (`contexts/orchestration/domain/models.py:256-295`, whose docstring states the intent), and
   the clamped patch wins the merge. `{"n": 99999}` is stored as `n: 1000`, inside the exact
   range the API validator enforces. The candidate conflated `BoundedConfig` (bytes, depth and
   node count only) with the range validator.
10. **"Re-installing after the schema correction reports plain success with no drift signal."**
    Every mechanical claim is correct, but D-16
    (`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:827-832`) records the
    scenario sentence for sentence, including "reports success", and "no re-sync, no
    versioning" is an explicit non-goal (`:593-595`). A documented, accepted limitation rather
    than a finding. Worth passing on for whoever builds the re-sync: `AdminActivityTypeOut`
    omits `payload_schema` entirely, so there is currently no admin surface from which a stale
    row's stored schema can even be inspected to diagnose the drift.

One further candidate was **partially** refuted and folded into F-9 rather than dropped: the
claim that orphaned opt-in rows cause authorization failures. Every read filters
`deleted_at IS NULL` and `reachability.py:44-46` fetches the type before consulting the opt-in,
so the orphans are permanently inert and the facilitator's 404 is intended delete semantics.
Only the two false docstrings, the unreclaimable rows and the silent opt-in loss on the
documented upgrade path survive.

## 5. Hand-off

Per the dossier contract, this section links the task slugs this audit spawned. A finding with
no dossier and no explicit decision to skip it is an unfinished triage.

**Triaged 2026-08-16: every finding is selected for fixing.** None was declined.

**Why eighteen findings map to thirteen dossiers.** The contract's `depends_on` section names
"overlap prerequisite" as a first-class reason to sequence work: dossiers touching the same
files closely enough that building them concurrently produces conflicting diffs. Several
findings here share a blast radius exactly that way (F-6 and F-7 are both the activities
validation-and-error contract; F-8, F-11 and F-16 are all the pack install report and the one
dialog that renders it; F-1 and F-14 are the same two CLI files). Grouping them is not
scope-merging: each finding keeps its own AC inside the dossier that owns it, and each row
below still resolves to a named artifact.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix | `docs/tasks/2026-08-16-example-cli-seeder-scope-leak/` |
| F-14 | fix | `docs/tasks/2026-08-16-example-cli-seeder-scope-leak/` |
| F-2 | fix | `docs/tasks/2026-08-16-activity-submission-wakeup-gap/` |
| F-3 | fix | `docs/tasks/2026-08-16-migration-0076-retry-safety/` |
| F-4 | fix | `docs/tasks/2026-08-16-admin-platform-type-edit-unreachable/` |
| F-5 | fix | `docs/tasks/2026-08-16-activity-type-key-collision-across-scopes/` |
| F-6 | fix | `docs/tasks/2026-08-16-activities-install-error-contract/` |
| F-7 | fix | `docs/tasks/2026-08-16-activities-install-error-contract/` |
| F-8 | fix | `docs/tasks/2026-08-16-agent-pack-install-report-fidelity/` |
| F-11 | fix | `docs/tasks/2026-08-16-agent-pack-install-report-fidelity/` |
| F-16 | fix | `docs/tasks/2026-08-16-agent-pack-install-report-fidelity/` |
| F-9 | fix | `docs/tasks/2026-08-16-platform-type-delete-optin-lifecycle/` |
| F-10 | fix | `docs/tasks/2026-08-16-example-dialog-pending-and-optout/` |
| F-12 | fix | `docs/tasks/2026-08-16-example-pack-prompt-grounding/` |
| F-13 | fix | `docs/tasks/2026-08-16-example-docs-corrections/` |
| F-17 | fix | `docs/tasks/2026-08-16-example-docs-corrections/` |
| F-15 | fix | `docs/tasks/2026-08-16-shared-common-i18n-namespace/` |
| F-18 | fix | `docs/tasks/2026-08-16-mandala-center-fallback/` |

The dossiers do not exist yet; each is written by `/spec` in bugfix mode, and this audit
reaches `closed` only once every row above resolves to a real folder. Suggested build order,
which is also the severity order with the overlap constraints honoured: the five majors
(`example-cli-seeder-scope-leak`, `activity-submission-wakeup-gap`,
`migration-0076-retry-safety`, `admin-platform-type-edit-unreachable`,
`activity-type-key-collision-across-scopes`) first, then the remaining eight, which are
mutually independent and can run in parallel.

One cross-dossier note for whoever writes them: `activity-type-key-collision-across-scopes`
(F-5) and `example-cli-seeder-scope-leak` (F-1) both stem from the same root, the widening of
`type_repo.list_for_project` to union project-owned and opted-in platform rows. They are
separate dossiers because the fixes are separate (one adds a scope predicate at a single CLI
call site, the other needs a design decision about cross-scope key uniqueness), but whichever
lands second must re-verify the other's assumption about what `list_types` returns.

## 6. Out-of-scope Observations

Recorded for routing, deliberately not judged by this skill.

- **FU-1** - **Test coverage, not behaviour.** Newly added pack or course files get only the
  generic parametrized tests (`TestEveryShippedCourse.test_loads_and_validates`,
  `test_every_agent_carries_a_usable_model_hint`). The per-key assertions that make the
  refutations in section 4 items 4 and 5 hold are hard-coded to the four agents and four types
  shipped today, so a fifth would be uncovered for exactly the failure modes those tests
  currently catch. Route to a test-coverage task.
- **FU-2** - **Hardening, not a defect.** Three loader-tolerance items refuted above are still
  worth one sweep together: the opt-in subquery's missing scope predicate (item 2), the pack
  loader's unvalidated `soft_bounds` leaf types, and `validate_exact_match_config`'s
  non-exhaustive key check (item 6). Route to `check-security` or a hardening task.
- **FU-3** - **Structural.** `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md`
  FU-1 (fourteen direct `AgentService` instantiations in `app/api/v1/agents.py` against the
  route rule) and FU-9 (duplicated `_fail` / `_require_fields` / `_require_str` helpers across
  the two catalogues) remain open and are structural-quality matters. Route to `check-quality`.
- **FU-4** - **Unresolved from prior dossiers, still true.**
  `docs/tasks/2026-07-13-activities-activation-ux/spec.md` carries `status: done`, not a value
  in the contract's lifecycle (FU-6 of the platform-example dossier, carried forward as FU-8 of
  the agent-packs dossier). Reconcile to `implemented`.
- **FU-5** - **Verification debt, not a finding.** The `db`, `integration` and `wiring` tiers
  have never been executed against this work on any developer host, and
  `tests/integration/test_activity_schema_key_order.py` in particular has never been observed
  passing (AC-4 was closed on inspection). Per the project's remote-CI rule, CI is
  authoritative; confirm these tiers are green on CI before treating the `x-order` premise as
  measured.
- **FU-6** - **Still open from the source dossiers**: no `frontend/e2e/` spec covers the
  activities or agents surfaces at all (FU-4 of the platform-example dossier). The
  install to opt-in to activate to submit chain crosses two authorities and the room boundary,
  and would have caught F-4 and F-10.
