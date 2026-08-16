---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.35]
depends_on: []
---

# The pack install report hides the group it created, and the dialog hides what it knows

## 1. Summary

Three defects on one surface: what the agent-pack installer tells the user.

- **F-8**: re-installing a pack whose agent group was renamed creates a **second** group holding
  all three agents, while the report reads `{"created": [], "already_present": [3 names]}` -
  textually identical to AC-8's "a second install creates nothing". The report has no
  created-versus-reused signal for the group at all.
- **F-11**: the dialog's own header comment claims it "shows which provider each agent will end
  up on". It does not, and it never shows which activity types each agent is written for either.
  Both fields are already on the wire and discarded, as is the resolved `model_hint` the install
  returns.
- **F-16**: AC-14 requires the dialog to state that the design agent belongs in the teacher's own
  room and that its drafts must be pasted into an agent by hand. Only the first half exists, as
  a badge.

F-8, F-11 and F-16 of
`docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`. Grouped because they
are one report, one dialog, and one pair of locale files.

## 2. Observed vs Expected

### F-8 - the second group

**Observed.** `_group_for`
(`backend/contexts/agents/application/example_service.py:328-351`) matches an existing group by
**exact name** (`:343`) and otherwise creates one (`:345-351`). Its docstring says it matches
"by name, matching how agents are deduplicated, so a re-install lands its new agents in the same
group rather than creating a second one beside it" - which holds only while the name is
unchanged.

Renaming a group is a supported Project Owner route
(`backend/app/api/v1/agent_groups.py:159-177`), and `uq_agent_groups_project_name_active` is on
`(project_id, name) WHERE deleted_at IS NULL`
(`backend/alembic/versions/0043_graphrag_owner.py:52-55`), so once the name has moved the second
create collides with nothing and succeeds.

`PackInstallReport` (`example_service.py:99-106`) carries `pack_key`, `created`,
`already_present`, `group_id` - and no signal for whether the group was created or reused. The
API model mirrors it field for field (`backend/app/api/v1/agents.py:502-513`, populated
`:590-603`), as does the generated client
(`frontend/src/shared/api-client/models/ExamplePackInstallReportOut.ts:13-18`). The dialog
toasts on `report.created.length` alone (`AgentPackInstallDialog.vue:108-117`) and never
mentions the group.

**The agents end up in both groups.** Tracing `install_pack` (`example_service.py:223-282`):
`:223` builds `existing` from every live agent name; `:233-236` puts each already-present agent's
id into `members` while `created` stays empty; `:265-271` creates the second group; `:272-282`
adds every id in `members` to it. Nothing removes them from the renamed group, and
`AgentGroupService.add_member` is idempotent per `(group_id, agent_id)` over a composite PK
(`backend/contexts/agent_groups/application/group_service.py:104-134`).

**Expected.** The report says a group was created. AC-8's "creates nothing"
(`docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:634-638`) must be true of the
whole install, not only of the agent rows, and §7 NFR of that dossier requires created and
already-present to be reported separately.

### F-11 - the dialog discards what it has

**Observed.** `frontend/src/slices/agents/components/AgentPackInstallDialog.vue:6-8` states the
dialog "shows which provider each agent will end up on -- the pack states a preference the
chosen group may not be able to serve". The per-agent list item (`:256-277`) renders exactly
three things: `agent.name`, `roleLabel(agent.room_role)`, and an installed badge. `onSuccess`
(`:108-117`) reads only `report.created.length`.

A whole-tree grep is decisive: `preferred_model_hint` and `binds_activity_types` appear in
`frontend/src` only in the generated model
(`frontend/src/shared/api-client/models/ExamplePackAgentOut.ts:8-15`) and a test fixture -
**zero occurrences in any `.vue` file**. `InstalledPackAgentOut.model_hint`
(`.../InstalledPackAgentOut.ts:5-10`) likewise.

The backend already sends all of it: `agents.py:542-549` populates `preferred_model_hint` and
`binds_activity_types`; `:592-599` populates `model_hint`. **No backend change and no
`gen:api` run is needed for F-11** - this is purely presentational.

**Expected.** `docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md:434-438` requires
the dialog to list agents "with role, orchestration summary, and the activity types each is
written for", and `:427` requires the response's model hint to be surfaced "so the UI can state
what it picked rather than implying the pack chose".

### F-16 - the design agent's write-back limit

**Observed.** `frontend/src/slices/agents/locales/en.json:12-39` and `zh-TW.json:12-39` are the
complete `agents.examplePacks` namespace. The only design-related string is `roleDesign` (`:34`),
rendered as a badge via `roleLabel` (`AgentPackInstallDialog.vue:131-135`, used at `:267`).
Nothing mentions manual copying; a search for `paste|hand|手動|貼上` across both files returns
nothing. `ExamplePackOut` has no `description` field, so the sentence cannot arrive as data.

**Expected.** AC-14 (`spec.md:655-657`): "the dialog and docs both state that DA belongs in the
teacher's own room and that its drafts must be pasted into an agent by hand". The docs half was
done; the dialog half was not. Reinforced by `spec.md:370-373`: "a 'design agent' that appears
to configure agents is the obvious misreading."

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | For F-8: add a report field, or make group resolution stable via a marker column? | **Add `group_created: bool` to the report. No migration.** | Not a user question. `agent_groups` has no metadata column to hold a `pack_key` (`backend/contexts/agent_groups/infrastructure/tables.py:19-42` is `id, project_id, name, concept_map_enabled, created_at, deleted_at`) and the domain model carries no more (`domain/models.py:15-21`), so a stable marker needs a migration **and** would put an agents-context concept into the agent-groups schema for one example feature. It would also arguably make things worse: with a stable marker, a re-install would silently add agents back into a group the teacher deliberately renamed away from. The install is already correct on the record that matters - every named agent lands in the returned group; what is broken is only the report's honesty. |
| Q-2 | Should the dialog go further and offer the existing groups, or warn about a pack-agent group under a different name? | **No. Report honestly and stop there.** | The install surface is deliberately minimal - a pack is repository data with no re-sync, no versioning and no authoring UI, all explicit non-goals of the source dossier (`spec.md:593-595`). Group-picking machinery is how an example installer becomes a management console. The honest report gives the owner what they need: "created a new group X" tells them a duplicate exists and they can delete it. |
| Q-3 | For F-11: show the pack's *preference*, or compute the *resolved* hint before install? | **Show the preference, labelled as a preference.** | The resolved hint is knowable only server-side, in `_hint_for` (`backend/contexts/agents/application/example_service.py:309-326`), against the chosen key group; there is no endpoint that answers "which providers does this key group carry" - `has_carried_provider_in_group` is a `KeysFacade` internal. Building one is a new API surface, i.e. a feature, not a bugfix. The dialog can be fully honest without it: show the preference, and the existing `keyGroupHelp` string (`en.json:25`) already explains the fallback policy. The **resolved** hint is then reported after the fact from `report.created[].model_hint`, which is what `spec.md:427` actually asks for. |
| Q-4 | For F-16: a conditional note keyed on `room_role === null`, or an unconditional one? | **Conditional**, mirroring the existing observer note exactly. | Not a user question - the file already has the pattern. `anyObserver` (`AgentPackInstallDialog.vue:98-100`) drives a `role="note"` block at `:182-191`; `anyDesignAgent` is its twin. Unconditional would be false for a deployment shipping only room packs. `room_role: null` is exactly and only the design agent (`creative-thinking-design.json:11` is the sole null in the shipped packs) and the generated type declares the union (`ExamplePackAgentOut.ts:14`), so the check is type-safe. |
| Q-5 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches the agents slice or `contexts/agents/application/example_service.py`. No sibling dossier from this audit edits `AgentPackInstallDialog.vue` or the pack install service. |

## 4. Reproduction

**F-8.**

1. As a Project Owner, install `creative-thinking-room`. Three agents and one group named
   `創造思考技法 課堂代理` are created.
2. Rename that group to `七年三班` via the agent-groups page.
3. Install `creative-thinking-room` again.

**Actual.** Response is `{"created": [], "already_present": ["TA 教師代理", "SA 學生代理",
"AA 分析代理"], "group_id": "<a new uuid>"}`, and the toast says nothing was installed. The
Agents page now shows **two** groups, both containing the same three agents. **Expected.** The
response and the toast say a new group was created.

**F-11.** Open the pack dialog in a project whose only key group carries OpenAI keys. The shipped
agents declare `preferred_model_hint: "claude"`. **Actual.** No provider appears anywhere in the
dialog before confirming, and no activity type is named for any agent. After installing, the
toast reads only "Installed 3 agent(s)"; the per-agent `model_hint` the server computed is
discarded. **Expected.** The preference is visible before, and the resolved provider after.

**F-16.** Open the dialog with `creative-thinking-design` listed. **Actual.** The DA row carries
a badge reading "Not for a class room" and nothing else; nothing says its drafts must be copied
by hand. **Expected.** A note states both.

## 5. Root Cause Analysis

**F-8.** One link: `_group_for` (`example_service.py:328-351`) returns only an id, so the caller
cannot know whether it created or found, and `PackInstallReport` (`:99-106`) was shaped around
agents alone. The source dossier reasoned carefully about name-based idempotency being fragile
under a rename (`spec.md:265-270`, `:591`) but scoped that reasoning to **agent** renames, and
the mitigation it leans on at `:592` - "the report telling the owner what it created" - is
exactly what does not exist for the group.

**Why no test caught it.** `backend/tests/unit/test_agent_example_service.py:177-187`
(`test_a_second_install_reports_already_present_and_creates_nothing`) is the AC-8 test, and it
builds the service with `groups=None`, so `list_groups` returns `[]` and `create_group` **is**
awaited during the test's own run - while the test asserts only `report.created == ()` and the
three already-present names. The AC-8 claim was verified against agents only.

**F-11.** Also one link, and it is an omission rather than a mistake: the data reaches the
component and is not rendered. The header comment at `:6-8` was written describing the intended
design and was never reconciled with what shipped, which is why the audit called it "the sharpest
part" - documentation that is simply untrue.

**F-16.** The AC was half-implemented: `roleDesign` satisfies "belongs in the teacher's own
room" as a badge, and the write-back sentence was never added. The test that claims the AC
(`frontend/src/slices/agents/__tests__/AgentPackInstallDialog.test.ts:111-120`) asserts only
that the `roleDesign` label appears, so the missing half was invisible to the suite.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Project Owners installing agent packs. F-8 leaves a duplicate group - untidy,
no data loss, no cross-tenant leakage, and `concept_map_enabled` defaults to false so the
duplicate does not widen retrieval by itself. F-11 and F-16 are informational: the install
itself is correct in both cases.

**Sibling suspects.**

| Site | Verdict |
|---|---|
| `install_course`'s idempotency report (`backend/contexts/activities/application/example_service.py:193-201`) | **cleared for this defect class** - `InstallReport` reports created and already-present separately and creates no secondary object. Its own staleness gap is documented as D-16 of the source dossier and was refuted as a finding by the audit. |
| Agent idempotency by name in the same install (`example_service.py:223`, `:233-236`) | **cleared** - the audit's refuter established that `uq_agents_project_name_active` (`backend/alembic/versions/0011_agents.py:103-105`) makes two live same-named agents impossible, so the name lookup is unambiguous. |
| `ExampleImportDialog` (the activities sibling) | **cleared for F-11/F-16** - it renders the consent notice and per-example detail it is required to. Its own defect is the pending-state race, owned by `2026-08-16-example-dialog-pending-and-optout`. |

**Systemic reading.** F-8 is the only place in the example subsystem where an install creates a
secondary object without reporting it. The general lesson - an acceptance criterion phrased
"creates nothing" must be asserted against every object the operation can create - is recorded
as FU-1.

## 7. Fix Design

**7.1 F-8 - an honest report.** `_group_for` returns `(group_id, created: bool)` instead of an
id. `PackInstallReport` (`example_service.py:99-106`) gains `group_created: bool`;
`ExamplePackInstallReportOut` (`agents.py:502-513`) gains the matching field, populated at
`:590-603`. This **is** a response-model change, so `pnpm run gen:api` and
`pnpm run check:openapi-drift` must both run - note D-8 of the source dossier
(`spec.md:785-791`), which records that regeneration is reproducible only against a resolved
dependency set and that the spec must be written without a BOM.

The dialog then reports it: when `group_created` is true the toast names the group, so
"nothing was installed" can never again be shown for a run that created one.

**7.2 F-11 - render what is already on the wire.** In the per-agent list item
(`AgentPackInstallDialog.vue:256-277`), add the pack's `preferred_model_hint` as a badge
**labelled as a preference** (Q-3), and the `binds_activity_types` list. In `onSuccess`
(`:108-117`), report the provider actually used, taken as the distinct set of
`report.created[].model_hint` - a set rather than a single value because agents can in principle
resolve differently when their preferred hints differ and only some are usable.

Correct the header comment at `:6-8` in the same change so it describes what the component does.

**7.3 F-16 - a conditional note.** Add `anyDesignAgent` beside `anyObserver`
(`AgentPackInstallDialog.vue:98-100`) testing `a.room_role === null`, and a matching
`role="note"` block modelled on `:182-191`. One new i18n key stating both halves of AC-14: the
design agent belongs in the teacher's own chatroom, and applying a draft to an agent is a manual
copy and paste.

**7.4 i18n.** New keys under `agents.examplePacks` in **both**
`frontend/src/slices/agents/locales/en.json` and `zh-TW.json` (`:12-39` in each): the preference
label, the bound-activities label, the resolved-provider toast, the group-created toast, and the
design-agent note. The two files are currently at exact key-set parity, so any addition must land
in both or gate #12 fails the build. Pack content itself (agent names, prompts) stays untranslated
- it is course data, per §7 NFR of the source dossier.

**Why none of this masks a symptom.** F-8's symptom is a report that contradicts what happened
and its cause is a report shape with no field for it. F-11's and F-16's symptom is absent
information whose cause is that it was never rendered. In all three the fix is at the cause.

**Data repair.** None. F-8 leaves duplicate groups on deployments where it has already occurred;
they are ordinary groups the owner deletes normally. Not repaired automatically - the platform
cannot tell a duplicate from a group the owner wanted.

## 8. Regression Test Plan

**8.1 F-8 failing test, backend.** In `backend/tests/unit/test_agent_example_service.py`: install
into a project where all three agent names exist **and** the only live group carries a different
name. Assert `report.created == ()`, `report.group_created is True`, and that `create_group` was
awaited once. Fails today - there is no such field. The `AgentGroup` import (`:23`) and the
`groups=` kwarg on the `_service` helper (`:80`, `:99-100`) already exist, so no new fixture
machinery is needed.

**8.2 Its converse.** Extend `test_reuses_an_existing_group_of_the_same_name` (`:156-175`) to
assert `group_created is False`, and extend the AC-8 test
`test_a_second_install_reports_already_present_and_creates_nothing` (`:177-187`) to assert the
group outcome - so that test finally says something about the object it silently creates.

**8.3 F-11 failing tests, frontend.** In
`frontend/src/slices/agents/__tests__/AgentPackInstallDialog.test.ts`: assert the rendered dialog
names each agent's preferred provider and the activity types it is written for. The `packAgent()`
fixture (`:37-47`) already returns `preferred_model_hint: 'claude'` and
`binds_activity_types: ['mandala-9grid']`, so no fixture change is needed. A second test asserts
the post-install toast names the resolved provider; the `installMock` (`:71-76`) already returns
`model_hint: 'claude'`.

**Harness change required**: the `useToast` mock (`:32-35`) currently returns fresh `vi.fn()`s
per call, so nothing is assertable. Hoist the spies before the toast assertions can mean
anything.

**8.4 F-16 failing tests, frontend.** Assert the design note renders when a pack carries a
`room_role: null` agent, and is absent when none does - mirroring the observer-notice pair at
`:91-100` and `:102-109`. Extend the existing AC-14 test (`:111-120`) rather than replacing it,
so both halves of the criterion are pinned.

**8.5 Must stay green.** The remaining tests in both files, in particular the cross-project
isolation tests in `test_agent_example_service.py::TestTenantIsolation` - this change touches
neither the guard order nor the project scoping.

## 9. Risks and Rollback

- **F-8 is an OpenAPI contract change.** One additive boolean, so no client breaks, but it
  requires the `gen:api` + `check:openapi-drift` cycle, which the source dossier records as a
  known trap (D-8, `spec.md:785-791`; the two commits before it were both fixes to this exact
  gate).
- **F-8 reports rather than prevents.** Duplicate groups remain creatable. Q-1 explains why
  preventing them is the wrong trade for an example installer, but it means the fix improves
  honesty rather than removing the state.
- **Toast length.** Reporting the resolved provider and the group in one toast risks a sentence
  nobody reads. Prefer two short lines or a compact form; the substance is that neither fact is
  silently dropped.
- **i18n parity.** The `agents` namespace is currently at exact parity across both locales;
  adding to only one fails the build (which is the gate working correctly).
- **Rollback**: `git revert`. The backend and frontend halves are independently revertable and
  should be separate commits, though reverting the backend alone would leave the dialog reading a
  field that no longer exists - so revert frontend-first if both go.

## 10. Acceptance Criteria

- [x] AC-1: The test from §8.1 fails before the fix and passes after: a re-install that creates a
  second group reports `group_created: true`.
  `test_a_renamed_group_makes_a_reinstall_create_a_second_one_and_say_so`; failed pre-fix with
  `AttributeError: 'PackInstallReport' object has no attribute 'group_created'`.
- [x] AC-2: A re-install that reuses the same-named group reports `group_created: false`, and the
  AC-8 test asserts the group outcome rather than agents alone.
  `test_reuses_an_existing_group_of_the_same_name` and
  `test_a_second_install_reports_already_present_and_creates_nothing`, the latter now also
  asserting `create_group.assert_not_awaited()`.
- [x] AC-3: The install toast never reads "nothing was installed" for a run that created a group;
  when a group is created it is named.
  `says both halves when a run created only a group` + `names the group when the install
  created one`. See D-5: the first of those was strengthened after close-out, because
  satisfying this criterion literally still left the owner less informed than before in
  exactly F-8's scenario.
- [x] AC-4: Before confirming, the dialog shows each agent's preferred provider, labelled as a
  preference rather than as the resolved value, and the activity types it is written for.
  `names each agent preferred provider and the activities it is written for`.
- [x] AC-5: After installing, the dialog reports the provider actually used, from
  `report.created[].model_hint`.
  `reports the provider actually used once the install returns` + `reports every distinct
  provider when agents resolved differently` (which also pins the de-duplication).
- [x] AC-6: The header comment at `AgentPackInstallDialog.vue:6-8` describes what the component
  renders.
- [x] AC-7: A note states that a design agent belongs in the teacher's own chatroom **and** that
  applying its drafts to an agent is a manual copy and paste; it is absent when no listed pack
  carries a `room_role: null` agent. Asserted as a pair, mirroring the observer notice.
- [x] AC-8: Every new string exists in both `en.json` and `zh-TW.json` under
  `agents.examplePacks`, and the two key sets remain identical. Verified 31/31 keys after
  D-5's addition, no asymmetry, plus gate #12 in `pnpm lint`.
- [x] AC-9: Gates green. `ruff check .` (all passed), `ruff format --check .` (943 files),
  `mypy .` (938 source files, no issues), `pytest tests/unit -q` (**6838 passed, 6 skipped**,
  24m54s), `pnpm lint`, `pnpm typecheck`, `pnpm test` (**1111 passed, 181 files**),
  `pnpm build`, `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`. Three qualifications, all recorded rather than
  waved past:
  - **`pytest -q` was run as `pytest tests/unit -q`.** `testpaths = ["tests"]` with no default
    marker filter (`pyproject.toml:431-444`), so the bare form also collects the
    `integration`/`db`/`wiring` tiers, which need Postgres+Redis+Vault from compose. Docker
    was down (D-1), so those tiers are **unrun here and belong to CI**, whose
    `backend-integration`, `backend-db` and `backend-wiring` jobs own them. The 6 skips are
    pre-existing (`test_workspace_volume_reconcile.py`, "host cannot create symlinks").
  - **`gen:api` + `check:openapi-drift` verified by equivalence, not by the script.**
    `check-openapi-drift.sh` asserts a clean `git status` after regenerating, and the codegen
    writes CRLF on Windows, which that check reports as drift while CI's Linux runners never
    produce it. Re-running the export and `pnpm run gen:api` was confirmed a **content**
    no-op against the committed spec and client (`git diff --numstat` empty), which is what
    the gate is actually asserting. The spec was written BOM-free and LF, per the source
    dossier's D-8.
  - The exported `openapi.json` diff is exactly the additive `group_created` boolean plus its
    `required` entry; nothing else in the 765 KB document moved.

## 11. SRS Delta

**None.** [R30.35] already requires installation to be "idempotent by agent name within the
project" and to create "ordinary project-scoped agents plus one agent group and nothing else".
This restores the reporting that makes that claim checkable; it defines no new behaviour.

Worth noting for a future reader rather than amending: [R30.35]'s "one agent group" is a
statement about a single install, not an invariant across re-installs, and the fix does not make
it one (Q-1).

## 12. Deviation Log

- **D-1: No behavioural verification.** Docker Desktop was not running on the implementing host,
  so the compose stack could not be launched and the install flow was never exercised in a
  browser. Both halves rest on unit tests and reasoning. This is the same constraint the source
  dossier recorded as its own D-12, which means the pack install dialog has now shipped twice
  without a manual pass; confirm on the first deployed build, specifically that the two toasts
  read sensibly together and that the per-agent badge row does not wrap badly at narrow widths
  (jsdom asserts neither).
- **D-2: Four new i18n keys, not five.** §7.4 planned separate keys for the preference label, the
  bound-activities label, the resolved-provider toast, the group-created toast and the design
  note. The resolved provider was folded into the existing `installed` message as a `{providers}`
  parameter rather than added as a fifth key and a third toast. Reason: §9 warned that reporting
  both facts risks "a sentence nobody reads" and asked for a compact form; one enriched success
  message plus one group message is the two short lines it recommended. `installed` is called
  from nowhere else, so widening its parameters breaks no other caller.
- **D-3: The test harness fix went further than §8.3.** That section asked only that the
  `useToast` spies be hoisted. Hoisting alone leaves AC-5 unassertable: the render harness loads
  no locale messages, so the real `t` returns the bare key and the resolved provider never
  appears in the string the toast receives. `useI18n` is therefore also faked, returning a `t`
  that appends serialised interpolation params. Verified safe for this tree - `SModal` and
  `SBadge` are the only components in it that call `useI18n`, and both destructure `t` alone.
- **D-5: One toast case reworked after close-out, on a code-review finding.** As first
  shipped, the `created == 0 && group_created` branch suppressed `nothingToInstall` and
  emitted only `groupCreated` - which satisfies AC-3 as written, but is F-8's own scenario and
  told the owner *less* than before the change about one thing: that every agent already
  existed. The two facts were traded rather than both reported. A fifth key,
  `nothingNewButGroupCreated`, now states both in one message, and the branch structure became
  a three-way (agents created / only a group created / genuinely nothing) rather than an
  `else if` plus a trailing `if`. Note this narrows D-2: the count is now five new keys after
  all, though still two toasts at most. Q-2 is untouched - it ruled out group-picking
  machinery, not saying what happened.
- **D-4: The task base commit moved mid-build, and this task's work was stashed by another
  session.** Work started at `bf1edcb`. While it was in progress a concurrent session committed
  `2026-08-16-example-cli-seeder-scope-leak` (three commits, `49f6197`..`9bec23a`) and ran
  `git stash`, which parked this task's uncommitted backend half. It was recovered from
  `stash@{0}` intact - the stash held exactly this task's five files and nothing else - and the
  base was re-baselined to `9bec23a`. Both audit gates in §Definition of Done were run against
  `9bec23a..HEAD`, not the original base. Q-5's "no unfinished dossier conflicts" still holds on
  the merits: the seeder touches `contexts/activities` and `smap/examples`, this task touches
  `contexts/agents` and the agents slice, and the two diffs do not intersect. What Q-5 did not
  anticipate was concurrent *sessions* on one branch rather than concurrent dossiers.

## 13. Follow-ups

- **FU-1**: **An acceptance criterion phrased "creates nothing" must be asserted against every
  object the operation can create.** AC-8 was verified green by a test that awaited
  `create_group` during its own run. Worth a sweep of other "creates nothing" / "is idempotent"
  ACs in `docs/tasks/` to see which were verified against only part of their operation.
- **FU-2**: `AgentPackInstallDialog.test.ts`'s `useToast` mock returns fresh `vi.fn()`s per call
  (`:32-35`), so no test in the file can assert a toast. That is why F-11's missing toast content
  was invisible. The same shape may exist in other slices' test setups.
- **FU-3**: The dialog cannot show the **resolved** provider before install because no endpoint
  answers "which providers does this key group carry" (Q-3). If that endpoint is ever built for
  another reason, this dialog is its first consumer and `spec.md:427`'s intent would be fully
  met rather than met after the fact.
- **FU-4**: `agent_groups` has no metadata column (Q-1), so nothing in the platform can record
  that a group came from a pack. That is deliberate today; if packs ever gain re-sync (OQ-2 of
  the source dossier), provenance becomes a prerequisite and this is where it would live.
- **FU-5**: `roleLabel` (`AgentPackInstallDialog.vue:131-135`) returns `roleDesign` for anything
  that is not `'observer'` or `'normal'`, while the new `anyDesignAgent` tests `room_role === null`
  exactly. They agree only because the union is currently `'normal' | 'observer' | null`; a third
  room role would be labelled a design agent while the design note stayed hidden. Found by the
  quality gate, pre-existing in `roleLabel`. Fix is a `switch` over the union with an explicit
  `null` arm, which the type checker would then keep exhaustive.
- **FU-6**: `_group_for` (`example_service.py:340-350`) reads every group in the project and
  matches by name in Python. Bounded only by how many groups a project has, and self-inflicted,
  so the security gate filed it as hardening rather than a finding - but a name-filtered
  repository read would bound it properly. Same family as the source dossier's FU-8/FU-9.
- **FU-7**: the group-created toast falls back to an empty name
  (`AgentPackInstallDialog.vue:124`, `?? ''`) if the installed pack is not in the catalogue list,
  rendering `Created the agent group ""`. Verified near-unreachable - the lookup runs before the
  query is invalidated, and the pack key came from the button rendered off that same list - and
  deliberately left, because every alternative fallback (`group_id`, `pack_key`) names something
  that is not the group's name. Recorded so the next reader does not have to re-derive why.
