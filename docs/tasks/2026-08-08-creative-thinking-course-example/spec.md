---
type: feature
status: draft
created: 2026-08-08
requirements: [R30.02, R30.03, R30.05, R30.17, R30.18, R30.20, R30.21, R30.23, R30.24, R30.25, R30.26]
depends_on: []
---

# Creative-thinking course example on the activities platform

## 1. Summary

Ship a two-unit worked example that proves the structured-activities feature can carry a
real published curriculum end to end. The source is Ke Pei-jung's 2019 NTNU master's
thesis, an eight-week junior-high guidance course ("少年《I》的奇幻旅程") that integrates
two creative-thinking techniques — de Bono's Six Thinking Hats and the Mandala
(nine-grid) method — into a self-development theme axis. Two of its eight units become
project `ActivityType`s: unit 2 (時空旅人, Mandala) and unit 4 (情緒播報台, Six Hats).

Delivering them requires closing one genuine platform gap: the only first-party
in-process validator today is `exact_match` (`backend/app/plugins/activity_validators.py:23`),
which compares one payload field to an answer key. Open-ended creative responses have no
answer key, so every submission would score invalid. This task adds a second first-party
validator, `filled_count`, which scores completeness rather than correctness — and in doing
so gives the platform its first "collect, do not judge" mode. It also registers the
platform's first production activity plugin: a 3x3 Mandala grid, which is currently the
only untested half of the plugin SDK ([R30.17], [R30.19]).

## 2. Goals and Non-goals

**Goals**

- Add a first-party `filled_count` in-process validator so an activity type can accept
  open-ended responses, scoring completeness (`sub_scores.filled`) instead of correctness.
- Make `filled_count` authorable from the existing type-authoring UI (it needs a
  `min_filled` sub-form, mirroring how `exact_match` gets `field`/`expected`).
- Register the first production activity plugin — a reusable Mandala nine-grid renderer
  bound to the key `mandala-9grid` — exercising `defineActivityPlugin` and `InProcessBridge`
  in production for the first time.
- Provide a repeatable, idempotent seeder (`python -m smap.examples creative-thinking-course`)
  that registers the two activity types into an existing project.
- Publish `docs/examples/creative-thinking-course.md`: the unit-to-type mapping, both
  payload schemas verbatim, a facilitator/participant runbook, and an unvarnished
  limitations section.

**Non-goals**

- The remaining six units of the eight-week course. Two units cover both techniques; the
  rest is data entry that adds no new platform evidence.
- Automatic scoring of the other three creativity dimensions (變通力 / 獨創力 / 精進力).
  `filled_count` addresses 流暢力 only. The rubric for the rest is an unresolved
  domain-expert deliverable (`docs/assessments/nstc-meeting-learning-activities.md:76`, C-1)
  and inventing one here would fabricate research instrumentation.
- Creating orgs, projects, chatrooms, users, or activations. The seeder registers types
  into a project that already exists and is identified by flag.
- Pre/post creativity instruments (新編創造思考測驗, 威廉斯創造性傾向量表, 自我概念量表).
  These are external paper instruments in the source study.
- Any change to activation, session, submission, or scoring semantics.
- A visual/manipulable canvas (drag, rotate, decompose). The source course is entirely
  text-based, which is precisely why it fits the platform today; the component-manipulation
  tasks flagged as the major engineering risk in
  `docs/assessments/nstc-meeting-learning-activities.md:40-42` belong to a different paper
  and stay out of scope.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How large should the example course be? | Two units: unit 2 時空旅人 (Mandala) and unit 4 情緒播報台 (Six Hats). | Covers both techniques the thesis uses, and splits the evidence: one unit exercises a custom plugin, the other proves a type works with zero frontend code. Eight units would multiply seed data without adding platform evidence. |
| Q-2 | How should open-ended responses be scored, given only `exact_match` ships? | Add a first-party `filled_count` in-process validator. | Rejected: a webhook LLM judge (needs a service that does not exist, and the four-dimension rubric is undecided — `nstc-meeting-learning-activities.md:76`); rejected: reusing `exact_match` (marks every creative answer invalid, so the example would teach the wrong thing). `filled_count` is domain-neutral, deterministic, and its count is the direct operational definition of 流暢力 (fluency). |
| Q-3 | What form should the example take? | Docs + seeder CLI + a frontend Mandala plugin. | The plugin makes the demo read like the paper worksheet rather than nine stacked text boxes, and it is the first production exercise of the plugin SDK. |
| Q-4 | Which activity-type key should the Mandala plugin bind to? | The generic key `mandala-9grid`, not the course-specific `mandala-time-traveler`. | `registry.set(plugin.manifest.key, plugin)` (`frontend/src/slices/activities/plugins/registry.ts:10`) is a single global `Map` keyed by type `key`, while `ActivityType.key` is unique only per project ([R30.02]). A generic key lets the grid serve the thesis's other three Mandala units (3, 5, 6) and any other project, and the project chooses which of its types claims the key. Accepted cost: any project naming a type `mandala-9grid` inherits this UI. |
| Q-5 | Should room AI agents see the content of student answers? | Yes for agents, no for the room echo: `expose_payload_to_agent=true`, `echo_includes_content=false` (the platform defaults, `backend/contexts/activities/domain/models.py:68-69`). | Teacher/peer/observer agents cannot give feedback or diagnose on a digest they cannot read, and that feedback loop is the platform's reason for existing. Keeping `echo_includes_content=false` stops one student's answer being pasted into the shared transcript for the whole room. See §8 — this routes minors' written responses to an LLM provider and is a consent/IRB matter for any real deployment. |
| Q-6 | What `retention_days` should the seeded types declare? | `null` (submissions follow the room's normal purge). | The example is a feasibility demonstration, not a data-collection instrument. Retention is an IRB question still open in `docs/assessments/nstc-meeting-learning-activities.md:102` (G-2); hard-coding a horizon here would read as a platform recommendation for a decision that is the researcher's. The docs explain how to set it ([R30.20]) when a real study runs. |
| Q-7 | Does this task depend on any unfinished dossier? | No — `depends_on: []`. | The only non-`implemented` dossiers are `2026-07-07-graphrag-two-axis-redesign` (approved, GraphRAG) and `2026-07-19-large-artifacts-silently-dropped` (in-progress, artifacts). Neither touches `contexts/activities`, `app/plugins/`, `slices/activities`, or `smap/`. Verified by frontmatter scan over every `docs/tasks/*/spec.md`. |

## 4. Current State

### 4.1 The four activity nouns

`backend/contexts/activities/domain/models.py:47-70` defines `ActivityType` (project-scoped
template: `key`, `name`, `payload_schema`, `validator_kind`, `validator_config`,
`retention_days`, `expose_payload_to_agent`, `echo_includes_content`, `version`).
`ActivityActivation` (`:84-92`) turns exactly one type on per room ([R30.21]);
`ActivitySession` (`:73-81`) is one subject's run with a monotonic `attempt_no`;
`ActivitySubmission` (`:103-124`) is the authoritative scored record.

### 4.2 Validators: only one, and it needs an answer key

`register_in_process_validator` (`backend/contexts/activities/application/validators/registry.py:56-65`)
holds a process-global registry; `list_registered` (`:72-75`) is what the authoring picker
reads; `get_config_validator` (`:78-80`) returns the optional per-validator config checker.
The context ships zero domain validators by design (`registry.py:4-7`), so first-party
scorers register from `backend/app/plugins/activity_validators.py`, invoked at import
(`:69`) and by the startup step at `backend/app/bootstrap/startup.py:48-56`.

The only registered validator is `exact_match` (`activity_validators.py:23`, scorer at
`:27-48`, config checker at `:51-57`, registration at `:60-66`). Its verdict is a comparison
against `validator_config["expected"]`. There is no registered validator that accepts a
response without an answer key, and `validator_kind` is mandatory with exactly three
members (`models.py:25-28`) — so today there is no supported way to configure a
collect-only activity.

`ActivityTypeService._validate_validator_config` (`backend/contexts/activities/application/type_service.py:181-210`)
gates registration: the `IN_PROCESS` branch (`:183-192`) rejects an unregistered
`validator_id` and then calls the validator's own config checker (`:190-192`) with the whole
config dict. `update()` re-runs the same checks whenever a behavioral field changes
(`:111-120`) and refuses while an activation is live ([R30.23]).

### 4.3 Payload schemas and the generic form

`validate_schema_wellformed` (`backend/contexts/activities/application/validators/schema.py:18-24`)
runs `Draft202012Validator.check_schema` — meta-schema validation only, no keyword
allowlist. `payload_errors` (`:27-30`) validates each submission against the type schema
before dispatch ([R30.04]).

On the client, `fieldsFromSchema` (`frontend/src/slices/activities/components/schemaFields.ts:47-63`)
derives fields from `properties`, `kindFor` (`:27-45`) maps them to
`string | number | boolean | enum | enum-array | json`, and anything unrepresentable
degrades to a labelled JSON textarea rather than being dropped ([R30.18]). `labelFor`
(`:23-25`) uses the schema's `title`, falling back to the property name — so student-facing
prompts live in the schema itself, not in i18n bundles. `assemblePayload` (`:96-147`) and
`validatePayload` (`:191-211`) are pure and reusable outside `SchemaForm.vue`.

The guided authoring builder is restricted to flat scalars — `SCHEMA_FIELD_TYPES` is
`['string','number','integer','boolean']` (`frontend/src/slices/activities/types/schemas.ts:24`)
and `SchemaBuilder.vue:66-77` emits `{type:'object', properties, required?}`. Both course
units are flat all-string schemas, so both round-trip through the builder without needing
the raw-JSON escape hatch.

### 4.4 Authoring surface

`POST /api/projects/{project_id}/activity-types` (`backend/app/api/v1/activities.py:296-303`,
owner-gated at `:304`), `PATCH` (`:355-363`), `DELETE` (`:385-392`), `GET` list (`:408-413`,
which strips `validator_config` for non-owners per [R30.25]). `GET /api/activity-validators`
(`:449-452`) returns `ActivityValidatorOut` — `{id, title}` only (`:120-124`), backed by the
static `ActivitiesFacade.list_validators()` (`backend/contexts/activities/interfaces/facade.py:133-136`).

Because that endpoint carries no per-validator config schema, the authoring form hardcodes
each validator's sub-form. `frontend/src/slices/activities/types/schemas.ts:20` pins
`EXACT_MATCH_VALIDATOR_ID`; the `superRefine` branch at `:98-117` requires
`exact_match_field`/`exact_match_expected`; `assembleValidatorConfig` (`:122-145`) folds
them into `validator_config`, and `:18-19` states in a comment that any other validator id
folds to just `{validator_id}` — correct only while `exact_match` is the sole validator.
Adding a second validator therefore requires a frontend change by construction.

The management view is `frontend/src/slices/activities/views/ActivityTypesView.vue`, routed
at `/projects/:projectId/activity-types` (`frontend/src/slices/activities/routes.ts:8-13`).

### 4.5 Plugin SDK — fully built, zero production users

`ActivityPlugin` (`frontend/src/slices/activities/sdk/types.ts:58-64`) requires a `manifest`
(`key`, `version`, `title` — all required, `:50-54`) and a
`render(container: HTMLElement, ctx): void | ActivityTeardown` (`:63`); `schema` is an
optional client-side override (`:60-61`). `ActivityRenderCtx` (`:43-48`) has exactly four
members: `schema`, `session`, `emit`, `t`. `defineActivityPlugin` is an identity helper that
validates nothing at runtime (`sdk/defineActivityPlugin.ts:5-6`).

`InProcessBridge.mount` (`sdk/bridge.ts:38-50`) builds that ctx, delegates `emit` straight to
the host's submit (`:44`), and normalizes a non-function `render` return into a no-op
teardown (`:48`). `IframeBridge` is a deferred stub that throws (`:58-63`) — the [R30.19]
sandbox is future work.

`ActivityHost.vue:44` looks the plugin up by `props.activityType.key`; `:45-47` resolves
`plugin.schema ?? activityType.payload_schema`; the template branches mutually exclusively
between a plugin container (`:77-81`) and `SchemaForm` (`:83-88`). Mount happens in
`onMounted` only (`:54-66`), teardown in `onBeforeUnmount` (`:68-71`) — there is no watcher
that re-mounts if `activityType` changes while the host stays mounted. `ctx.t` is wired at
`:63` as a thin wrapper over the app's vue-i18n `t`, so plugin keys resolve against the
global merged bundle.

`registerActivityPlugin` (`plugins/registry.ts:9-11`) writes into a module-level `Map`
(`:7`) keyed on `manifest.key`, overwriting silently with no duplicate guard.
`plugins/index.ts:1-4` designates itself the registration entry point but `:6` is currently a
bare re-export with no side-effect import. Repo-wide there is **no** production
`registerActivityPlugin(...)` call site — every activity type today falls to `SchemaForm`.

`composables/useActivityHost.ts:43-69` owns submit: it posts through `submitActivity`,
upserts the Pinia store, and on failure sets `errorMessage` from `ApiError.message` or the
key `activities.host.submitFailed`, then **rethrows** (`:62-66`) so a plugin's
`await ctx.emit()` observes the rejection. There is no error callback on `ctx`.

### 4.6 i18n wiring

Activity components call `useI18n()` from vue-i18n directly (e.g. `ActivityHost.vue:3,28`).
Locale bundles live at `frontend/src/slices/activities/locales/{en,zh-TW}.json` under a single
`activities` namespace, registered by `installActivitiesSlice()`
(`frontend/src/slices/activities/index.ts:46-51`) via `registerLocaleLoaders`, invoked once at
boot from `frontend/src/app/main.ts:43`. `shared/i18n/index.ts:42-56` merges bundles lazily
per locale.

### 4.7 CLI conventions

`smap/` holds three Typer packages — `bootstrap/`, `maintenance/`, `rotation/`. Each is
`__init__.py` + `__main__.py` + one module per subcommand. `app = typer.Typer(help=..., no_args_is_help=True)`
(`backend/smap/maintenance/__main__.py:12-15`); a single-command app **must** declare an empty
`@app.callback()` (`:18-27`) or Typer collapses it and `python -m smap.X <cmd>` breaks — pinned
by `backend/tests/unit/test_smap_cli_contract.py:88-102`. Commands are thin `@app.command("kebab-name")`
wrappers delegating to a sibling module's `run(...)`, logging a plain dataclass report via
loguru (`maintenance/__main__.py:52-59`) and raising `typer.Exit(code=1)` on failure (`:45-51`).
Async implementations wrap `asyncio.run(...)` and take their session from
`get_sessionmaker()` (`backend/smap/rotation/rotate_transit.py:209-211`;
`backend/smap/maintenance/reconcile_attachment_sizes.py:70,79`).

The "import `shared_kernel.infra.*` and `app.config.settings` only, never `contexts.*`" rule
in `backend/smap/bootstrap/__init__.py` is bootstrap-specific: `maintenance` already imports a
context facade (`reconcile_attachment_sizes.py:37`). `smap*` is a packaged module
(`backend/pyproject.toml:87`) and is not governed by an importlinter contract (`:442`).
No compose service runs a `smap` CLI; invocation is always ad-hoc
(`Makefile:82,84,86`; `.github/workflows/ci.yml:139`).

### 4.8 Nothing seeds activity types

`register_activity_type` exists only as a route function name (`app/api/v1/activities.py:297`).
`app/bootstrap/seed.py` has zero activity references; migrations `0049_activities.py`,
`0050_activity_activations.py`, `0065_activity_agent_visibility.py` create tables only. Test
"fixtures" are duplicated module-level `_make_type` factories per file (e.g.
`backend/tests/unit/test_activities_services.py:51-66`), not pytest fixtures.

## 5. Design

### Options considered

**Scoring open responses**

**Option A — `filled_count` first-party in-process validator.** Deterministic, no network,
no new service. Counts non-empty payload fields; `is_valid = filled >= min_filled`;
`sub_scores.filled` is the fluency count. `min_filled: 0` yields a never-invalid collect-only
mode. Domain-neutral, so it does not violate the platform's "ships no domain rule" stance
([R30.14] in spirit).

**Option B — webhook LLM judge.** Closest to the thesis's four-dimension assessment, but
requires a scoring service that does not exist, egress-proxy configuration ([R30.07]), and a
rubric that domain experts have not yet produced. Non-deterministic output also undermines the
Cohen's-Kappa reproducibility concern already logged at
`docs/assessments/nstc-meeting-learning-activities.md:52`.

**Option C — reuse `exact_match`.** Zero backend work, but marks every creative answer
invalid. The example would then demonstrate the feature being misused.

**Mandala plugin rendering**

**Option D — Vue island via `createApp`.** `render()` receives a raw `HTMLElement`, so the
plugin mounts its own Vue app into it and returns `() => app.unmount()` as the teardown.
Keeps the grid a normal SFC with Tailwind, testable with the existing harness. Cost: a
`createApp` root inherits none of the host app's provides — no vue-i18n, no Pinia, no router.

**Option E — imperative DOM.** `document.createElement` inside `render`. No framework
context question at all, but hand-rolled state management and no access to project styling
idioms.

### Decision

**Option A** for scoring. `filled_count` is the smallest change that makes open-ended
activities correct rather than merely possible, and it is generic enough to belong in the
platform rather than in a course: "did the participant fill in enough of the form" is not a
pedagogy rule. It deliberately scores completeness only — it makes no claim about answer
quality, which is what keeps it honest about the undecided rubric.

Config is `{validator_id: "filled_count", min_filled: <int >= 0>}`. `min_filled: 0` is
explicitly legal and is the collect-only mode. A field counts as filled when its value is not
`None` and, for strings, contains non-whitespace; lists and dicts count when non-empty;
numbers and booleans always count. That last clause is a real caveat: `assemblePayload`
always emits a boolean (`schemaFields.ts:105-107`), so an untouched checkbox counts as
filled. `filled_count` is therefore documented as intended for text-response schemas. Both
seeded types are all-string, so it does not bite here. A `fields` allowlist that would fix it
in general is deliberately **not** added, because `GET /api/activity-validators` carries no
config schema and the authoring form would have no way to set it — shipping an
unreachable knob is worse than documenting the limit (FU-1).

`sub_scores` returns `{"filled": n}` and nothing else. `min_filled` is **not** echoed into
`sub_scores`: `sub_scores` reaches participants on `ActivitySubmissionOut`, while
`validator_config` is owner-confidential ([R30.25]). The threshold is not an answer key, but
copying config into a participant-visible field is the exact habit that leaks one later.

**Option D** for the plugin. The island mounts a `MandalaGrid.vue` SFC and returns
`() => app.unmount()`. Because the island has no i18n injection, `ctx.t` is passed in as a
prop — which is precisely why the SDK surfaces `t` at all (`sdk/types.ts:43-48`). For the same
reason the island uses **plain elements plus Tailwind classes, not `@shared/ui` components**:
a shared component that internally calls `useI18n()` would throw inside a context-free root.
This constraint is recorded here because it is invisible at the call site and will otherwise
be rediscovered by whoever adds the second plugin.

Field placement is derived, not hardcoded, so the plugin serves any nine-grid type: reuse
`fieldsFromSchema` to read the schema; if there are exactly 9 fields, the one named `center`
(or the first field, when no such property exists) takes the middle cell and the rest fill the
ring in declaration order; for any other field count the plugin renders the same inputs in a
single column. Submission reuses `assemblePayload` and `validatePayload` from
`components/schemaFields.ts` rather than reimplementing payload assembly.

**Seeder shape.** A new `backend/smap/examples/` Typer package following the
maintenance/rotation pattern — a local report dataclass and loguru logging, not
`bootstrap/_common.py`'s `BootstrapReport` (that module is bootstrap-private by its
underscore, and maintenance/rotation already establish the plain-dataclass precedent). It
imports `ActivitiesFacade`, which is permitted outside `bootstrap/`. It is idempotent: types
already present by `key` are reported `already-present` and skipped, so a re-run is safe.

**Cell labels.** The eight ring cells are deliberately left generic (`格 1` … `格 8`) rather
than given themes. The thesis's Mandala figures (放射型曼陀羅, 圖 2-1-1) are free-association
layouts; pre-labelling the cells would constrain exactly the divergent thinking the unit is
measuring.

## 6. Detailed Changes

**Backend**

- `backend/app/plugins/activity_validators.py` — add `FILLED_COUNT_ID = "filled_count"`,
  `filled_count_scorer(payload, activity_type, *, db)`, `validate_filled_count_config(config)`
  (rejects a `min_filled` that is missing, non-integer, boolean, or negative), and register it
  inside `register_first_party_validators()` beside `exact_match` (`:60-66`) with
  `title="Filled count"`. Error class on failure: `too_few_filled`. Extend `__all__`.
- No context change. `contexts/activities` stays domain-free; registration remains outside it
  ([R30.05]).
- No migration. No new table, column, or enum value.
- New CLI package `backend/smap/examples/`:
  - `__init__.py` — docstring stating scope.
  - `__main__.py` — `typer.Typer(help=..., no_args_is_help=True)`, a no-op `@app.callback()`
    (mandatory for a single-command app), `@app.command("creative-thinking-course")` taking
    `--project-id` and `--owner-user-id`, delegating to `_creative_thinking_course.run(...)`,
    logging the report, `typer.Exit(code=1)` on failure, `if __name__ == "__main__": app()`.
  - `creative_thinking_course.py` — the two type definitions as module constants, plus
    `run(project_id, owner_user_id) -> SeedReport` wrapping `asyncio.run`, using
    `get_sessionmaker()`, calling `ActivitiesFacade(db).list_types()` to skip existing keys and
    `register_type(...)` for the rest, then `await db.commit()` (the facade never commits —
    `type_service.py:1-6`).

**API contract** — unchanged. No new or modified endpoint, request model, or response model.
`gen:api` rerun required: **no**. `GET /api/activity-validators` gains a second row at runtime
purely because the registry has a second entry.

**Frontend**

- `frontend/src/slices/activities/types/schemas.ts` — add
  `FILLED_COUNT_VALIDATOR_ID = 'filled_count'`; add a `filled_count_min` form field (default
  `0`; must **not** reuse the `emptyToNull` preprocess at `:48-49`, which maps `0` to `null`
  and would destroy the legal collect-only value); add a `superRefine` branch requiring a
  non-negative integer; add a branch to `assembleValidatorConfig` emitting
  `{validator_id, min_filled}`.
- `frontend/src/slices/activities/components/ActivityTypeForm.vue` — conditional `min_filled`
  input shown when the selected in-process validator is `filled_count`, mirroring the existing
  `exact_match` sub-form.
- New `frontend/src/slices/activities/plugins/mandala9grid/MandalaGrid.vue` — the 3x3 grid,
  props `schema`, `t`, `submit`; textareas for each cell; submit button; per-field error
  display; disabled state while submitting.
- New `frontend/src/slices/activities/plugins/mandala9grid/index.ts` —
  `defineActivityPlugin({ manifest: { key: 'mandala-9grid', version: '1.0.0', title: 'Mandala 9-grid' }, render })`
  where `render` does `createApp(MandalaGrid, props).mount(container)` and returns
  `() => app.unmount()`.
- `frontend/src/slices/activities/plugins/index.ts` — add the side-effect import plus the
  `registerActivityPlugin(...)` call, making this the first production registration
  (the file already documents itself as that entry point at `:1-4`).
- `frontend/src/slices/activities/locales/en.json` and `zh-TW.json` — new keys under
  `activities`: `mandala.center`, `mandala.cell`, `mandala.submit`, `mandala.fieldRequired`,
  and `typeForm.filledCount.minFilled` / `typeForm.filledCount.minFilledHelp`.

**Docs**

- New `docs/examples/creative-thinking-course.md` (English prose per the docs contract;
  Chinese only inside the literal course data, which is student-facing project content, not
  platform UI). Contents: bibliographic attribution to the source thesis — cited
  bibliographically, **not** by repo path, because `_projects_documents/` is gitignored
  (`.gitignore:16`) and would be a dangling reference; the unit-to-type mapping table; both
  payload schemas verbatim; the seeder invocation; a facilitator/participant runbook; and the
  limitations section.

**Deploy/config** — none. No env var, Vault path, or compose change.

### 6.1 Seeded type definitions

Unit 2 — 時空旅人 (Mandala). `key: mandala-9grid` (Q-4), `name: 單元二 時空旅人`,
`validator_kind: in_process`, `validator_config: {validator_id: "filled_count", min_filled: 4}`,
`retention_days: null`, `expose_payload_to_agent: true`, `echo_includes_content: false`.

```json
{
  "type": "object",
  "properties": {
    "center": { "type": "string", "title": "中心主題：30 歲的我", "description": "用一句話寫下你想像中 30 歲的自己。" },
    "cell_1": { "type": "string", "title": "格 1" },
    "cell_2": { "type": "string", "title": "格 2" },
    "cell_3": { "type": "string", "title": "格 3" },
    "cell_4": { "type": "string", "title": "格 4" },
    "cell_5": { "type": "string", "title": "格 5" },
    "cell_6": { "type": "string", "title": "格 6" },
    "cell_7": { "type": "string", "title": "格 7" },
    "cell_8": { "type": "string", "title": "格 8" }
  },
  "required": ["center"]
}
```

`min_filled: 4` means the centre plus at least three free associations.

Unit 4 — 情緒播報台 (Six Thinking Hats). `key: six-hats-emotion-desk`,
`name: 單元四 情緒播報台`, `validator_config: {validator_id: "filled_count", min_filled: 3}`,
same visibility and retention settings. No plugin — this type proves an activity ships with
zero frontend code ([R30.18]).

```json
{
  "type": "object",
  "properties": {
    "event": { "type": "string", "title": "困擾我的事件", "description": "最近或曾經讓自己困擾的一件事。" },
    "hat_white": { "type": "string", "title": "白帽：事實", "description": "只寫客觀發生了什麼，不加評價。" },
    "hat_red": { "type": "string", "title": "紅帽：感受", "description": "當下的情緒，不需要說明理由。" },
    "hat_yellow": { "type": "string", "title": "黃帽：好處", "description": "這件事有沒有任何好的一面？" },
    "hat_black": { "type": "string", "title": "黑帽：風險", "description": "可能的壞處或風險是什麼？" },
    "hat_blue": { "type": "string", "title": "藍帽：總結", "description": "整理以上，你現在的想法是什麼？" }
  },
  "required": ["event"]
}
```

Property order drives render order (`schemaFields.ts:50`). The white→red→yellow→black→blue
sequence is de Bono's standard review order; the thesis lists the five hats without fixing a
sequence (表 3-5-2, 單元四), so this is an adaptation and the docs say so.

## 7. NFR Checklist

- [x] **i18n** — every new plugin and form string goes through `$t()`/`ctx.t` with keys added
  to both `en.json` and `zh-TW.json`. Student-facing prompts are schema `title`/`description`
  values, which are project data rendered verbatim by `labelFor` (`schemaFields.ts:23-25`) and
  correctly are not i18n keys. Gate #12 applies to the plugin's template.
- [x] **Audit log** — `register_type` already emits `activity_type.created`
  (`type_service.py:64-75`). The seeder passes the operator-supplied `--owner-user-id` as
  `actor_user_id` and `actor_ip=None`, so every seeded row is attributable.
- [x] **Tenant isolation** — no new endpoint, so no new AuthZ surface. The seeder scopes every
  write to the `--project-id` it is given and reads existing keys through
  `list_types(project_id)`. See §8 on the CLI's deliberate AuthZ bypass.
- [x] **Error handling UX** — the plugin renders per-field required errors from
  `validatePayload` and surfaces submit failure via the host's existing `errorMessage` path
  (`useActivityHost.ts:62-66`), which rethrows so `ctx.emit` rejects. Submitting state disables
  the button. Seeder failures log and exit 1.
- [x] **Performance** — two rows, one-off. `filled_count` is O(number of payload fields) with
  no I/O and ignores the `db` session it is handed. The plugin adds one `createApp` root per
  mounted activity; bundle gate #9 (initial ≤ 250 KB gzip) is checked by
  `pnpm run check:bundle-size` — the plugin is inside the lazily-loaded activities slice, not
  the initial chunk.

## 8. Security Considerations

- **Participant-visible score leakage.** `sub_scores` is returned to participants on
  `ActivitySubmissionOut` (`app/api/v1/activities.py:167-178`) while `validator_config` is
  owner-confidential ([R30.25]). `filled_count` therefore returns `{"filled": n}` only and
  never copies `min_filled` (or any other config value) into `sub_scores`.
- **Scoring authority.** `filled_count` runs server-side inside the request transaction
  ([R30.03]); the client cannot supply or influence `is_valid`, `sub_scores`, or `attempt_no`.
  The plugin sends a raw payload through `ctx.emit` and has no score channel by construction
  (`sdk/types.ts:1-6`).
- **Untrusted-plugin isolation is not in play.** This plugin is first-party, bundled, and runs
  in-process. `IframeBridge` remains a throwing stub (`sdk/bridge.ts:58-63`); [R30.19]'s sandbox
  stays future work. The plugin must not be presented as evidence that untrusted plugins are
  safe to load.
- **Global plugin key namespace.** `registry.ts:9-11` keys on `manifest.key` with no duplicate
  guard and silent overwrite, while `ActivityType.key` is only per-project unique. Any project
  naming a type `mandala-9grid` gets this renderer. That is intended (Q-4) but it means a
  plugin can change how another tenant's activity renders. It cannot change what is stored or
  how it is scored — both are server-side — so the blast radius is presentation only. Recorded
  so it is a known property rather than a surprise.
- **CLI bypasses API authorization by design.** The seeder calls `ActivitiesFacade.register_type`
  directly, skipping the route's `assert_project_owner` gate (`app/api/v1/activities.py:304`).
  This matches `smap/bootstrap/create_admin.py`, which likewise trusts its operator; anyone who
  can run `python -m smap.examples` already has DB credentials. `--owner-user-id` is used for
  the audit trail, not as an authorization check, and the docs must say so plainly rather than
  implying the flag authorizes anything.
- **Student responses reach an LLM provider.** With `expose_payload_to_agent=true` (Q-5),
  submitted text enters the agent context block ([R30.15]) and is sent to whichever provider the
  project's key targets. The source study's participants are 13-year-olds and unit 4 collects
  negative-affect narratives. The docs must state this explicitly alongside the informed-consent
  and IRB items already open at `docs/assessments/nstc-meeting-learning-activities.md:99-103`,
  and note that `expose_payload_to_agent=false` is the switch if a study needs answers kept out
  of agent prompts.
- No auth, provider-key, WebSocket, or file-upload surface is otherwise touched. All submission
  input continues through `payload_errors` schema validation before persistence ([R30.04]).

## 9. Quality Notes

**Existing debt in touched files** (do not imitate, do not silently fix)

- `frontend/src/slices/activities/types/schemas.ts:18-19` states in a comment that a
  non-`exact_match` validator folds to `{validator_id}` alone, "correct until a second
  validator ships". This task ships that second validator, so the comment must be updated, not
  left to rot.
- The per-validator sub-form is hardcoded client-side because `GET /api/activity-validators`
  returns `{id, title}` only (`app/api/v1/activities.py:120-124`). Every future validator will
  need a frontend edit. Do not fix this here — FU-2.
- `registerActivityPlugin` overwrites silently with no duplicate detection
  (`plugins/registry.ts:9-11`). Not fixed here — FU-3.
- `ActivityHost.vue:54-66` mounts a plugin in `onMounted` with no watcher for a later
  `activityType` change. Do not add one opportunistically; verify the real behavior first (§10).
- Activity-type test factories are duplicated as module-level `_make_type` functions across at
  least five test files (`test_activities_services.py:51-66`, `test_activity_type_edit.py:41-56`,
  `test_activities_authz.py:26-41`, `test_activities_activation_projection.py:29-44`,
  `test_activities_validation_worker.py:61`). Follow the local convention; do not start a
  conftest refactor inside this task.

**Patterns to follow**

- Validator: mirror `exact_match` exactly — module-level id constant, a scorer with the
  `(payload, activity_type, *, db)` signature, a separate `validate_*_config` raising
  `ValidatorConfigInvalid`, registration inside `register_first_party_validators()`, explicit
  `__all__` (`backend/app/plugins/activity_validators.py:23-77`).
- CLI: `backend/smap/maintenance/__main__.py` is the closest exemplar (Typer app, no-op
  callback, thin command wrapper, dataclass report, loguru, `typer.Exit(code=1)`);
  `backend/smap/rotation/rotate_transit.py:209-211` for the `run()` → `asyncio.run` shape.
- Plugin: the ctx contract in `sdk/bridge.ts:38-50` and the assertions in
  `__tests__/sdk.test.ts:13-22,25-31,47`.
- SoC: `contexts/activities` must gain no domain knowledge; the validator stays in
  `app/plugins/` ([R30.05], `validators/registry.py:4-7`). The plugin lives inside the
  activities slice and imports only from that slice and `@shared` (boundaries gate #1).

**Reuse inventory** (use these, do not reimplement)

- `fieldsFromSchema`, `initialValues`, `assemblePayload`, `validatePayload` —
  `frontend/src/slices/activities/components/schemaFields.ts:47,65,96,191`. The Mandala plugin
  must use all four; they are pure and already unit-tested.
- `defineActivityPlugin` (`sdk/defineActivityPlugin.ts:5`) and the `ActivityPlugin` /
  `ActivityRenderCtx` / `JSONSchema` types (`sdk/types.ts:14-64`).
- `registerActivityPlugin` (`plugins/registry.ts:9`); `clearActivityPlugins` (`:18`) for test
  teardown, as `__tests__/ActivityHost.test.ts:52-55` does.
- `ValidationResult` (`backend/contexts/activities/domain/models.py:127-134`) and
  `ValidatorConfigInvalid` (`backend/contexts/activities/domain/errors.py`).
- `ActivitiesFacade.register_type` / `.list_types` (`interfaces/facade.py:70-86`, `:138-139`)
  for the seeder — never the repository or service directly.
- `get_sessionmaker` (`shared_kernel/db/session.py`) for the CLI session.
- `renderView` from `frontend/tests/utils` and the `vi.hoisted` mock pattern in
  `__tests__/ActivityHost.test.ts:13-14` for component tests.

## 10. Risks and Rollback

- **No migration, so rollback is a revert.** Seeded rows are ordinary project data; removing
  them is a soft-delete through the existing owner-only `DELETE` route
  (`app/api/v1/activities.py:385-392`), which also ends any live activation ([R30.23]).
- **`createApp` island has no app context.** If the grid ever imports a `@shared/ui` component
  that calls `useI18n()`/`useRouter()`/a Pinia store, it throws only at runtime inside the
  plugin. Mitigation: plain elements plus Tailwind only (§5), and a component test that mounts
  the plugin through `InProcessBridge` rather than only unit-testing the SFC in isolation.
- **Plugin remount on activity switch is unverified.** `ActivityHost.vue:54-66` has no watcher.
  If the host stays mounted across an end/start cycle, a stale plugin could persist. Verify
  during build by switching between the two seeded types in a live room (`/run` or
  `frontend:verify`); if it reproduces, record it as a follow-up rather than widening this
  task's scope.
- **Bundle budget.** The plugin adds an SFC plus a `createApp` root to the activities slice.
  Gate #9 is enforced by `pnpm run check:bundle-size`; the slice is lazily loaded, so the
  initial-chunk budget should be unaffected. Confirm rather than assume.
- **Seeder run against the wrong project.** `--project-id` is unvalidated beyond the facade's
  own behavior. Idempotent skip-by-key limits the damage to two extra soft-deletable types.
- **Course-fidelity risk.** The eight ring-cell labels and the hat ordering are adaptations,
  not verbatim thesis content. If the collaborating educator wants exact worksheet wording,
  it is a data edit in one file — no code change.

## 11. Acceptance Criteria

- [ ] AC-1: `filled_count` is registered at startup and appears in
  `GET /api/activity-validators` alongside `exact_match`, with a display title.
- [ ] AC-2: `filled_count_scorer` returns `is_valid=true` with `sub_scores == {"filled": n}`
  when the count of filled fields is `>= min_filled`, and `is_valid=false` with
  `error_class == "too_few_filled"` otherwise. Whitespace-only strings do not count as filled;
  `None` does not count.
- [ ] AC-3: `sub_scores` contains no key other than `filled` — in particular it never carries
  `min_filled` or any other `validator_config` value ([R30.25]).
- [ ] AC-4: `min_filled: 0` is accepted at registration and makes every schema-valid submission
  `is_valid=true` (collect-only mode).
- [ ] AC-5: Registering a type with `validator_id: "filled_count"` and a missing, negative,
  boolean, or non-integer `min_filled` is rejected with `ValidatorConfigInvalid` at
  registration **and** at edit ([R30.02], [R30.23]).
- [ ] AC-6: The activity-type authoring form offers `filled_count` in the in-process validator
  picker, shows a `min_filled` input when it is selected, and submits
  `validator_config == {validator_id: "filled_count", min_filled: <n>}`.
- [ ] AC-7: A type whose `key` is `mandala-9grid` renders through the Mandala plugin, not
  `SchemaForm`; a type with any other key still renders through `SchemaForm`.
- [ ] AC-8: The Mandala plugin places the `center` property in the middle cell and the other
  eight in the ring in declaration order; with a field count other than 9 it renders a single
  column instead of a broken grid.
- [ ] AC-9: Submitting from the plugin calls `ctx.emit` with the assembled payload and no score
  field; a rejected submit surfaces the host error without unmounting the grid.
- [ ] AC-10: `python -m smap.examples creative-thinking-course --project-id X --owner-user-id Y`
  registers both types, and a second run reports both as already-present, registers nothing, and
  exits 0.
- [ ] AC-11: Both seeded types are registered with `expose_payload_to_agent=true`,
  `echo_includes_content=false`, `retention_days=null`, and pass
  `validate_schema_wellformed` (Q-5, Q-6).
- [ ] AC-12: `docs/examples/creative-thinking-course.md` exists and states: the source thesis
  attribution, both schemas, the runbook, the one-active-activation-per-room constraint
  ([R30.21]), that `filled_count` covers fluency only, and that `expose_payload_to_agent=true`
  routes student text to an LLM provider.
- [ ] AC-13: All existing gates stay green — `pytest -q`, `ruff check . && ruff format --check .`,
  `mypy .`, `pnpm test`, `pnpm lint`, `pnpm run typecheck`, `pnpm build`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | unit | `backend/tests/unit/test_activities_services.py` — extend the startup-registration class at `:321-334`; and `test_activity_type_edit.py:329-355` (`TestValidatorListRoute`) for the listing endpoint. |
| AC-2, AC-3, AC-4 | unit | New `TestFilledCountScorer` in `test_activities_services.py`, following the `_score` helper + `teardown_method(clear_registry)` shape of the `exact_match` class at `:265-318`. |
| AC-5 | unit | New cases in `TestTypeServiceValidatorConfig` (`test_activities_services.py:125-263`), using the function-local `register_first_party_validators()` import already used at `:221,:244`; edit-path case exercising `update()` (`type_service.py:111-120`). |
| AC-6 | component | `frontend/src/slices/activities/__tests__/ActivityTypeForm.test.ts` plus a pure test of `assembleValidatorConfig`/`superRefine` beside the existing schema tests. |
| AC-7 | component | `frontend/src/slices/activities/__tests__/ActivityHost.test.ts` — register the plugin, assert the plugin container branch (`ActivityHost.vue:77-81`) wins; assert `SchemaForm` for another key. `clearActivityPlugins()` in `afterEach` as at `:52-55`. |
| AC-8, AC-9 | component | New `__tests__/MandalaGrid.test.ts` — mount through `InProcessBridge` with a stub `submit` (the `mountOptions` helper shape at `sdk.test.ts:13-22`), assert cell placement, the non-9-field fallback, and that a rejected submit leaves the grid mounted. Assert raw i18n keys, since locale bundles are not merged in unit tests. |
| AC-10 | unit | New `backend/tests/unit/test_smap_examples_cli.py` with a mocked sessionmaker/facade: first run registers two, second run skips two. Add a `--help` case to `test_smap_cli_contract.py:74-102` to pin the Typer callback. |
| AC-11 | unit | Assert the two constant definitions directly (visibility flags, `retention_days`, and `validate_schema_wellformed` over both schemas) in the CLI test. |
| AC-12 | manual | Review the written doc against the listed items. |
| AC-13 | CI | The commands in the root `CLAUDE.md` command table; full suites on remote CI. |

Manual end-to-end (not a gate, but the point of the task): seed both types into a dev
project, activate unit 2 in a room, submit from the grid as a participant, confirm the SYSTEM
echo carries no answer content, then end it and activate unit 4 to confirm the `SchemaForm`
path — using the `run` or `frontend:verify` skill.

## 13. SRS Delta

Amend **[R30.24]** — replace the parenthetical validator example so the second first-party
validator is documented (change shown in full; the rest of the entry is unchanged):

> - **[R30.24]** The authoring surface supports the `webhook`, `mcp`, and `in_process` validator kinds. The platform registers first-party in-process validators at startup from a code registration site outside the activities context; the surface offers `in_process` only while at least one such validator is registered, and an authenticated read endpoint (`GET /api/activity-validators`) lists the registered validator ids and their display titles as the single source the picker draws from. A `webhook` validator's URL is stored for proxy-only egress ([R30.07]); an `mcp` validator's `agent_id`/`binding_id` must reference agents/bindings within the same project; an `in_process` validator's config must name a registered `validator_id` plus that validator's required parameters (e.g. `exact_match` requires the payload `field` to compare and the `expected` value; `filled_count` requires a non-negative `min_filled` threshold), validated at registration and edit time ([R30.02], [R30.23]).

Add **[R30.27]**:

> - **[R30.27]** The platform ships a first-party `filled_count` in-process validator that scores completeness rather than correctness: it counts the non-empty fields of a submission payload and reports `is_valid` against a configured `min_filled` threshold, exposing the count as `sub_scores.filled`. A threshold of `0` is legal and yields a collect-only activity whose submissions are always valid — the supported way to run an open-ended activity that has no answer key. The validator returns no configuration value in `sub_scores`, since `sub_scores` is participant-visible while `validator_config` is owner-confidential ([R30.25]).

Add **[R30.28]**:

> - **[R30.28]** The repository ships at least one worked activity example — activity-type definitions plus an idempotent operator seeder (`python -m smap.examples`) and accompanying documentation — demonstrating both the custom-plugin and generic-form rendering paths ([R30.17], [R30.18]). Example content is documentation and operator tooling, not platform behavior: it is never registered automatically at startup, and no runtime code path depends on it existing.

## 14. Open Questions

- Whether the collaborating educator wants the eight Mandala ring cells left unlabelled (the
  choice made here, §5) or themed. Data-only change; does not block approval.
- Whether the remaining six units are eventually wanted in-repo or belong in the researcher's
  own project. Non-goal for this task either way.
- Whether the plugin remount behavior in `ActivityHost.vue:54-66` is an actual defect. To be
  settled by observation during build (§10), not by speculation now.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — `filled_count` has no `fields` allowlist, so on a schema containing booleans an
  untouched checkbox counts as filled (`schemaFields.ts:105-107` always emits one). Adding the
  knob requires the authoring form to be able to set it, which needs FU-2 first.
- **FU-2** — `GET /api/activity-validators` returns `{id, title}` only
  (`app/api/v1/activities.py:120-124`), so every first-party validator needs a hand-written
  frontend sub-form in `types/schemas.ts`. A per-validator config JSON Schema on that endpoint
  would make the picker self-describing and remove the coupling.
- **FU-3** — `registerActivityPlugin` overwrites an existing key silently
  (`plugins/registry.ts:9-11`). With more than one bundled plugin, a duplicate-key warning or
  hard failure would be worth having.
- **FU-4** — One plugin per type `key` means the thesis's four Mandala units (2, 3, 5, 6)
  cannot all use the grid renderer within one project. Serving several types from one plugin
  needs either multi-key registration or a `key` pattern match in `ActivityHost.vue:44`.
- **FU-5** — The remaining six course units are not seeded (Non-goal).
- **FU-6** — The 變通 / 獨創 / 精進 dimensions have no automated scoring. Blocked on the
  domain-expert rubric (`docs/assessments/nstc-meeting-learning-activities.md:76`).
