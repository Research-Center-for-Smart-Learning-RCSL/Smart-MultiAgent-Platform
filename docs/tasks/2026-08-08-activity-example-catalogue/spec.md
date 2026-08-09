---
type: refactor
status: implemented
created: 2026-08-08
requirements: [R30.02, R30.28]
depends_on: []
---

# Separate the example-course catalogue from the seeding engine

## 1. Summary

`backend/smap/examples/creative_thinking_course.py` currently holds three unrelated things
in one 170-line module: the course content (JSON Schemas, Chinese prompt text, unit names),
the seeding mechanics (idempotency, session, transaction), and the report dataclass. Adding
a second example course means copying the seeding loop, and changing a single prompt string
means a domain expert has to edit Python, pass lint, and pass tests.

This splits the module into a reusable seeding engine plus a data-only course catalogue
stored as validated JSON, so adding a course is dropping in a file and editing course text
needs no code change. Behavior for the two existing units is unchanged: the same two
activity types, the same keys, the same schemas, the same idempotency.

## 2. Motivation

Named by `check-quality` dimension, with evidence:

- **Dimension 5, Single Responsibility.** `creative_thinking_course.py` mixes content,
  persistence orchestration, and reporting. `_mandala_schema()` `:47-64` and
  `_six_hats_schema()` `:67-105` are pure content; `_seed()` `:135-166` is pure mechanism;
  `SeedReport` `:127-131` is neither. The module has three reasons to change — a course
  edit, a seeding-semantics change, and a reporting change.
- **Dimension 12, DRY (anticipatory).** The duplication has not happened yet because there
  is exactly one course. `_seed()`'s skip-by-key, register, and commit loop `:141-164` is
  the part a second course would copy. This refactor pays the debt before it is incurred
  rather than after — which is the cheap moment.
- **Accessibility of content, not a formal dimension but the operative reason.** The
  platform's purpose is that domain experts author learning activities. The first shipped
  example puts their content behind a Python edit + `ruff` + `mypy` + `pytest` + commit.
  The seeded prompts are Chinese instructional text (`:50-52`, `:74-104`) authored by an
  education researcher, not by an engineer.

The trigger is FU-5 of `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md`:
six of the source course's eight units are unseeded. Doing that work against the current
shape would multiply the debt.

## 3. Non-goals

- **No externally observable behavior change.** The same two activity types register with
  the same keys, names, schemas, validator configs, and visibility flags; the CLI keeps the
  same command name and flags; idempotency is unchanged; the audit actor is still
  `--owner-user-id`.
- **Not seeding the remaining six units** (Q-2). This refactor proves adding a third course
  is cheap; it does not add content. The other six units' worksheet details are not in hand
  and their answer-field design would need the collaborating educator's confirmation.
- **No change to `filled_count`, the mandala plugin, or anything under `contexts/activities`.**
- **No new CLI capability** — no dry-run, no unseed, no update-in-place. Idempotent
  skip-by-key stays exactly as it is.
- Not a general-purpose course authoring API. This is operator tooling ([R30.28]).

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What format should course data use? | JSON files under `smap/examples/courses/`, validated at load. | A domain expert can edit prompt text without touching Python or the toolchain, which is the operative reason for the refactor. Cost accepted: correctness moves from static types to runtime validation — mitigated because the validator largely exists already (`validate_schema_wellformed`, `backend/contexts/activities/application/validators/schema.py:18-24`) and the loader tests run over every shipped file. Rejected: keeping Python constants and extracting only the engine (fixes duplication but leaves content inaccessible); rejected: JSON with no validation (a typo surfaces only when the seeder runs, with a poor message). |
| Q-2 | Seed the remaining six units at the same time? | No. | Structure first. The six units' answer-field design is an interpretation of the thesis appendix that has not been reviewed by the collaborating educator; inventing it inside a refactor would smuggle unreviewed content in under a "no behavior change" banner. |
| Q-3 | Does this depend on any unfinished dossier? | No — `depends_on: []`. | The only other open dossiers are `2026-07-07-graphrag-two-axis-redesign`, `2026-07-19-large-artifacts-silently-dropped`, and `2026-08-08-activity-governance-policy` (drafted same day). None touches `backend/smap/examples/`. The governance dossier touches `contexts/activities` and the admin surface, with zero file overlap. |

## 5. Current vs Target Structure

**Current**

```
backend/smap/examples/
  __init__.py                    docstring
  __main__.py                    Typer app, one @app.command
  creative_thinking_course.py    content + mechanism + report, all three
```

`creative_thinking_course.py` responsibilities: `CourseActivityType` dataclass `:27-41`,
`_mandala_schema()` `:47-64`, `_six_hats_schema()` `:67-105`, `COURSE_TYPES` `:111-124`,
`SeedReport` `:127-131`, `_seed()` `:135-166`, `run()` `:169-170`.

**Target**

```
backend/smap/examples/
  __init__.py                    docstring (unchanged intent)
  __main__.py                    Typer app; one command taking --course
  _catalogue.py                  load + validate a course JSON -> CourseDefinition
  _seeding.py                    SeedReport + seed_course(...) engine, course-agnostic
  courses/
    creative-thinking.json       data only: the two units
```

Dependency edges after the change: `__main__.py` → `_catalogue.py` + `_seeding.py`;
`_seeding.py` → `ActivitiesFacade` + `app.plugins.activity_validators` +
`shared_kernel.db.session`; `_catalogue.py` → `contexts.activities.application.validators.schema`
only (for schema well-formedness). `_catalogue.py` must **not** import `_seeding.py` or the
facade — it is a pure parser, which is what makes it cheap to unit-test over every shipped
file. No layer order in `backend/CLAUDE.md` changes; `smap` remains a top-level CLI package
that may import context facades (the maintenance/rotation precedent,
`backend/smap/maintenance/reconcile_attachment_sizes.py:37`).

**JSON shape** (one file per course):

```json
{
  "course_key": "creative-thinking",
  "title": "Creative thinking skills integrated into guidance activities",
  "source": "Ke Pei-jung (2019), MA thesis, National Taiwan Normal University",
  "activity_types": [
    {
      "key": "mandala-9grid",
      "name": "單元二 時空旅人",
      "validator_kind": "in_process",
      "validator_config": { "validator_id": "filled_count", "min_filled": 4 },
      "retention_days": null,
      "expose_payload_to_agent": true,
      "echo_includes_content": false,
      "payload_schema": { "...": "verbatim, as today" }
    }
  ]
}
```

Loader validation, all failing with a message naming the file and the offending key:
required fields present; `validator_kind` in the enum; `payload_schema` passes
`validate_schema_wellformed`; keys unique within a course; `min_filled` (when
`validator_id` is `filled_count`) is a non-negative integer **not greater than the number
of declared properties** — the same rule the authoring form enforces, so a shipped example
cannot be an activity nobody can pass.

## 6. Characterization Test Plan

The behavior to pin **before** moving anything. Existing coverage in
`backend/tests/unit/test_smap_examples_cli.py`:

| Behavior | Already pinned | Citation |
|---|---|---|
| Exactly two types, in order | yes | `TestSeededDefinitions::test_seeds_exactly_the_two_units` |
| Both schemas well-formed | yes | `::test_payload_schema_is_wellformed` |
| Visibility + retention settings | yes | `::test_visibility_and_retention_settings` |
| `filled_count` config valid | yes | `::test_uses_filled_count_with_a_valid_config` |
| Mandala is 9 fields incl. `center`, in order | yes | `::test_mandala_is_a_nine_field_schema_with_a_center` |
| Six-hats field set | yes | `::test_six_hats_covers_the_five_hats_plus_the_event` |
| A full submission passes schema validation | yes | `::test_a_realistic_submission_passes_schema_validation` |
| Registry empty → config rejected; after registration → accepted | yes | `TestSeededConfigsPassTheRealRegistrationGate` |
| `_seed` registers validators before touching the facade | yes | `::test_seed_registers_validators_before_touching_the_facade` |
| First run creates both; second run creates none; partial fills the gap | yes | `TestSeederIdempotency` |
| Audit actor is the `--owner-user-id` | yes | `::test_registers_with_the_operator_supplied_audit_actor` |
| CLI `--help` stays in group mode; bad UUID exits 1; failure exits 1 | yes | `TestSeederCli` |

**Gaps to close before moving code** (these are the characterization tests `/build` writes
first):

- **G-1** — no test asserts the *exact* seeded values (names, prompt `title`/`description`
  strings, `min_filled` numbers). The suite would not notice if the refactor silently
  altered a prompt during the Python→JSON transcription, which is the single most likely
  defect in this change. Add a snapshot-style assertion over the fully-resolved course
  before the move, and keep it after.
- **G-2** — no test asserts `run()`'s return value shape or that `__main__` logs
  created/already-present. Thin, but the command wrapper is being edited.
- **G-3** — no test covers a course file that is malformed, since no loader exists yet.
  New behavior, so this is a new test rather than a characterization one.

## 7. Migration Steps

Each step leaves the tree green.

1. **Pin the current data.** Add G-1's exact-value assertions against the existing
   `COURSE_TYPES`, plus G-2. No production change. Tree green.
2. **Extract the engine.** Move `SeedReport` and the seeding loop into `_seeding.py` as
   `seed_course(*, project_id, owner_user_id, activity_types) -> SeedReport`, keeping the
   validator registration inside it. `creative_thinking_course.py` keeps its constants and
   delegates. All existing tests pass unmodified. Tree green.
3. **Add the loader.** `_catalogue.py` with `CourseDefinition` / `CourseActivityType` and
   `load_course(course_key)`, plus G-3's malformed-file tests. Not yet wired. Tree green.
4. **Transcribe the data.** Create `courses/creative-thinking.json` from the existing
   constants. Add a test asserting the loaded course equals the Python constants
   field-for-field — this is the transcription safety net and it is why step 1 came first.
   Tree green.
5. **Switch the source of truth.** `__main__.py` calls `load_course` + `seed_course`; delete
   `creative_thinking_course.py`. Update the equality test from step 4 into the G-1
   assertions now reading from JSON. Tree green.
6. **Generalise the command.** `--course` option defaulting to `creative-thinking`, so the
   documented invocation in `docs/examples/creative-thinking-course.md:133` keeps working
   verbatim. Update that doc plus the "Where the pieces live" table. Tree green.

Steps 1-4 are additive and independently revertable; step 5 is the only one that removes
code, and it is a single file deletion after its replacement is proven equal.

## 8. Risks and Rollback

- **Transcription drift is the main risk** — a prompt string altered while moving Chinese
  text from Python to JSON. Mitigated by step 1 pinning exact values and step 4 asserting
  the JSON equals the constants before the constants are deleted. Both files exist
  simultaneously at step 4, which is the whole point of that ordering.
- **Encoding.** The JSON carries Chinese text; the loader must read UTF-8 explicitly rather
  than relying on the platform default, or a Windows dev host will mojibake it. Assert a
  non-ASCII prompt round-trips in the loader test.
- **Packaging.** `smap*` is a packaged module (`backend/pyproject.toml:87`), but JSON is not
  a `.py` file — the build must include `courses/*.json` as package data, or the CLI works
  from a source checkout and fails from an installed wheel. Verify explicitly; this is the
  failure mode that would not show up in any test run from the repo root.
- **`--course` default** keeps the documented command line working; if it were made
  required, the published doc would become wrong.
- Rollback is `git revert` per step.

## 9. Acceptance Criteria

- [x] AC-1: No externally observable behavior change — every characterization test from §6,
  including the step-1 exact-value assertions, passes unmodified after the move.
  (See D-3: assertions unchanged, patch targets and import paths re-pointed.)
- [x] AC-2: The §2 violation no longer exists: `_seeding.py` contains no course content and
  `courses/*.json` contains no logic, verified by reading both.
- [x] AC-3: `python -m smap.examples creative-thinking-course --project-id X --owner-user-id Y`
  still works with no new required flag, and still reports both types already-present on a
  second run. Verified at the real CLI out of process (`--course` shows
  `[default: creative-thinking]`, only the two UUID flags are `[required]`); the
  second-run no-op is pinned by `TestSeederIdempotency::test_second_run_registers_nothing`.
  The seed was not replayed against a live database — see the note under AC-9.
- [x] AC-4: `load_course` rejects, with a message naming the file and the offending key: a
  missing required field, an unknown `validator_kind`, a malformed `payload_schema`, a
  duplicate key within a course, and a `min_filled` exceeding the declared property count.
  `TestLoaderRejectsAMalformedCourse` covers these plus the D-4 additions.
- [x] AC-5: A non-ASCII prompt string round-trips through the loader unchanged (UTF-8 pinned
  explicitly, not inherited from the platform default). See D-6 on `utf-8-sig`.
- [x] AC-6: `courses/*.json` ships as package data — verified by loading a course from an
  installed/built artifact, not only from the source tree. A wheel was built with
  `python -m build --wheel`, and `load_course("creative-thinking")` was run against the
  extracted wheel from outside the repository, returning the course with its Chinese text
  intact. `tests/unit/test_smap_examples_packaging.py` pins the declaration, since building
  a wheel per test run is too slow for the unit tier.
- [x] AC-7: Adding a course requires **only** a new JSON file — demonstrated by a test that
  loads a fixture course from a temp directory and seeds it through `seed_course` with no
  production code change (`TestAddingACourseNeedsNoCode`).
- [x] AC-8: `docs/examples/creative-thinking-course.md` reflects the new layout (the "Where
  the pieces live" table and the seeding section), and gains an "Adding another course"
  section stating the loader's rules.
- [x] AC-9: Gates green — `ruff check . && ruff format --check .`, `mypy .` (919 files), and
  the unit tier: 6554 passed, 6 skipped, 0 failed (the skips are pre-existing, from a
  Windows host that cannot create symlinks). No frontend change, so frontend gates are N/A;
  no migration and no API contract change, so those contract gates are N/A too.
  Two things a reader should not mistake for coverage:
  - A bare `pytest -q` at `backend/` runs the `integration`, `db`, `wiring` and `e2e`
    tiers too — `addopts` carries no `-m` filter (`backend/pyproject.toml:412`) — and those
    need live Postgres/Redis/Vault, so locally they hang rather than fail. They ran on CI,
    not here. Nothing outside `tests/unit` references `smap.examples`.
  - The seeder was not replayed against a live database. The DB write path is
    character-identical to the deleted `_seed` (same facade call, same kwargs), and standing
    up the compose stack including a Vault unseal was disproportionate to that.

## 10. SRS Delta

None. [R30.28] already states that shipped examples are documentation and operator tooling
never registered automatically; this changes only how that tooling is organised, not what
the platform does.

## 11. Deviation Log

- **D-1 — `_catalogue.py` has more import edges than §5 states.** §5 allows it only
  `contexts.activities.application.validators.schema`. It also imports `ValidatorKind` and
  `PayloadSchemaInvalid`/`ValidatorConfigInvalid` from `contexts.activities.domain`, and
  `FILLED_COUNT_ID` + `validate_filled_count_config` from `app.plugins.activity_validators`.
  Reason: AC-4 requires checking `validator_kind` against the enum and `min_filled` against
  the `filled_count` id and its integer rule, and re-declaring either constant locally would
  drift from the rule the authoring form enforces — the drift this refactor exists to
  prevent. The edges §5 actually forbids (`_catalogue` → `_seeding`, `_catalogue` → facade)
  hold, and the additions are far more conservative than the `smap` package's existing norm
  (`smap/rotation/rotate_transit.py:31` and `smap/maintenance/repair_compaction_summaries.py:67`
  import infrastructure tables directly).

- **D-2 — `CourseActivityType` moved in step 2, not step 3.** §7 creates `_catalogue.py` in
  step 3, after the engine. Doing it that way would have forced `_seeding.py` to import its
  type annotation from `creative_thinking_course.py`, the module step 5 deletes. Moving the
  dataclass first removed the temporary edge. No change to what either step produces.

- **D-3 — §7 step 2's "All existing tests pass unmodified" could not hold.** The seeder
  tests monkeypatch `get_sessionmaker` and `ActivitiesFacade` on the module where the
  seeding loop resolves them, so moving the loop required re-pointing those patch targets;
  step 5 likewise re-pointed `COURSE_TYPES` from a module constant to the loaded course.
  Every behavioral assertion is unchanged — only patch targets and import paths moved.

- **D-4 — The loader rejects more than §5 lists.** Added: unknown fields, a `payload_schema`
  declaring no properties (mirroring the authoring form's `schemaEmpty` rule), a
  `retention_days` that is neither null nor a positive integer, and a `course_key` that
  disagrees with its filename. The unknown-field rule is the load-bearing one: without it a
  typo'd `expose_payload_to_agents` falls through to the permissive default, silently
  shipping a course that sends participant text to an LLM provider.

- **D-5 — All eight per-type fields are required, none defaulted.** §5's sample JSON shows
  all eight, and the dataclass defaults remain for Python callers, but a course *file* must
  state each one. Same reasoning as D-4: `expose_payload_to_agent` decides whether student
  writing reaches a provider, and inheriting that by omission is the wrong default for a
  file whose intended author is not an engineer.

- **D-6 — Course files are read as `utf-8-sig`, not `utf-8`.** Still an explicit pin rather
  than the platform default, so AC-5 holds. `-sig` additionally tolerates the byte-order
  mark a Windows editor commonly writes, which plain `utf-8` would keep as a leading
  character that then fails to parse as JSON — a failure the intended authors would hit.

- **D-7 — Four robustness fixes the gates surfaced, each with a regression test.** None
  changes a documented behavior; all close paths where a hand-edited file produced a
  traceback rather than a diagnosis. (a) A course saved as Big5 raised `UnicodeDecodeError`
  and an unreadable file raised `OSError`, both escaping `CourseFileInvalid`. (b) An absent
  catalogue directory raised `FileNotFoundError` out of `available_courses` — which is the
  exact shape of the packaging failure AC-6 guards against, so it now reads as an empty
  catalogue. (c) `_COURSE_KEY_RE` was anchored `^..$`; Python's `$` also matches before a
  trailing newline, so a key ending in one passed a guard written to reject anything that is
  not a bare filename component (no traversal was reachable through it). Now `\A..\Z`.
  (d) The command catches any remaining loader failure, so no input to the loader — a
  deeply nested document raising `RecursionError`, for instance — can crash the CLI.

## 12. Follow-ups

- **FU-1** — Absorbs FU-5 of `2026-08-08-creative-thinking-course-example` (six units
  unseeded). After this lands, seeding them is a content task: one JSON file, no code. The
  unit designs still need the collaborating educator's confirmation before being written.
- **FU-2** — A course JSON currently has no `$schema` and no editor tooling. If the
  catalogue grows past a handful of courses, a published JSON Schema for the course file
  itself would give domain experts autocomplete and inline errors.
- **FU-3** — The command is still named `creative-thinking-course` while taking a `--course`
  option that can name any course, and its log lines carry that name as a prefix. AC-3 fixed
  the name so the published invocation keeps working. When FU-1 adds a second course, rename
  to something course-neutral and keep `creative-thinking-course` as an alias so
  `docs/examples/creative-thinking-course.md` stays correct.
- **FU-4** — There is no way to list the catalogue. `available_courses()` exists but is
  reachable only indirectly, through the error message for an unknown `--course`. A
  `list-courses` command is the obvious affordance; deliberately not added here, since §3
  rules out new CLI capability.
