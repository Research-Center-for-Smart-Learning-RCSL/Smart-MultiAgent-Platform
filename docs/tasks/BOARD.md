# Task Board

Derived view over every dossier under `docs/tasks/` that is not
`implemented`/`superseded`/`abandoned`, grouped by `depends_on` + `status` per the rules
in `README.md`. If this file and a dossier's own frontmatter disagree, the frontmatter
wins — this is a cache, not a second source of truth. Maintained by `/spec` (adds a row
on dossier creation) and `/build` (moves a row on every status change).

Backfilled 2026-07-20 for the dossiers active at that date; the other ~80 dossiers under
`docs/tasks/` were already `implemented`/`superseded` and are intentionally not listed
here (see README.md's Dependencies and sequencing section for why untouched history
doesn't need a `depends_on` backfill).

## Ready now

Nothing blocking; these can start in any order relative to each other, including in
parallel.

### From the 2026-08-16 example-subsystem audit

Thirteen dossiers from `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`
(18 findings, grouped by blast radius so concurrent builds cannot produce conflicting diffs).
Every one is `depends_on: []`; the five majors are listed first. Three file-overlap pairs are
noted below — these are **not** sequenced, but whoever builds second must rebase rather than
assume.

- `2026-08-16-activity-submission-wakeup-gap` (bugfix, approved) — major. Submissions never re-arm
  the silence clock, so agents read worksheet time as a lull. Fix touches the clock only; a full
  wake-up evaluation would produce one agent turn per submission. **Carries an SRS Delta**
  amending [R15.02].
- `2026-08-16-migration-0076-retry-safety` (bugfix, approved) — major. 0076 half-applies and cannot
  be re-run, in both directions. Note the audit's originally proposed fix
  (`transaction_per_migration`) is a **no-op** for this defect. **Carries an SRS Delta** replacing
  [O4.04], which prescribes a mechanism Alembic does not have. Has an ops prerequisite: run
  `alembic current` against staging and prod before merging.
- `2026-08-16-admin-platform-type-edit-unreachable` (bugfix, approved) — major. The admin Edit action
  on an installed platform example does nothing once the row ages off a 200-row page. Adds
  `GET /api/admin/platform-activity-types`, so `gen:api` + `check:openapi-drift` apply.
- `2026-08-16-activity-type-key-collision-across-scopes` (bugfix, approved) — major. Two live types
  can share one key in a project's usable set. **Carries an SRS Delta** amending [R30.02], which
  is silent on the union [R30.33] created. Deliberately permissive, because refusing would
  overturn the approved `example-cli-seeder-scope-leak` Q-2.
- `2026-08-16-activities-install-error-contract` (bugfix, approved) — F-6 + F-7. An unknown course
  key returns 500; `min_filled` is never checked against the schema it scores. Adjacent-line
  overlap with `activity-type-key-collision-across-scopes` in `type_service.register`'s pre-flight
  region.
- `2026-08-16-platform-type-delete-optin-lifecycle` (bugfix, approved) — F-9. The type delete is
  soft, so the FK cascade never fires and every project's opt-in outlives its type; two docstrings
  and migration 0076's index comment all assume otherwise.
- `2026-08-16-example-dialog-pending-and-optout` (bugfix, approved) — F-10. D-14's single-valued
  pending-token defect, unfixed in the activities dialog and its admin sibling. **File overlap**
  with `admin-platform-type-edit-unreachable` in `ActivityExamplesSection.vue` (different
  regions).
- `2026-08-16-example-pack-prompt-grounding` (bugfix, approved) — F-12. The shipped AA prompt asks
  who has not submitted, against a 30-row window with no roster. Prompt content only; note the fix
  does not reach agents already installed copy-on-import.
- `2026-08-16-example-docs-corrections` (bugfix, approved) — F-13 + F-17. The walkthrough inverts the
  `filled_count` boolean rule and omits that the OpenAI fallback voids the packs' temperatures.
  **File overlap** with `example-pack-prompt-grounding` in
  `docs/examples/creative-thinking-course.md` (different sections).
- `2026-08-16-shared-common-i18n-namespace` (bugfix, approved) — F-15. The `common.*` namespace
  exists in no bundle, so 17 call sites render their English default arguments. Two JSON files; no
  call site changes.
- `2026-08-16-mandala-center-fallback` (bugfix, approved) — F-18. The mandala promotes the first
  property to the centre when none is named `center`, against [R30.36]. The rule conflict with an
  older AC-8 was triaged in [R30.36]'s favour on 2026-08-16.

### Other ready work

- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked


## In progress

- `2026-08-16-agent-pack-install-report-fidelity` (bugfix) — `depends_on: []`. F-8 + F-11 + F-16.
  A re-install after a group rename creates a second group while reporting that nothing was
  created; the dialog discards provider and activity-type data already on the wire; AC-14's
  design-agent sentence was never added. Adds a response field, so `gen:api` +
  `check:openapi-drift` apply.
- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
Removed on 2026-08-16 after implementation: `2026-08-16-example-cli-seeder-scope-leak` (the
example CLI seeder now keys idempotency on ownership via a new `list_owned_by_project` read
rather than on `list_types`' usable set, and warns per key that shadows an opted-in platform
type). Nothing lists it in `depends_on`, so no row moved out of Blocked. **One thing a later
reader needs:** its Q-2 warning and `2026-08-16-activity-type-key-collision-across-scopes`
(F-5) describe the same collision from two sides; when F-5 is built, its warning wording should
be reconciled with the seeder's rather than duplicated.
Removed on 2026-08-14 after implementation: `2026-08-13-creative-thinking-example-agents`
(two shipped agent packs installed copy-on-import into a project, the creative-thinking course
transcribed from its actual worksheets, and an explicit `x-order` on payload-schema
properties). Nothing lists it in `depends_on`, so no row moved out of Blocked. **Two caveats a
later reader needs.** AC-4's `db`-tier test — `tests/integration/test_activity_schema_key_order.py`,
which pins that `jsonb` really does discard payload-schema key order — has never been
executed: Docker was unavailable on the implementing host, so the entire `x-order` half rests
on reasoning until CI runs it, and §10 says what to do if it fails. And **D-12**: no
behavioural verification was performed at all, for the same reason; confirm the install flow
and the corrected worksheets on the first deployed build.
Removed on 2026-08-09 after implementation: `2026-08-09-platform-example-activity-types`
(migration 0076 gives `activity_types` a `scope`, the example catalogue moves out of the
`smap` CLI package into `contexts/activities/infrastructure/examples/`, and seven duplicated
tenancy checks collapse into one reachability rule gated on a per-project opt-in). Nothing
lists it in `depends_on`, so no row moved out of Blocked. Two things a later reader will
want: **D-1** — a platform-scoped type must declare an `in_process` validator, because mcp
and webhook validators have no project to run in; **FU-8/FU-9** — the catalogue and opt-out
queries are unbounded, bounded today only by how many examples an admin installs.
Removed on 2026-08-09 after implementation: `2026-08-09-chatroom-rail-scroll-and-resize`
(opt-in `fill` on `STabs`, the missing `min-height`/overflow on `.chatroom__presence`,
`ActivityPanel`'s own scroll region, a resizable persisted rail width, and
container-relative layout for activity plugins). Nothing lists it in `depends_on`, so no
row moved out of Blocked. **Carries an unusual caveat for a closed dossier:** AC-1, AC-3,
AC-5 and AC-12 are layout outcomes that jsdom cannot assert, and the dossier was closed
without the manual browser check (D-5). The reported symptom has been reasoned to be fixed
from the CSS, not observed fixed in a browser. Confirm on the first deployed build.
Removed on 2026-08-09 after implementation: `2026-08-08-activity-example-catalogue`
(`smap/examples/` is now `courses/*.json` + a validating `_catalogue.py` loader + a
course-agnostic `_seeding.py`; `creative_thinking_course.py` deleted, course JSON shipped as
package data). Nothing lists it in `depends_on`, so no row moved out of Blocked. It does
retire FU-5 of `2026-08-08-creative-thinking-course-example` as a code task: seeding the
other six units is now one JSON file and no Python, pending the collaborating educator's
confirmation of the unit designs (carried forward as this dossier's FU-1).
Removed on 2026-08-01 after implementation: `2026-07-22-wait-for-event-timer-and-join-ports`
(timer waits now arm their own `delay_seconds` via `workflow_event_resume`; the join
`timeout` port's absence is recorded via linter advisory + docs rather than built, per Q-2).
Nothing lists it in `depends_on`, so no row moved out of Blocked.
Removed on 2026-08-01 after implementation: `2026-07-22-turn-outcome-reporting` (C2 and C3's
frontend-only slices on 2026-07-31, then C1, C4 and C3's backend half once
`turn-idempotency-and-locking` released `turn_engine.py`). Nothing lists it in `depends_on`, so no
row moved out of Blocked. Two things it leaves behind that a later reader will want: **FU-10** —
`_post_commit` catches `Exception`, so a *cancellation* in the post-commit window still rewrites a
committed turn as failed; the fix belongs with `_finalize_failed_turn`, which
`turn-idempotency-and-locking` owns. **FU-11** — `agent.progress` beacons cover the gaps between
assembly steps, not a single provider call that outlasts the 120s watchdog. It also closes
`chatroom-socket-lifecycle`'s FU-8, which had been waiting on this dossier's C3.
Removed on 2026-08-01 after implementation: `2026-07-22-workflow-capability-enforcement`
(can_approve/can_instruct gated at runtime, advisory linter + picker markers, max_alive_subagents
bounds, migration 0073 applied and downgrade-checked). Nothing lists it in `depends_on`, so no
row moved out of Blocked.
Removed on 2026-07-31 after implementation: `2026-07-22-turn-idempotency-and-locking` (all six
commits C1–C6, migration 0072 applied and downgrade-checked). Nothing lists it in `depends_on`, so
no row moved out of Blocked. It does unblock `2026-07-22-turn-outcome-reporting`'s backend half:
that dossier's D-1 deferred C1/C3/C4 because `turn_engine.py` was being rebuilt here, and that
rebuild is now committed. Re-verify its citations before resuming — this work restructured
`run_turn` (the lock loop is wrapped in a `try/finally` that drains the coalesced trigger), split
`_run_locked`'s failure handling into a shared `_finalize_failed_turn` with a third `except` arm
for a lost lock, and changed `distributed_lock` to yield a `LockHandle` instead of a bool.

Removed on 2026-07-28 because their own frontmatter reads `implemented` and the board only
lists unfinished work: `2026-07-22-activity-session-authz-and-validation`,
`2026-07-22-workflow-run-cancellation`, `2026-07-28-activity-schema-participant-access`.
Also removed on 2026-07-29 after implementation: `2026-07-22-reingest-allowlist-propagation`,
`2026-07-29-knowledge-ingest-concurrency-and-enqueue`,
`2026-07-29-knowledge-upload-resource-bounds`, `2026-07-29-knowledge-ingest-ports`,
`2026-07-29-knowledge-document-ui-split`, and `2026-07-22-retention-sweep-fixes`.
Removed on 2026-07-29 after implementation: `2026-07-22-search-determinism-and-highlighting`.
Removed on 2026-07-30 after implementation: `2026-07-22-settings-form-reconciliation`. Nothing
listed it in `depends_on`, so no row moved out of Blocked.
Removed on 2026-07-31 after implementation: `2026-07-22-tool-dispatch-failure-categories`.
Nothing lists it in `depends_on`, so no row moved out of Blocked. It does change the ground
under `2026-07-22-turn-idempotency-and-locking`, which names it as a textual adjacency: this
work restructured `_stream_with_tools` (the tool-round loop is now a bounded `for` over
attempts with its own round counter, and the function returns `ToolLoopOutcome` instead of
`tuple[str, int]`), so that dossier's citations into the turn loop need re-verifying before
it starts.
Removed on 2026-07-30 after implementation: `2026-07-22-subagent-spawn-fail-fast`. Nothing listed
it in `depends_on`, so no row moved out of Blocked. It does validate two standing assumptions in
`2026-07-22-workflow-capability-enforcement`: `SubagentService.spawn` now has **zero** production
callers, so that dossier's Q-2 (no runtime gate for `can_create_subagent`) and its R6 (zero file
overlap) both hold as written.
