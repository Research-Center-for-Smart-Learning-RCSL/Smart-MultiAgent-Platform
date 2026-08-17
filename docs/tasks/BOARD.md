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
assume. All thirteen are now implemented and removed (see the notes below the In progress
list). This section is kept as the record of what that audit produced.

### Other ready work

- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked


## In progress

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
Removed on 2026-08-17 after implementation: `2026-08-16-platform-type-delete-optin-lifecycle`
(an admin platform-type delete now removes every project's opt-in explicitly and records the
count on the `activity_type.deleted` event, instead of relying on an FK cascade that a soft
delete can never fire). Nothing lists it in `depends_on`, so no row moved out of Blocked.
**Three things a later reader needs.** **The db tier is the only thing that found the real
defects, and it found two.** No Docker and no local PostgreSQL on the implementing host, so AC-1
shipped unverified (D-4) and was answered by CI: the first run failed, not on an assertion but on
**fixture teardown** — `audit_logs` has an `ON DELETE SET NULL` FK to `users` and an append-only
trigger that refuses the UPDATE that cascade performs, so dropping the `project` fixture's
throwaway user breaks for any test that emitted an audit event as it. This was the first `db`
test to do so; `tests/integration/conftest.py` now clears those rows under
`SET ROLE smap_audit_retention` (D-9), which is a landmine removed for everyone. The same push
also failed `frontend-gate-openapi-drift` twice: once because a FastAPI route **docstring** is
published as the operation description and `make openapi-types` was never run (D-8), and again
because the regenerated spec was committed with a **UTF-8 BOM** that PowerShell redirection adds
and `core.autocrlf` does not normalise (D-10). AC-1 is now green on run `31993358787`. D-5 still
stands: no behavioural verification in a browser.
**D-3** — the db test does more than the spec asked, because `check-quality` found that nothing
in the change exercised the real cursor: the unit tier mocks `result.scalars().all()`, so a
misread `RETURNING` clause would delete correctly, pass everything, and write
`optins_removed: "0"` forever; the test now reads the audit row back. And **D-7**, which outlives
this task: `tests/unit/test_graphrag_builder.py` **hangs indefinitely on this host**, in
isolation as well as in the tier, so 48 tests are silently unrunnable locally and only CI covers
them. Unrelated to this diff (last changed in `1c3bad6`), but somebody should chase it.
Removed on 2026-08-17 after implementation: `2026-08-16-shared-common-i18n-namespace` (the
`common.*` namespace now exists in both shared bundles, so all seventeen call sites resolve
instead of rendering their English default arguments). Nothing lists it in `depends_on`, so no
row moved out of Blocked. **Two things a later reader needs.** **FU-4** — the reason no test
caught this and the reason the next one will not either: `renderView` mounts the shared i18n
singleton with **no** bundle loaded at all, so all 182 component test files assert raw keys or
English defaults. A key deleted from `zh-TW.json` only is invisible to the entire suite the same
way; the cheap fix is a per-slice bundle-parity test, not a harness rewrite. And **D-2** — the
recurrence guard scans `src/` for `t('common.X')` and asserts every hit resolves, but it excludes
`__tests__/` and asserts the scan finds at least one call site, so a glob that silently stops
matching fails loudly rather than passing vacuously.
Removed on 2026-08-17 after implementation: `2026-08-16-mandala-center-fallback` (the mandala
grid resolves `center` as a named opt-in, so a nine-field schema declaring none renders in its
declared order instead of having its first field promoted to the middle). Nothing lists it in
`depends_on`, so no row moved out of Blocked. **Two things a later reader needs.** **D-1** — a
schema naming no centre now renders with *no* highlighted cell, because `isCenter` returns false
for every field once `centerField` is null; that is correct but was not stated in the spec, so
the test asserts the absence rather than leaving it implied. And **D-3** — no behavioural
verification (Docker unavailable), though the exposure is narrower than usual: the shipped course
declares `center`, so no shipped type changed and only projects reusing the `mandala-9grid` key
with their own centre-less schema see any difference.
Removed on 2026-08-17 after implementation: `2026-08-16-example-pack-prompt-grounding` (the AA
prompt no longer asks who has not submitted, states that its activity block is a bounded recent
window whose gaps are not evidence, and refuses coverage questions back to the teacher). Nothing
lists it in `depends_on`, so no row moved out of Blocked. **Three things a later reader needs.**
**D-1** — the prompt says 數十筆 rather than naming 30, because a literal number would be a second
uncoupled copy of `DEFAULT_ACTIVITY_WINDOW` that would silently start lying if the constant
moved; the figure lives in the walkthrough next to the constant instead, and **FU-5** records
that nothing ties the two. **D-3** — AC-8's dry-run checklist did not exist, so it was created
covering all five behavioural checks rather than adding one free-floating item to nothing. And
the operational point that outlives the diff: **the fix does not reach an installed deployment**
— pack agents are copied on import and install is idempotent by name, so any project that
already installed `creative-thinking-room` still holds an AA carrying the old prompt and must
edit or re-create that one agent by hand. Observations the old prompt already produced are not
retracted.
Removed on 2026-08-16 after implementation: `2026-08-16-example-dialog-pending-and-optout`
(both example surfaces now gate every action button on "is anything in flight" read off the
mutations themselves, and the hand-maintained `pendingId`/`installingKey` refs are gone along
with the `onSettled` clears that released the wrong request's lock). Nothing lists it in
`depends_on`, so no row moved out of Blocked. **Three things a later reader needs.** **D-1** —
the fix went further than §7.1 asked: *both* questions are now answered from vue-query, "is
anything pending" from `isPending` and "which row" from the mutation's own `variables`, which
deletes the second half of the root cause instead of patching it. That leaves
`AgentPackInstallDialog` as the last site still on the D-14 hand-maintained form, and **FU-5**
records the shared `@shared/composables` helper the three sites should collapse into. **D-2** —
the buttons gained a `:loading` spinner they never had: gating on "anything pending" disables
every button and destroys the only signal that a click registered, so the spinner replaces it.
And **D-6** — no behavioural verification, again (Docker unavailable); two user-visible changes
are unobserved, so confirm on the first deployed build. That is the fifth consecutive dossier
in this series to record the same gap.
Removed on 2026-08-16 after implementation: `2026-08-16-example-docs-corrections` (the
walkthrough now states the `filled_count` boolean rule the code actually implements and points
at `_is_filled`'s docstring as the authority, and a new Limitations entry says that the install
fallback's provider substitution voids the packs' shipped temperatures on OpenAI). Nothing
lists it in `depends_on`, so no row moved out of Blocked. **Two things a later reader needs.**
**D-4** — AC-7's em-dash rule was applied to the **whole** document, not only the two sections
this dossier owns: 23 occurrences, roughly 20 of them inside sections that
`example-pack-prompt-grounding` and `platform-type-delete-optin-lifecycle` own. Punctuation
only, no claim changed, but those dossiers will hit conflicts on lines they expected to merge
cleanly and must rebase. And **D-2** — AC-3 asked the entry to say "Claude and Gemini forward
temperature", which would have been a second per-provider claim of exactly the kind this
dossier exists to fix; the rule is per *resolved model*, and `claude-*-5` / `claude-opus-4-[7-9]`
reject sampling too, so the entry says that instead.
Removed on 2026-08-16 after implementation: `2026-08-16-migration-0076-retry-safety` (0076 is a
single transaction in both directions, the three stale copies of the `transactional_ddl` rule
are corrected, and a structural test pins the no-statement-before-an-autocommit-block rule
across all 80 migrations). Nothing lists it in `depends_on`, so no row moved out of Blocked.
**Two things a later reader needs.** The dossier was parked because AC-1/AC-2 could not be
measured; they are now measured, and the reason they were not is worth knowing. **D-7** — the
db-tier atomicity tests gate on `SMAP_SCRATCH_DATABASE_URL` and **nothing ever set it**, so they
had never executed anywhere while `backend-db` reported `68 passed, 5 skipped` and read as full
coverage. `ci.yml` now creates a `smap_scratch` database on the postgres service that job
already starts; the tier is at `70 passed, 3 skipped`. If that step is ever removed these go
quiet rather than red. **D-8** — the first run that actually executed them failed in both
directions on the *tests*, not the migration (SQLAlchemy 2.0 autobegins on the first `execute`,
so the pre-check assertion owned the transaction the migration needed). And the one thing still
outstanding, deliberately routed to FU-3 rather than held against the dossier: **production's
`alembic current` is still unread**, and prod has no automatic migration step at all (FU-5).
Removed on 2026-08-16 after implementation:
`2026-08-16-admin-platform-type-edit-unreachable` (the shipped-examples section resolves its
edit target from a new unbounded platform-only listing instead of one 200-row page of the
cross-project one, so an installed example can always be edited; the cards show stored values
rather than the course file's). Nothing lists it in `depends_on`, so no row moved out of
Blocked. **Three things a later reader needs.** **D-4** — §7.3's truncation warning was
*replaced*, not implemented: Q-1's unbounded route leaves no page limit to key one on, and
`admin.activities.truncated`'s "Showing the most recent {count}" could not be true of it, so the
section warns on an unresolved row instead, under a new key. **D-5** — §5's account of the
reseed defect names a trigger that cannot happen: vue-query's structural sharing returns the
*previous* object for a deeply-equal refetch, so an identical refetch never reaches the watcher
at all and the literal §8.2 test passed against the pre-fix code. The live case is a refetch
whose **contents** changed; both are now tests. This sharpens FU-4's sweep for
`watch(() => [`. And **D-6** — no behavioural verification, again (Docker unavailable); four
user-visible behaviours changed and none has been seen in a browser, so confirm on the first
deployed build — **D-9** sharpens that: a post-close `/code-review` found two more windows in
exactly the residual-state handling jsdom was asserting (the warning fired after a *successful*
install, and a row deleted by another admin blanked an open form), both now fixed with tests.
**File overlap** with the still-open `example-dialog-pending-and-optout` in
`ActivityExamplesSection.vue`: that dossier owns the `installingKey` pending state, this one
rewrote row resolution, the card rendering and the Edit button's guard around it. Rebase.
Removed on 2026-08-16 after implementation: `2026-08-16-activities-install-error-contract` (an
unknown admin `course_key` is now a mapped 404 carrying the shipped-course list instead of a
logged 500, and `_validate_validator_config` finally receives the `payload_schema` it must
score `min_filled` against, so `register`/`update` refuse an unpassable threshold with the same
422 every other validator-config refusal produces). Nothing lists it in `depends_on`, so no row
moved out of Blocked. **Three things a later reader needs.** **D-1** — it also closed the three
pre-existing `__all__` omissions in `activities/domain/errors.py`, which retires FU-3 of
`2026-08-09-platform-example-activity-types`. **D-2** — `pytest -q` was NOT run to completion:
the `integration`/`wiring`/`db` tiers need a live PostgreSQL and Docker was unavailable, so
they fail at connect (`getaddrinfo failed`); the `unit` tier, which holds every test this
dossier touches, is green. And the deliberate non-goal worth not undoing: a course file that
exists but does **not parse** still produces a 500, because that is a defect in the deployed
artifact and reporting it as "not found" sends an operator to the wrong place — a negative test
pins it.
Removed on 2026-08-16 after implementation:
`2026-08-16-activity-type-key-collision-across-scopes` (both doors onto a cross-scope key
collision now warn without refusing, the facilitator picker and the type list distinguish the
two rows by `scope`, and the activity signal carries `activity_type_id`/`activity_type_scope` so
a rule written from now on can pin one). Nothing lists it in `depends_on`, so no row moved out
of Blocked. **Four things a later reader needs.** This dossier **applied an SRS Delta** amending
[R30.02] (`REQUIREMENTS.md:2161`): key uniqueness is per scope, the collision is permitted, and
`scope` is the disambiguator — so a future dual-scope entity has a stated rule to follow.
**D-3** — the `opt_in` warning reports *state, not this call's effect*: a repeat opt-in that
inserts nothing still reports the collision it left behind, matching what
`smap/examples/_seeding.py` already does, and the field name `shadowed_by_platform` is
deliberately shared with the seeder's report. **D-8** — no behavioural verification at all
(Docker unavailable); four user-visible surfaces changed and none has been seen in a browser,
so confirm on the first deployed build. And the central trade, stated plainly: **the collision
is not prevented**, so an *already-stored* workflow rule naming only `activity_type_key` still
matches both types. The new optional `activity_type_scope` filter helps only rules written
after this change; FU-1 records the report that would tell an operator which existing rules to
edit by hand.
Removed on 2026-08-16 after implementation: `2026-08-16-activity-submission-wakeup-gap` (an
activity submission now re-arms the per-agent silence clock through a new
`triggers.evaluate_room_activity`, so an agent on `silence_minutes` no longer reads a class
filling in a worksheet as a lull). Nothing lists it in `depends_on`, so no row moved out of
Blocked. **Two things a later reader needs.** **D-1** — Q-3 justified importing the conversation
*application* layer into the route on a false premise: `activities.py` imports only from
`interfaces` and was clean under the route rule, so the fix goes through
`ConversationFacade.note_room_activity` instead. Behaviour and call site are exactly as approved.
And the deliberate non-goal worth not undoing: a submission re-arms the clock but is **not**
counted by `every_n_messages`, because the shipped teacher agent runs at `n=1` and counting would
mean one agent turn per student per submission. A negative test pins it.
Removed on 2026-08-16 after implementation: `2026-08-16-agent-pack-install-report-fidelity`
(the pack install report now carries `group_created`, and the dialog renders each agent's
preferred provider and bound activity types, reports the provider actually used, and states
that a design agent's drafts are copied by hand). Nothing lists it in `depends_on`, so no row
moved out of Blocked. **Two things a later reader needs.** **D-1** — no behavioural
verification was performed: Docker was unavailable, so the install flow was never exercised in
a browser and the `integration`/`db`/`wiring` test tiers are unrun locally; this dialog has now
shipped twice without a manual pass (the source dossier's D-12 was the first), so confirm on
the first deployed build. And **D-4** — this task's uncommitted work was stashed mid-build by a
concurrent session on the same branch; it was recovered intact, but the task base moved from
`bf1edcb` to `9bec23a` and both audit gates were run against the later base.
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
