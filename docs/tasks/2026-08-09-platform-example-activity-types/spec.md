---
type: feature
status: draft
created: 2026-08-09
requirements: [R30.02, R30.09, R30.23, R30.28, R30.29, R30.30, R30.31]
depends_on: []
---

# Platform-scoped example activity types, installable and dispatchable from the UI

## 1. Summary

Today a shipped example course reaches a running system only through
`python -m smap.examples creative-thinking-course --project-id … --owner-user-id …`
(`backend/smap/examples/__main__.py:33-38`). Nothing about the examples is visible in the
product: there is no catalogue endpoint, no catalogue screen, and no way for a platform
admin to put an example in front of a facilitator without shell access to the deployment.

This feature makes shipped examples **platform-scoped activity types** — rows owned by the
platform rather than by any one project, installed by a platform admin from a UI, editable
by that admin, and read-only to Project Owners. A Project Owner opts their project into an
example from the existing Activity Types page; once opted in, the type appears in the room
Activity picker and a facilitator dispatches it exactly like a hand-authored type.

Prior art this builds directly on: `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md`
(the course content), `docs/tasks/2026-08-08-activity-example-catalogue/spec.md` (the
JSON catalogue + loader this promotes out of the CLI package, closing its FU-4), and
`docs/tasks/2026-08-08-activity-governance-policy/spec.md` (the policy this must not
become a hole in).

## 2. Goals and Non-goals

**Goals**

- A platform admin sees the shipped example catalogue in the admin UI and installs a
  course into the platform with one action, producing platform-scoped `ActivityType` rows.
- A platform admin can edit an installed platform type's `name`, `retention_days`, and the
  two governance flags, so a tightened policy never leaves an example permanently
  unusable.
- A Project Owner sees available platform examples from `/projects/:projectId/activity-types`
  and enables one for their project; the enabled type then behaves like any other type in
  the room Activity picker, session, submission, scoring, and agent-digest paths.
- Platform types are read-only to Project Owners: no edit, no delete, no key reuse.
- Tenant isolation is preserved. A platform type is reachable from a project only after
  that project explicitly opts in; installing an example never silently changes what any
  existing project's facilitators can start.
- The example catalogue becomes readable from the HTTP layer without `app/` importing the
  `smap` CLI package.

**Non-goals**

- **Not seeding the remaining six course units.** Their answer-field designs still need the
  collaborating educator's confirmation (`docs/tasks/2026-08-08-activity-example-catalogue/spec.md:317-319`,
  FU-1). This task ships the mechanism, not more content.
- **No course-authoring UI.** A course is still a JSON file in the repository. Admins
  install and adjust; they do not compose new courses in the browser.
- **No cross-project dispatch from the admin surface.** An admin installs platform types;
  which projects use them stays the Project Owner's decision. Rejected explicitly in Q-2 of
  the pre-spec conversation.
- **No change to the room-side participant flow**, `ActivityHost`, the plugin SDK, the
  generic `SchemaForm`, scoring, or the agent digest. A platform type flows through those
  paths unchanged once it is in a project's list.
- **The `smap.examples` CLI stays.** It remains the seeding path for a project-scoped copy
  and for air-gapped operators; only the catalogue *parser* relocates.
- **No per-organisation scope layer.** The `scope` column admits exactly `project` and
  `platform` in this task, matching the `activity_policies` precedent
  (`backend/contexts/activities/domain/models.py:83-85`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Are examples copied into a project, or shared platform-wide? | **Shared.** One platform-owned row is the single source of truth; a project references it rather than copying it. | User decision. A copy diverges the moment the platform edits the example, and the stated goal is that the platform admin *owns* the examples. Cost accepted and quantified in §4: seven tenant-isolation gates and a partial-unique index assume `project_id` is non-null. |
| Q-2 | Where is the Project Owner's entry point? | `/projects/:projectId/activity-types`, next to the existing Create button (`frontend/src/slices/activities/views/ActivityTypesView.vue:147-155`). Not the admin cross-project surface, not the chatroom Activity tab. | User decision. Reuses the existing `decided && isAuthorized` Project-Owner gate (`views/ActivityTypesView.vue:41`, `useProjectRole.ts:35`) rather than inventing a second authority. Putting it in the chatroom tab would push type-provisioning down to room creators, contradicting [R30.23]. |
| Q-3 | How do platform rows get created? `migration` adds the column but grows no data. | **Platform admin installs from the UI**, one action per course. Not a startup auto-seed. | User decision. Keeps the operative half of [R30.28] — nothing is registered automatically at startup — so installing remains a deliberate, audited act with a real actor id. A boot-time seed would also have to answer "does it overwrite an admin's edit on every restart", which has no good answer once Q-4 makes those rows editable. |
| Q-4 | What happens when a platform example violates the governance policy? Both shipped types set `expose_payload_to_agent: true` (`backend/smap/examples/courses/creative-thinking.json:15,72`), so an admin locking that flag to `false` blocks activation via `activation_service.py:52`. | **The platform admin can edit an installed platform type's `name`, `retention_days`, and both governance flags.** Read-only to Project Owners, writable by platform admins. | User decision. Under a strictly read-only model the two shipped examples become permanently unactivatable with no actor able to fix them — a dead end created by the platform's own policy. Making the owner of the row the one who can fix it is the only self-consistent resolution. Consequence recorded: the shipped JSON is an *initial value*, not a permanent truth, once installed. |
| Q-5 | Does a platform type appear in every project automatically, or per opt-in? | **Per-project opt-in**, via a new `project_activity_type_optins` join table. | Not a user question — a least-surprise consequence of Q-1 + Q-3. Automatic global visibility means one admin action silently adds entries to every tenant's facilitator picker across every org, which is exactly the kind of cross-tenant side effect [R30.09] exists to prevent. Opt-in also gives the Q-2 button a real job; with automatic visibility "import from examples" would have nothing to do. Cost: one extra table and one extra join in `list_for_project`. |
| Q-6 | Does this depend on any unfinished dossier? | **No — `depends_on: []`.** | Scan of every `docs/tasks/*/spec.md` whose status is not terminal returned `2026-07-07-graphrag-two-axis-redesign` (approved) and `2026-07-19-large-artifacts-silently-dropped` (in-progress). Neither touches `contexts/activities`, `app/api/v1/activities.py`, the activities slice, or `smap/examples`. The three same-week activities dossiers (`creative-thinking-course-example`, `activity-example-catalogue`, `activity-governance-policy`) are all `implemented`. |

## 4. Current State

### 4.1 The example catalogue is CLI-only

- The loader `load_course` / `parse_course` / `available_courses` lives in
  `backend/smap/examples/_catalogue.py:202-291`, a package whose own docstring states
  "Nothing in this namespace is served over HTTP" (`backend/smap/__init__.py:1-7`).
- The only consumers are `backend/smap/examples/__main__.py:10` and
  `backend/smap/examples/_seeding.py:21`.
- The course files resolve through `files(__package__).joinpath("courses")`
  (`_catalogue.py:228-230`) and ship via `[tool.setuptools.package-data] "smap.examples" = ["courses/*.json"]`
  (`backend/pyproject.toml:94`).
- `available_courses()` exists (`_catalogue.py:233-246`) but is reachable only through the
  error message for an unknown `--course` — recorded as FU-4 of the catalogue dossier.
- **The SoC obstacle is a documented convention, not a failing gate.** `import-linter`'s
  `root_packages` is `["app", "contexts", "shared_kernel"]` (`backend/pyproject.toml:449`),
  so it structurally cannot see an `app → smap` edge, and its only two contracts concern
  domain purity (`:452-509`). Ruff selects `TID` (`:115`) but declares no
  `flake8-tidy-imports.banned-api` section. The rule lives in `backend/CLAUDE.md`'s import
  rules and in `backend/smap/__init__.py:1-7`; the previous dossier recorded the same fact
  (`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:205-208`).
- **`_catalogue.py` already carries one layer inversion**: it imports `FILLED_COUNT_ID` and
  `validate_filled_count_config` from `app.plugins.activity_validators` (`_catalogue.py:27`),
  recorded as D-1 of the catalogue dossier. Inside `smap` that is merely unusual; inside
  `contexts/` it is a `contexts → app` inversion and must be removed by the move.

### 4.2 `activity_types` is project-scoped in seven places

`activity_types.project_id` is `nullable=False`
(`backend/contexts/activities/infrastructure/tables.py:25-30`, migration
`backend/alembic/versions/0049_activities.py:54-59`). Seven sites enforce
`type.project_id == caller's project` and would reject a platform type:

| Site | Line | Effect if unchanged |
|---|---|---|
| `application/activation_service.py` | `:44` | A facilitator can never start a platform type |
| `application/submission_service.py` | `:83` | A participant can never submit to one |
| `application/session_service.py` | `:56` | A participant can never open a session |
| `application/type_service.py` | `:117` | (correct as-is — blocks owner edits of platform rows) |
| `interfaces/facade.py` | `:322` | (correct as-is — blocks owner deletes) |
| `app/api/v1/activities.py` | `:439` | Room-scoped type read 404s |
| `app/api/v1/activities.py` | `:259` | Activation response embeds `activity_type: null` |

Three further hazards, each silent rather than loud:

- **The partial-unique index does not survive NULL.** `uq_activity_types_project_key_active`
  is `UNIQUE (project_id, key) WHERE deleted_at IS NULL` (`0049_activities.py:77-80`).
  PostgreSQL treats every NULL as distinct, so with a nullable `project_id` two platform
  types could share a key with no error. `type_repo.create` string-matches that index name
  to raise `ActivityTypeKeyConflict` (`type_repo.py:100-103`), so a platform conflict would
  surface as a raw `IntegrityError` 500.
- **`activation_repo.list_active_for_type` assumes project scope in its docstring**
  (`:91-93`: "a type is project-scoped, so the id alone bounds the set to one project").
  It is driven from `facade.delete_type` (`facade.py:326`), as is
  `session_repo.close_open_for_type` (`session_repo.py:124-137`) from `facade.py:338`.
  Deleting a platform type would end activations and close sessions across every tenant.
- **Nothing stops a mutation by id.** `type_repo.update` (`:190-228`) and
  `type_repo.soft_delete` (`:230-243`) carry no scope guard; the only guards are the
  caller-supplied equality checks above, which a platform row cannot participate in.

`ActivityType.project_id: uuid.UUID` is non-optional in the domain model
(`domain/models.py:50`), which sits in the mypy **strict** zone
(`backend/pyproject.toml:185-192`, `module = ["contexts.*.domain.*", …]`). The wire models
`ActivityTypeOut.project_id` (`app/api/v1/activities.py:96`) and
`AdminActivityTypeOut.project_id` (`app/api/v1/admin_activities.py:56`) are likewise
non-optional, and the admin listing hydrates project names via
`TenancyFacade(db).get_projects([at.project_id for at in types])` (`admin_activities.py:233`).

### 4.3 The governance policy, and why the examples collide with it

`ActivityPolicyService.assert_allows` (`application/policy_service.py:51-96`) raises
`ActivityTypeViolatesPolicy` when a locked flag disagrees with its default or a retention
horizon exceeds the ceiling. It runs at authoring time (`type_service.py:57-61`, `:136`)
and again at activation time (`activation_service.py:52` via `assert_type_allowed`,
`policy_service.py:121-131`) — the second gate being what makes a tightened policy reach
types that already exist ([R30.30]). No policy row means `PERMISSIVE_POLICY`
(`domain/models.py:107-117`, `version=0`), so nothing is blocked today.

Both shipped example types declare `expose_payload_to_agent: true`
(`courses/creative-thinking.json:15`, `:72`). An admin who sets
`expose_payload_to_agent_locked = true, expose_payload_to_agent_default = false` therefore
blocks both examples at activation. That is the dead end Q-4 resolves.

### 4.4 The two UI surfaces that gain entry points

- `frontend/src/slices/activities/views/ActivityTypesView.vue` — Project Owner surface,
  gated `decided && isAuthorized` (`:41`, `:135`, `:190`, and the query's `enabled` at
  `:50`). `isAuthorized = isAdmin || isOwner` where `isOwner` requires the literal project
  role `'owner'` (`frontend/src/slices/tenancy/composables/useProjectRole.ts:28-35`). The
  Create button sits at `:147-155`.
- `frontend/src/slices/admin/views/AdminActivitiesView.vue` — platform admin surface, today
  **read-only by explicit design** (comment at `:149-151`: "grants no create/edit/deactivate
  capability by design"). It already hosts `ActivityPolicyForm` (`:8`) and two tables driven
  by `adminKeys.activityTypes()` / `adminKeys.activityActivations()`
  (`frontend/src/slices/admin/queries/index.ts:22-25`).

Cross-slice constraint that shapes the frontend design: `activities` may import from
`agents` and `tenancy` only, and `admin` from `prompt-studio` and `skills` only
(`frontend/eslint.config.js:30`, `:37`; mirrored `frontend/src/slices/README.md:35`, `:38`).
**Neither slice may import the other**, so the two entry points cannot share a component.

## 5. Design

### Options considered

**Option A — nullable `project_id` alone.** Drop `NOT NULL`; a platform type is a row with
`project_id IS NULL`. Minimal DDL. Rejected: no explicit intent marker (NULL means both
"platform" and "someone forgot"), the existing partial-unique silently stops constraining,
and every read has to encode the NULL convention rather than a named concept.

**Option B — `scope` column + nullable `project_id` + a CHECK tying them (chosen).**
`scope TEXT NOT NULL DEFAULT 'project'` with `CHECK (scope IN ('project','platform'))` and
`CHECK ((scope = 'project') = (project_id IS NOT NULL))`, plus a second partial-unique for
platform keys. Intent is explicit and machine-enforced; a row cannot be half-converted.

**Option C — a sentinel "platform" project row.** No schema change at all; platform types
belong to a reserved project. Rejected: the three room-level gates compare against the
*caller's* project, so a sentinel project's types are still invisible to every real room —
the hard part is untouched. It also puts a fake tenant in `projects`, which every
project-listing, membership, and billing-adjacent query would have to learn to exclude.

**Option D — copy-on-import, no platform scope.** The catalogue stays a source; importing
creates an ordinary project-scoped type. Cheapest by a wide margin, no migration risk, no
multi-tenant surface. Rejected by the user in Q-1: it gives up the single source of truth
and platform ownership that motivated the request. Recorded here because it remains the
correct fallback if the migration proves too risky to land.

### Decision

**Option B, plus a per-project opt-in join table (Q-5).**

`scope` mirrors the idiom `activity_policies` established two days ago
(`tables.py:159-161`, `0075_activity_policies.py:82`), and whose domain docstring already
anticipates further scopes being "a row, not a migration"
(`domain/models.py:83-85`). Using the same word for the same concept in the same context is
worth more than the column it costs.

The seven duplicated `type.project_id != project_id` checks collapse into **one shared
domain helper**, `ActivityType.is_visible_to(project_id, *, opted_in)` — or an application
-layer `assert_type_reachable(...)`. This is the part of the change that leaves the codebase
better: today the same tenancy rule is re-typed at seven call sites with no single place to
audit, which is precisely how one of them would drift.

What is consciously given up:

- **Blast radius.** This modifies the multi-tenant boundary of a context that
  `CLAUDE.md` singles out ("every API endpoint must verify org/project membership before
  returning data"). Every one of the seven sites and both cascade paths must be re-argued,
  not merely re-compiled. §8 and §12 carry that weight.
- **`ActivityType.project_id` becomes `uuid.UUID | None` in a mypy-strict module**
  (`backend/pyproject.toml:186-192`), so every consumer must handle the None arm explicitly.
  That is a feature — the type checker enumerates the call sites for the implementer — but
  it means the diff is wide and shallow rather than narrow.
- **Two response models gain an optional field**, which is an OpenAPI contract change and
  therefore a `pnpm run gen:api` + `check:openapi-drift` cycle.

### Where the catalogue lands

`backend/contexts/activities/infrastructure/examples/` — `catalogue.py` plus the `courses/`
directory moved wholesale.

- **Not `domain/`**: that is the mypy-strict zone (`pyproject.toml:186-189`) and the two
  `import-linter` domain-purity contracts (`:452-509`); a JSON parser reading package data
  belongs in neither.
- **Not `application/`**: reading a packaged resource is an adapter over an external store,
  which is what `infrastructure/` means in this context's layout (`backend/CLAUDE.md`).
- `courses_root()`'s `files(__package__)` (`_catalogue.py:228-230`) follows the module
  automatically; only the `pyproject.toml:94` package-data key and
  `backend/tests/unit/test_smap_examples_packaging.py:30` need re-pointing.
- **The `app.plugins` inversion is removed**, not carried across. The loader validates a
  validator config through the context's own registry —
  `get_config_validator(validator_id)` (`application/validators/registry.py:78-80`) — instead
  of importing `validate_filled_count_config` directly. For the one rule the registry cannot
  express (`min_filled` must not exceed the declared property count, `_catalogue.py:144-156`,
  a cross-field check the backend's config validator cannot make because it never sees the
  schema), the registry gains an optional `schema_config_validator` hook registered
  alongside the scorer in `app/plugins/activity_validators.py:139-144`. The rule stays
  declared once, on the same side of the boundary as the validator it belongs to.
- `smap/examples/_catalogue.py` becomes a thin re-export so `__main__.py:10`,
  `_seeding.py:21`, and `test_smap_cli_contract.py:75,96,104` keep working unchanged.

## 6. Detailed Changes

### Backend — `contexts/activities`

- **`domain/models.py`** — `ActivityType.project_id: uuid.UUID | None`; new
  `ActivityTypeScope` enum (`PROJECT`, `PLATFORM`) and `ActivityType.scope`; new
  `ProjectActivityTypeOptIn` model. New reachability predicate (see §5 Decision).
- **`domain/errors.py`** — `PlatformActivityTypeReadOnly` (a Project Owner attempted to
  edit/delete a platform type) and `ActivityTypeNotOptedIn`, joining the existing set at
  `:10-88`. Both must be added to `__all__` (`:91-104`) — note three existing policy errors
  are missing from it, see FU-3.
- **`infrastructure/tables.py`** — `scope` column on `activity_types`; `project_id` nullable;
  new `project_activity_type_optins` table.
- **`infrastructure/repositories/type_repo.py`** — `create` takes `scope` and an optional
  `project_id`; the IntegrityError branch (`:100-103`) learns the second index name;
  `list_for_project` (`:174-188`) returns project-scoped rows **plus** platform rows joined
  through the opt-in table; new `list_platform()` and `list_platform_by_keys()`.
- **`infrastructure/repositories/optin_repo.py`** (new) — `list_for_project`, `add`,
  `remove`.
- **`infrastructure/examples/catalogue.py`** — moved from `smap/examples/_catalogue.py`,
  with the two import changes from §5.
- **`application/type_service.py`** — `register` accepts a platform scope; `update` and
  `soft_delete` raise `PlatformActivityTypeReadOnly` for a platform row on the project path
  and permit it on the admin path.
- **`application/example_service.py`** (new) — list catalogue entries with install state;
  install a course as platform types (idempotent by key, mirroring `_seeding.py:47-65`);
  opt a project in/out.
- **`application/activation_service.py:44`, `submission_service.py:83`,
  `session_service.py:56`** — the equality check becomes the shared reachability call.
- **`interfaces/facade.py`** — new methods for the catalogue/install/opt-in surface;
  `delete_type` (`:304-343`) bounds its cascade to the acting project for a platform type.
- **`interfaces/error_mapping.py`** — rows for the two new errors (403 and 404
  respectively), following `:15-88`.

Migration required: **yes** — `0076_platform_activity_types`, `down_revision = "0075_activity_policies"`.
DDL, in the idiom of `0075` (inline `op.create_table`, `op.execute` for partial indexes) and
`0074` (`autocommit_block` + `CREATE INDEX CONCURRENTLY` when touching the live table):

1. `ALTER TABLE activity_types ADD COLUMN scope text NOT NULL DEFAULT 'project'`
2. `ALTER TABLE activity_types ALTER COLUMN project_id DROP NOT NULL`
3. `CHECK ck_activity_types_scope`: `scope IN ('project','platform')`
4. `CHECK ck_activity_types_project_scope`: `(scope = 'project') = (project_id IS NOT NULL)`
5. `CREATE UNIQUE INDEX uq_activity_types_platform_key_active ON activity_types (key) WHERE project_id IS NULL AND deleted_at IS NULL`
6. `CREATE TABLE project_activity_type_optins (project_id, activity_type_id, enabled_by_user_id, created_at)`, PK `(project_id, activity_type_id)`, both FKs `ON DELETE CASCADE`

Every existing row satisfies (3) and (4) under the default, so step 1's default makes this a
pure add — no backfill statement.

### API contract

`gen:api` rerun required: **yes** (and `check:openapi-drift` in CI).

| Method | Path | Auth |
|---|---|---|
| `GET` | `/api/admin/activity-examples` | `require_admin` (`app/api/v1/admin_deps.py:15-20`) |
| `POST` | `/api/admin/activity-examples/{course_key}/install` | `require_admin` |
| `PATCH` | `/api/admin/activity-types/{type_id}` | `require_admin`; rejects a project-scoped target |
| `DELETE` | `/api/admin/activity-types/{type_id}` | `require_admin`; platform-scoped only |
| `GET` | `/api/projects/{project_id}/activity-examples` | `assert_project_owner` (`app/api/v1/deps.py:82-98`) |
| `POST` | `/api/projects/{project_id}/activity-type-optins` | `assert_project_owner` |
| `DELETE` | `/api/projects/{project_id}/activity-type-optins/{type_id}` | `assert_project_owner` |

Changed models: `ActivityTypeOut.project_id` and `AdminActivityTypeOut.project_id` become
optional and both gain `scope`; `AdminActivityTypeOut.project_name` hydration
(`admin_activities.py:233-244`) must skip platform rows rather than pass `None` into
`get_projects`.

### Frontend

- **`slices/activities`** — `api/index.ts` gains the three project-scoped calls;
  `queries/index.ts` gains `activityKeys.examples(projectId)` following the documented
  `['<slice>', '<resource>', ...scope]` convention (`queries/index.ts:1-4`); new
  `components/ExampleImportDialog.vue` (an `SModal` listing platform examples with an
  enable/disable action); `ActivityTypesView.vue` gains the trigger button beside Create
  (`:147-155`) and a "platform example" `SBadge` plus suppressed row actions for platform
  rows; new i18n keys under the existing `activities.typesList` namespace, in **both**
  `locales/en.json` and `locales/zh-TW.json`.
- **`slices/admin`** — `AdminActivitiesView.vue` gains an examples section and an edit
  dialog for platform types; `api/admin.ts` and `queries/index.ts` gain the matching
  members under the `['admin', …]` prefix (`admin/queries/index.ts:22-27`); new keys in
  `admin/locales/{en,zh-TW}.json` under `admin.activities`.
- No shared component between the two slices — the boundary forbids it (§4.4). The
  duplication is two small dialogs and is the intended cost of gate #1.

### Deploy/config

None. No env var, no Vault path, no compose change.

## 7. NFR Checklist

- [x] **i18n** — every new string via `$t()` in both locale files; gate #12 fails the build
  on a bare template literal. The admin and activities slices keep separate namespaces.
- [x] **Audit log** — install emits one `activity_type.created` per type (reusing
  `type_service.register`'s existing emission, `:81`); admin edit emits
  `activity_type.updated`; opt-in/opt-out emit new `activity_type.opted_in` /
  `activity_type.opted_out` events with the project and type ids. [R30.11] extends to
  platform types unchanged.
- [x] **Tenant isolation** — the three project-scoped routes gate on `assert_project_owner`;
  the four admin routes on `require_admin`. A platform type becomes reachable from a room
  **only** through an opt-in row, so the room-level gates keep an explicit,
  per-project authorisation to check rather than a global allowance. §8 carries the
  detail.
- [x] **Error handling UX** — the import dialog needs loading, empty ("no examples
  installed — ask a platform admin"), and error states via `SEmptyState` / `SQueryError`,
  matching `ActivityTypesView.vue:159-188`. The admin edit dialog must surface
  `activities/type-violates-policy` through the existing `usePolicyRefusal` composable
  (`composables/usePolicyRefusal.ts:32-52`) — an admin editing an example into compliance
  is exactly the flow Q-4 exists for, and it must say which field refused.
- [x] **Performance** — the catalogue is a handful of JSON files parsed per request; cache
  the parsed result in the process, since the files cannot change without a redeploy.
  `list_for_project` gains one join against a table with at most (projects x platform types)
  rows. The admin type listing already caps at `_POLICY_PREVIEW_SCAN`-style bounds
  (`AdminActivitiesView.vue:190`, `PAGE_LIMIT = 200`); platform rows are few and do not
  change that. No N+1: opt-ins load in one query keyed by project.

## 8. Security Considerations

This touches the tenant boundary, so it is the section to read twice.

- **The opt-in row is the authorisation record, and it must be checked on every room-level
  path, not only in the picker.** Filtering `list_for_project` is a presentation change; if
  `activation_service`, `session_service`, and `submission_service` merely learn "platform
  types are allowed", any facilitator in any org could start any platform type by id, since
  those routes take `activity_type_id` straight from the client body
  (`app/api/v1/activities.py:516`, `:587`, `:638`). The shared reachability helper must
  therefore consult the opt-in table, and the tests in §12 must assert the negative case
  from a non-opted-in project.
- **`validator_config` stays owner-confidential** ([R30.25]). The project-scoped example
  listing must reuse the existing `is_project_owner_or_admin` projection rule
  (`app/api/v1/activities.py:419-421`) and must not leak a platform type's
  `validator_config` to a non-owner. `AdminActivityTypeRow` is already flagged
  admin-confidential and not to be re-exported (`frontend/src/slices/admin/types/index.ts:81-85`).
- **The governance policy must apply to platform types identically.** `assert_type_allowed`
  runs on the stored row at activation (`activation_service.py:52`); nothing in this change
  may add a platform exemption. Q-4's answer is that an admin *edits the row into
  compliance*, not that the check is skipped — the distinction is the whole point.
  `preview_policy_impact` (`facade.py:181-233`) must count platform types too, or an admin
  would tighten a policy without being told it breaks the shipped examples.
- **The delete cascade must be bounded.** `facade.delete_type` currently ends every active
  activation for a type (`:326`, via `activation_repo.list_active_for_type:90-104`) and
  closes every open session (`:338`). For a platform type that set spans every tenant. An
  admin delete legitimately ends them all; a project opt-*out* must end only that project's
  activations. These are different operations and must not share a code path.
- **`expose_payload_to_agent: true` on both shipped examples means participant text reaches
  the project's configured LLM provider.** That property is now installed by an admin
  rather than chosen per project, so the import dialog must state it at the point of
  opting in — the ethics note in `docs/examples/creative-thinking-course.md:226-238` stops
  being documentation an operator read and starts being a consent surface.
- **Course files remain trusted repository content.** The loader's traversal guard
  (`_COURSE_KEY_RE`, anchored `\A..\Z`, `_catalogue.py:34-39`) and its rejection of unknown
  fields (`:96-102`) must survive the move verbatim; the unknown-field rule is what stops a
  typo'd `expose_payload_to_agents` from silently defaulting to the permissive value
  (recorded as D-4 of the catalogue dossier). The new admin install route takes a
  `course_key` from the client, so that guard now bounds an HTTP-reachable path rather than
  a CLI argument.

## 9. Quality Notes

**Existing debt in the touched files** — record, do not silently fix:

- The same tenancy equality is written out seven times (§4.2). This task removes it; that is
  the intended cleanup, not a side quest.
- `domain/errors.py:91-104` — `__all__` omits `ActivityTypeViolatesPolicy`,
  `ActivityPolicyVersionMismatch`, and `ActivityPolicyInconsistent`. Adding two more errors
  without noticing would deepen it. See FU-3.
- `ActivityPanel.vue:31,61-73` fetches the type list with a bare `ref` + `watch` rather than
  TanStack, unlike every other read in the slice. Opting a type in will not refresh that
  panel through query invalidation. Do not rewrite it here; see FU-2.
- `backend/README.md:58` records that `activities` is one of three contexts **not** wired
  into the `import-linter` contracts. This task adds no enforcement; it relies on review.
- `AdminActivitiesView.vue:149-151` carries a comment asserting the surface grants no
  create/edit capability. Q-3 and Q-4 change that. **Update the comment** — a stale
  invariant comment is worse than none.

**Patterns to follow** — exemplars:

- Migration: `0075_activity_policies.py` (inline `create_table`, named `CheckConstraint`s,
  `op.execute` for the partial unique, exact-reverse `downgrade`) and
  `0074_activity_admin_listing_indexes.py:41-57` (`autocommit_block` + `CONCURRENTLY` on a
  live table).
- Platform-scoped singleton idiom: `activity_policies` end to end — `tables.py:155-176`,
  `policy_repo.py:61-136`, `policy_service.py:37-197`.
- Repository style: `_TYPE_COLS` + `_row_to_type` + IntegrityError-to-domain-error
  translation, `type_repo.py:25-105`.
- Route style: `assert_project_owner` then facade, never a service —
  `app/api/v1/activities.py:296-322`.
- Frontend query keys: `['<slice>','<resource>',...scope]`, `queries/index.ts:1-15`.
- Frontend form + policy refusal: `ActivityTypeForm.vue:315-332` (refusal check *before* the
  generic 409 branch — the ordering is load-bearing and documented at `:319-322`).
- Tests: module docstring naming the ACs pinned; `class TestSomeBehaviour:` with no base;
  sentence-style method names; module-level `_make_type(**over)` builders rather than
  fixtures (`docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:216-218`).

**Reuse inventory** — use these, do not re-invent:

| Need | Use | Location |
|---|---|---|
| Project Owner gate (API) | `assert_project_owner` | `app/api/v1/deps.py:82-98` |
| Admin gate (API) | `require_admin` | `app/api/v1/admin_deps.py:15-20` |
| Owner-vs-member projection | `is_project_owner_or_admin` | `app/api/v1/deps.py:66-79` |
| Project Owner gate (UI) | `useProjectRole` → `decided && isAuthorized` | `frontend/src/slices/tenancy/composables/useProjectRole.ts:17-46` |
| Policy refusal decoding | `usePolicyRefusal` | `frontend/src/slices/activities/composables/usePolicyRefusal.ts:32-52` |
| Idempotent seeding loop | `seed_course` | `backend/smap/examples/_seeding.py:30-68` |
| Schema well-formedness | `validate_schema_wellformed` | `contexts/activities/application/validators/schema.py:18-24` |
| Validator config check | `get_config_validator` | `contexts/activities/application/validators/registry.py:78-80` |
| Policy check on a stored row | `assert_type_allowed` | `application/policy_service.py:121-131` |
| Error → RFC 7807 | `register_context_handler` via `_MAP` | `interfaces/error_mapping.py:15-105` |
| Modal / table / empty / error UI | `SModal`, `typedTable`, `SEmptyState`, `SQueryError`, `SBadge` | `@shared/ui` |
| Confirm before opt-out | `useConfirmDialog` | `@shared/composables`, used at `ActivityTypesView.vue:38` |

## 10. Risks and Rollback

- **Highest risk: a tenant-isolation regression.** The change rewrites the check that keeps
  one org's types out of another org's rooms. Mitigation: one shared helper rather than
  seven edits, plus the §12 negative tests asserting a non-opted-in project is refused at
  each of the three room-level services, not only in the listing.
- **Migration reversibility.** `downgrade()` reverses steps 1-6 exactly, but it cannot
  restore `NOT NULL` while platform rows exist. It must therefore **fail loudly** if any
  `activity_types` row has `project_id IS NULL`, rather than deleting rows an admin
  installed — data loss on a downgrade is worse than a failed downgrade. Document the
  manual step (delete platform types, then downgrade). Verify with a real
  `alembic upgrade head && alembic downgrade -1` against a database that has platform rows
  and one that does not.
- **OpenAPI drift.** Two response models change shape; `pnpm run gen:api` and
  `check:openapi-drift` must both run. The two most recent commits on `main`
  (`a889a90`, `064fb63`) were both fixes to this exact gate — treat it as a known trap, and
  regenerate on the pinned dependency set.
- **`ActivityPanel` staleness.** Opting a type in will not refresh an open chatroom's
  picker, because that list is not a TanStack query (§9). Acceptable — a reload fixes it —
  but it must be stated in the AC rather than discovered.
- **Scope creep into a course-authoring product.** The install action writes rows from a
  repository file; the moment someone asks to edit `payload_schema` from the admin UI this
  becomes a CMS. Q-4 deliberately limits admin edits to `name`, `retention_days`, and the
  two governance flags — the fields a *policy* conflict can involve.
- Rollback: `git revert` per commit, then `alembic downgrade -1` after removing platform
  rows. Nothing outside `contexts/activities` and the two frontend slices changes, so a
  revert is contained.

## 11. Acceptance Criteria

- [ ] **AC-1** — Migration `0076` applies to a database holding existing project-scoped
  types with no backfill and no data change; `alembic downgrade -1` succeeds on a database
  with no platform rows and fails with a clear message on one that has them.
- [ ] **AC-2** — Two platform types cannot share a `key`: a second install of the same
  course reports both types already-present (idempotent, as `_seeding.py:49-51` does), and a
  direct duplicate insert raises `ActivityTypeKeyConflict`, not `IntegrityError`.
- [ ] **AC-3** — A platform admin lists the shipped catalogue and installs
  `creative-thinking` from `/admin/activities`, producing two platform-scoped types with the
  JSON's exact field values, and one `activity_type.created` audit event each carrying the
  admin as actor.
- [ ] **AC-4** — A Project Owner sees both platform examples from
  `/projects/:projectId/activity-types`, enables one, and it appears in that project's type
  list marked as a platform example with no edit or delete action offered.
- [ ] **AC-5** — Before opt-in, a facilitator in that project **cannot** start the platform
  type: `POST /api/chatrooms/{id}/activity-activations` with its id returns 404, and the
  same holds for `activity-sessions` and `activity-submissions`. After opt-in, all three
  succeed.
- [ ] **AC-6** — A facilitator in a **different, non-opted-in** project is refused at all
  three endpoints even though the type exists and is installed platform-wide.
- [ ] **AC-7** — A Project Owner cannot edit or delete a platform type through the
  project-scoped `PATCH`/`DELETE` routes: both return 403 with a distinct RFC 7807 code.
- [ ] **AC-8** — A platform admin edits an installed platform type's `expose_payload_to_agent`,
  `echo_includes_content`, `retention_days`, and `name`, and cannot edit its `key`,
  `payload_schema`, or `validator_config`.
- [ ] **AC-9** — Governance policy applies unchanged: with
  `expose_payload_to_agent_locked = true, default = false`, activating an unedited platform
  example is refused with `activities/type-violates-policy` naming the field; after the
  admin edits the type to `false`, activation succeeds. `preview_policy_impact` counts the
  platform types among the violations.
- [ ] **AC-10** — Deleting a platform type as an admin ends its active activations across
  every tenant; opting a single project out ends only that project's activations and closes
  only its open sessions.
- [ ] **AC-11** — `app/` contains no import of `smap.*`: the catalogue is reachable from the
  API through `contexts/activities/infrastructure/examples/`, and
  `contexts/activities/**` contains no `from app.` import.
- [ ] **AC-12** — The `smap.examples` CLI still works verbatim:
  `python -m smap.examples creative-thinking-course --project-id X --owner-user-id Y` seeds
  project-scoped types as before, and `test_smap_cli_contract.py` passes unmodified.
- [ ] **AC-13** — `courses/*.json` still ships as package data from its new location,
  verified against a built wheel as the catalogue dossier's AC-6 did.
- [ ] **AC-14** — All user-facing strings in both slices exist in `en.json` and `zh-TW.json`;
  the import dialog states that an example with `expose_payload_to_agent` sends participant
  text to the project's LLM provider.
- [ ] **AC-15** — Gates green: `ruff check . && ruff format --check .`, `mypy .`,
  `pytest -q` (unit tier locally, full tiers on CI), `pnpm lint`, `pnpm typecheck`,
  `pnpm test`, `pnpm build`, `pnpm run check:openapi-drift`, `pnpm run check:bundle-size`,
  `pnpm run check:type-coverage`, `pnpm run check:boundaries-enforced`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | manual + `db` tier | `alembic upgrade head` / `downgrade -1` against the compose Postgres; a `pytest.mark.db` test asserting the two CHECK constraints reject a half-converted row |
| AC-2 | unit | `tests/unit/test_activity_repos.py` (compiled-SQL) + a `db`-tier duplicate insert — the unit tier renders `literal_binds` and cannot see a real constraint violation (`backend/CLAUDE.md`) |
| AC-3 | unit | new `tests/unit/test_activity_examples_service.py`, facade double per `test_smap_examples_cli.py:39-64`; audit via `patch("…application.example_service.audit.emit")` per `test_activity_policy_service.py:174` |
| AC-4, AC-7 | unit | `tests/unit/test_activities_authz.py` extension + `tests/unit/test_activity_type_edit.py` |
| AC-5, AC-6 | unit | new `tests/unit/test_platform_type_reachability.py` — parametrized over the three services (`activation_service`, `session_service`, `submission_service`) x (opted-in, not opted-in, other project). **This is the file that matters most**; it is the regression net for §8's first bullet |
| AC-8 | unit | `tests/unit/test_admin_activities_routes.py` |
| AC-9 | unit | `tests/unit/test_activity_policy_enforcement.py` + `test_activity_policy_impact.py`, extended with a platform-scoped type |
| AC-10 | unit | `tests/unit/test_activity_type_delete.py` — assert the opt-out path calls a *different*, project-bounded cascade |
| AC-11 | unit | a new AST tripwire in the idiom of `tests/unit/test_activities_no_agents_import.py:14-29`, scanning `contexts/activities/**` for `app.` imports and `app/**` for `smap.` imports |
| AC-12 | unit | `tests/unit/test_smap_cli_contract.py`, `test_smap_examples_cli.py` — must pass **unmodified** except for import paths |
| AC-13 | unit | `tests/unit/test_smap_examples_packaging.py`, re-pointed; wheel check performed once by hand and recorded |
| AC-4, AC-14 | component | `frontend/src/slices/activities/__tests__/ExampleImportDialog.test.ts`, `ActivityTypesView.test.ts`; `frontend/src/slices/admin/__tests__/AdminActivitiesView.test.ts` |
| AC-15 | CI | the full gate set; per `feedback_remote_ci_verification`, CI is authoritative over the local Windows host |

End-to-end (`frontend/e2e/`) is **not** extended here: the existing specs run against the
compose stack and none covers the activities surface today. Recorded as FU-4.

## 13. SRS Delta

Apply verbatim on approval.

**Amend [R30.02]** (adds the scope concept; the existing sentence about project scope
becomes the `project` case):

> - **[R30.02]** An `ActivityType` has a `scope` of `project` or `platform`. A
>   project-scoped type belongs to exactly one project and its registration requires Project
>   Owner capability; `(project_id, key)` is unique among non-deleted project-scoped types.
>   A platform-scoped type has no owning project, is created and edited only by platform
>   admins, and its `key` is unique among non-deleted platform-scoped types. A registered
>   payload schema must be well-formed JSON Schema; an in-process validator reference must
>   name a registered validator.

**Amend [R30.23]** (append, leaving the existing text intact):

> A platform-scoped type is read-only to Project Owners: the project-scoped edit and delete
> surfaces refuse it. A platform admin may edit a platform-scoped type's `name`,
> `retention_days`, `expose_payload_to_agent`, and `echo_includes_content` — the fields a
> governance-policy conflict can involve — and may not edit its `key`, `payload_schema`, or
> `validator_config`. Admin edits emit `activity_type.updated` with the admin as actor.

**Amend [R30.28]** (replaces the final sentence; the "never automatically at startup" rule
is kept, the "no runtime code path depends on it" clause is narrowed):

> - **[R30.28]** The repository ships at least one worked activity example — activity-type
>   definitions plus an idempotent operator seeder (`python -m smap.examples`) and
>   accompanying documentation — demonstrating both the custom-plugin and generic-form
>   rendering paths ([R30.17], [R30.18]). Example content is repository data, not platform
>   behavior: it is never registered automatically at startup, and the platform operates
>   normally when the catalogue is absent. A platform admin may install a shipped example as
>   platform-scoped activity types ([R30.32]); the seeder remains available for installing a
>   project-scoped copy.

**New [R30.32]**:

> - **[R30.32]** Platform admins may read the shipped example catalogue and install a course
>   as platform-scoped `ActivityType`s. Installation is idempotent by key, records the
>   installing admin as the audit actor of each `activity_type.created` event, and is never
>   performed automatically. The catalogue is validated on read — every field required, the
>   payload schema well-formed and non-empty, and a validator config checked against the
>   registered validator — so a malformed course file is diagnosed rather than installed.

**New [R30.33]**:

> - **[R30.33]** A platform-scoped `ActivityType` is reachable from a project only after a
>   Project Owner opts that project in; the opt-in is the authorization record and is
>   enforced server-side on activation, session opening, and submission, not merely in the
>   type listing. Opting out ends that project's active activations for the type and closes
>   its open sessions, affecting no other project. Opt-in and opt-out emit audit events.

**Amend [R30.31]** (append):

> The view additionally permits installing shipped examples as platform-scoped types and
> editing an installed platform-scoped type's safe and governance fields; it grants no
> create, edit, or deactivate capability over project-scoped types.

## 14. Open Questions

- **OQ-1** — Should an installed platform example be re-syncable when the shipped JSON
  changes in a later release? Today's answer is no: install is idempotent by key, so a
  changed course file does not reach an already-installed type. Once Q-4 makes those rows
  admin-editable, a re-sync would have to decide whether it overwrites an admin's edit.
  Deliberately deferred; does not block approval.
- **OQ-2** — Should `list_platform()` be visible to a Project Owner whose project has opted
  into nothing, or only to admins? The spec assumes Owner-visible (that is what makes the
  import dialog work), but it does mean every Owner learns which examples exist
  platform-wide. That is catalogue metadata, not tenant data, so the assumption stands
  unless a reviewer objects.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — Seeding the remaining six units of the creative-thinking course is now a
  content task with an install button in front of it. Inherits the same blocker: the unit
  designs need the collaborating educator's confirmation
  (`docs/tasks/2026-08-08-activity-example-catalogue/spec.md:317-319`).
- **FU-2** — `ActivityPanel.vue:31,61-73` fetches its type list with `ref` + `watch` instead
  of TanStack Query, so no cache invalidation reaches it. Converting it would let an opt-in
  refresh an open chatroom without a reload.
- **FU-3** — `contexts/activities/domain/errors.py:91-104` omits three existing policy error
  classes from `__all__`.
- **FU-4** — No `frontend/e2e/` spec covers the activities surface at all. The
  install → opt-in → activate → submit chain is the first flow worth adding, since it
  crosses two authorities and the room boundary.
- **FU-5** — The backend does not enforce `min_filled <= declared property count` at type
  registration: `validate_filled_count_config` (`app/plugins/activity_validators.py:119-129`)
  checks only that the value is a non-negative int, while the rule is enforced in the
  frontend (`frontend/src/slices/activities/types/schemas.ts:138-149`) and in the course
  loader (`smap/examples/_catalogue.py:144-156`). A direct API call can therefore register
  an activity nobody can pass. The `schema_config_validator` hook added in §5 makes closing
  this a three-line change in `type_service._validate_validator_config`.
- **FU-6** — `docs/tasks/2026-07-13-activities-activation-ux/spec.md` carries
  `status: done`, which is not a value in the contract's lifecycle
  (`docs/tasks/README.md:41`), and it appears in no `BOARD.md` section. Reconcile to
  `implemented`.
- **FU-7** — `docs/examples/creative-thinking-course.md` documents the CLI as the only
  installation path (`:129-153`) and states "Types can equally be created by hand through
  the owner-only management page". Both statements need updating once this ships; the
  Limitations note about one plugin per type key (`:220-223`) becomes *less* severe, since a
  single platform `mandala-9grid` now backs every project that opts in.
