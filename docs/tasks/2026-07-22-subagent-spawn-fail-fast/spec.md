---
type: bugfix
status: implemented
created: 2026-07-22
requirements: []
depends_on: []
---

# `subagent_spawn` parks for half an hour and then kills the run

## 1. Summary

The `subagent_spawn` workflow node creates an `agent_instances` row, arms a Redis callback
key, and parks. Nothing ever fires that callback — `SubagentService.destroy` has zero
production call sites — and nothing ever runs a turn for the spawned instance. The run sits in
`WAITING` until a watchdog force-fails it. The node's `success` port is unreachable.

**Scope, stated first: this dossier fixes the harm, not the feature.** Sub-agent *execution*
(G.8) was never built — only its bookkeeping. This dossier makes the node fail fast and
honestly on its `failure` port, so a workflow author learns in milliseconds that the capability
is unavailable instead of losing a run half an hour later to a misleading timeout. Building
hydration, turn execution and teardown is a **feature**, deferred to its own dossier.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-1, and the same
defect as `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-3 (both major,
both confirmed).

**Deviation from the assigned triage, recorded deliberately.** The a2a audit's hand-off assigns
F-1, F-27, F-28, F-29 and F-30 to one slug, `2026-07-22-subagent-execution-wiring`. Analysis
shows only F-1 is actionable today:

| Finding | Fixed here? | Why |
|---|---|---|
| **F-1** | **Yes — the harm** | The node stops parking and force-failing the run |
| F-27 | No — deferred | R15.22 inheritance has no reader because no runtime consumes an instance. Building the reader *is* the feature |
| F-28 | No — deferred | The claim-restore defect lives in a task with no production caller. Fail-fast removes the only park, making it more dead, not less |
| F-29 | No — deferred | A stale park timeout can only mis-fire if a park exists. Fail-fast deletes the park site; the defect becomes unreachable, not fixed |
| F-30 | No — deferred | `destroy` has zero production callers before *or* after this fix |

Writing all five into a bugfix would produce four changes with no test that can fail today and
no user-observable effect. They belong to the feature dossier as acceptance criteria, and are
listed in §13 so they are not lost. The assigned slug does not exist on disk, so renaming costs
nothing.

## 2. Observed vs Expected

- **Observed.** Three independent gaps compose into one dead node:
  1. **No hydration.** `backend/contexts/workflow/application/executors/subagent_spawn.py:68-73`
     calls `facade.spawn_subagent(...)`, which reaches
     `backend/contexts/orchestration/application/subagent_service.py:150-157` and does exactly
     one thing: INSERT an `agent_instances` row. Nothing dispatches a turn.
     `docs/implement/G-orchestration.md:183` specifies row creation "**and hydrates a
     short-lived runtime**"; only the first clause exists.
  2. **No teardown caller.** `SubagentService.destroy` (`subagent_service.py:182-215`) is
     exposed at `backend/contexts/orchestration/interfaces/facade.py:319-325` and called from
     nowhere. A repo-wide search for `wf:subagent_callback` returns only the writer
     (`subagent_spawn.py:82`) and the reader inside `destroy`'s own helper
     (`subagent_service.py:234`). `backend/app/workers/tasks/retention.py:494-496` states the
     condition in its own docstring: "Neither the synthetic root nor its workflow-spawned
     children are ever destroyed."
  3. **The node parks unconditionally by default.** `subagent_spawn.py:79`
     (`config.get("wait_for_all", True)`) → `:100-107` returns `park=True`;
     `backend/contexts/workflow/application/run_engine.py:647-653` flips the run to `WAITING`.
     Nothing resumes, so the run dies.

  **Which timeout actually kills it — correcting the finding's headline.** The run does not
  usually survive to 3600s. `backend/app/workers/tasks/workflow_watchdog.py:63-75` force-fails
  on idle, `idle_max_seconds` defaults to 1800
  (`backend/contexts/workflow/domain/models.py:198-200`), and a parked run accrues idle time
  because `steps.latest_activity_at` stops advancing. The observed reason is
  `idle_max_seconds exceeded`, not `subagent_timeout`. Both end in `force_fail`
  (`run_engine.py:402-431`).

  **A supporting, user-visible divergence.** `subagent_spawn.py:47` defaults `timeout_seconds`
  to 3600; `docs/workflow.schema.json:339` declares `minimum: 1, maximum: 600, default: 180`,
  and the key is optional (`:332`). Nothing injects schema defaults, so an omitting config gets
  **20x the schema maximum**. The frontend compounds it:
  `frontend/src/slices/workflow/components/config/SubagentSpawnConfigForm.vue:97,99-100` renders
  `?? 180` with `max="600"`, so the editor *shows* 180s while the backend uses 3600s, and
  `frontend/src/slices/workflow/constants.ts:9` seeds a new node without the key at all — so
  every palette-created node takes the 3600 branch.

- **Expected.** A capability the platform cannot deliver fails immediately and says so, rather
  than consuming a run's entire idle budget and reporting an unrelated cause.

  **Intent source.** `requirements: []` is a positive claim for *this* dossier: `[R15.18]`–
  `[R15.23]` describe the sub-agent feature, and this dossier does not implement them — it
  makes their absence honest. The expectation rests on internal consistency: the node's own
  `failure` port exists and, per §3 Q-2, is already guaranteed to be wired.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Bugfix, feature, or both? | **Bugfix now (this dossier) + feature later.** | Only F-1 is actionable today (§1). The reported harm is the hang, not the absence of sub-agents; removing the hang is fully within bugfix scope, and the fail-fast branch becomes the natural `if not feature_enabled` guard when the feature lands — so it is not throwaway work. |
| Q-2 | Is failing on the `failure` port safe for already-saved workflows? | **Yes, and this is stronger than first assumed.** | `backend/contexts/workflow/application/linter.py:52` lists `subagent_spawn` in `_MULTI_PORT_NODES`, and `rule_13_port_coverage` (`:527-555`) makes an unconnected `failure` port a **blocking save error** unless `on_error.strategy == "continue"`. Every saved workflow containing this node therefore already has a wired `failure` edge or an explicit continue strategy. Fail-fast lands on a path the author designed. `run_engine.py:601-610` then applies `on_error` normally, so `retry`/`fallback`/`continue` all behave. |
| Q-3 | Should the teardown callback simply be wired instead? | **No — unsound.** | Firing `destroy` after `spawn` would resume the node at `success` having performed zero work, with `output_variable` holding an instance id whose task never ran — a workflow that silently lies. Strictly worse than the current honest hang. |
| Q-4 | Should the node type be removed from the schema and the palette? | **No — badge it, do not remove it.** | `[R15.18]`–`[R15.23]` are still live requirements; nothing has been descoped. Removal is also the only option with a data-migration problem: `workflows.definition` is a JSONB blob validated on write, so any saved workflow containing the node becomes unloadable. `WorkflowNodeComponent.vue:45` and `NodeConfigPanel.vue:40` key off the type string, so an unknown type risks breaking round-tripping. |
| Q-5 | Should a blocking lint rule reject the node at save time? | **No — advisory warning only.** | `backend/contexts/workflow/application/workflow_service.py:135-140` (create) and `:179-184` (patch) call the **same** `validate_definition`, and `:185-189` raises on any error. A blocking rule would lock an author out of saving *any* edit to a workflow containing the node — **including the edit that removes it**. `validate_definition` (`linter.py:824-829`) computes `valid` from errors alone, so a warning is non-blocking on both paths and needs no create/update asymmetry. A blocking rule scoped to newly-added nodes would require diffing against the stored definition — machinery that does not exist and should not be built for this. |
| Q-6 | Should the dead worker tasks be deleted? | **No — keep them registered.** | Runs parked before deploy still hold Arq jobs for `workflow_subagent_timeout` (`backend/app/workers/main.py:267-268`). Removing the handlers turns those into job-not-found errors. Their existing guards (`backend/app/workers/tasks/workflow_steps.py:92-93`) already no-op safely on a terminal run. The feature dossier repairs them (F-28/F-29). |
| Q-7 | Does this depend on any open dossier? | No. `depends_on: []`. | Checked against `BOARD.md`. No open dossier touches `subagent_spawn.py`, the subagent service, or the workflow palette. |

## 4. Reproduction

1. Create a workflow `trigger → subagent_spawn → end`, with the `failure` port also wired to
   `end` — rule 13 rejects the save otherwise. Set `parent_agent_id` to any real agent in the
   project and leave `timeout_seconds` unset, which is the palette default
   (`constants.ts:9` omits the key).
2. Trigger a run.
3. Observe within seconds: an `agent_instances` row (`parent_id` set, `destroyed_at` NULL); a
   Redis key `wf:subagent_callback:{instance_id}` with TTL 3660; `workflow_runs.state = waiting`;
   no step ever completes.
4. At roughly 1800s the run is force-failed by `workflow_watchdog` with
   `idle_max_seconds exceeded`. The 3600s `workflow_subagent_timeout` job fires later and
   returns `no_op` (`workflow_steps.py:92-93`) because the run is no longer `WAITING`.

**Shortening the timeout for a test.** Do **not** reach for `timeout_seconds` — it controls the
3600s job that never speaks first. Either set the workflow's own `timeouts.idle_max_seconds`
(read via `RunContext.idle_max_seconds`, `domain/models.py:198-200`) low and invoke
`workflow_watchdog` directly, or — better for a regression test — call the executor and assert
on the returned `StepOutcome` (`park is True` today), which needs no timers at all.

## 5. Root Cause Analysis

**Root cause: the node's completion protocol has a writer and no reader.** `subagent_spawn.py:82`
writes `wf:subagent_callback:{instance_id}`; the only reader is
`SubagentService._fire_workflow_callback` (`subagent_service.py:217-251`), reachable only from
`destroy` (`:182-215`), which has no production caller. The park at `:100-107` is therefore
unconditional and terminal.

The deeper cause is that `docs/implement/G-orchestration.md:183` describes a two-clause
operation — create the row **and** hydrate a runtime — of which only the first clause was
built, and `docs/implement/H-workflow.md:79` records that the workflow node "**reuses** G.8". So
the node was always meant to be a thin caller over a G.8 runtime that was never delivered. **The
workflow layer is not where the hole is.**

**Aggravating factors:** the 3600s/180s divergence (§2), which means the failure takes 20x the
schema-declared budget to arrive; and the watchdog's idle timeout firing first, which reports a
cause unrelated to the actual defect.

## 6. Blast Radius and Sibling Suspects

**Blast radius of the defect.** Every workflow containing a `subagent_spawn` node — the whole of
G.8. Leaked state per run: a synthetic root instance (`subagent_service.py:54-89`) plus one child
per spawn, both with `destroyed_at` permanently NULL, reclaimed only by
`_sweep_orphaned_subagent_roots` (`retention.py:488-538`) and only after the owning
`workflow_runs` row is archived or deleted.

**Blast radius of the fix.** Saved workflows change from "hangs 30 minutes then fails" to "fails
in milliseconds on the `failure` port". Both are failures, and the new one respects `on_error` —
but see §9 R1 for the `continue` case.

**Sibling park sites — all three have live firers, confirmed:**

| Site | Firer | Verdict |
|---|---|---|
| `wait_for_event` | `backend/contexts/workflow/application/event_dispatch.py`, `backend/app/workers/tasks/workflow_signals.py` | live |
| `approval_gate` | `backend/app/workers/tasks/workflow_approvals.py:38-50` | live |
| `instruct` | `workflow_approvals.py:135-150` | live |
| **`subagent_spawn`** | **none** | **unique orphan** |

So this is an isolated hole, not a systemic pattern failure — which supports treating it as one
bounded piece of work.

**Related sibling, different slug.** `wait_for_event` with `event_type: "timer"` never fires and
is the editor's *default* new-node config (a2a audit F-2, `constants.ts:10`). Same class —
exposed in the palette but unwired — so the two fixes should adopt the same UX convention for
"available in the editor, not implemented". Cross-referenced here so they do not diverge.

**Frontend exposure is full and unqualified**, which is why a backend-only fix is insufficient:
`constants.ts:35` places the node in a palette category beside working types; `:23` gives it a
plain label; `NodeConfigPanel.vue:12,40` gives it a dedicated config form;
`WorkflowNodeComponent.vue:45,165` gives it an icon and border colour. Nothing marks it unbuilt.

## 7. Fix Design

**A — fail fast on the `failure` port.** In `subagent_spawn.py`, replace the spawn/park body
with an immediate `StepOutcome(state=FAILED, port="failure", error=...)` naming the capability
as unimplemented, **before** `ensure_subagent_root` and `spawn_subagent` are called — so no
`agent_instances` rows are created and no callback key is written. This also stops the orphan-row
pressure on `_sweep_orphaned_subagent_roots` at source, and neutralises the
`count_alive_children` lifetime-cap artefact (`repositories.py:537-546`) without touching it.

Keep the existing spawn/park code **out of the file** rather than behind a dead `if` — an
unreachable branch is exactly the debt that produced this defect. Keep the executor module, its
registration (`registry.py:49`) and `NodeType.SUBAGENT_SPAWN` intact: `test_executor_completeness`
requires every `NodeType` to resolve via `get_executor`.

**B — correct the timeout divergence.** `subagent_spawn.py:47` from 3600 to 180, clamped to the
schema maximum of 600. Strictly dead code once A lands, but it is a one-line fix the feature
dossier would otherwise re-derive, and it removes a documented-versus-actual lie. Better still,
hoist the schema default into a shared constant so the two cannot drift again.

**C — the frontend must say the feature is unavailable.** Minimum: an i18n-gated notice at the
top of `SubagentSpawnConfigForm.vue`, plus a badge on the palette label. All strings through
`$t()` per project rules, in both `locales/en.json` and `zh-TW.json`.

**D — advisory lint warning** in `linter.py:advisory_warnings` (`:715-785`), warning level only
per Q-5.

**Why this corrects rather than masks.** Masking would be shortening the timeout, or firing the
callback so the node resumes green. Both leave a node that claims to do work it never does.
Failing on the `failure` port is the *truthful* outcome: the node's contract is "spawn a
sub-agent and run its task", the platform cannot honour it, and the workflow's own declared
failure path is the designed channel for exactly that.

**Explicitly deferred to the feature dossier:** runtime hydration
(`backend/contexts/agents/application/runtime/` has **zero** references to `instance_id` or
`agent_instance`); turn execution against `agent_instances.task_description`; teardown wiring;
enforcement of the R15.22 inheritance matrix including the missing `graphrag_config_id` that
`G-orchestration.md:197` requires forced null (F-27); claim-restore and `_emit_resumed` in
`workflow_subagent_complete` (F-28); node-scoped park bookkeeping (F-29); idempotent `destroy`
with `getdel` (F-30); and the `count_alive_children` semantics.

## 8. Regression Test Plan

**`backend/tests/unit/test_workflow_executors.py` has no `SUBAGENT_SPAWN` class.** Its only test
classes are `TestConditionExecutor` (`:51`), `TestSetVariableExecutor` (`:141`),
`TestEndExecutor` (`:221`), `TestTriggerExecutor` (`:288`), `TestJoinExecutor` (`:358`),
`TestInstructExecutor` (`:420`) and `TestAgentInvocationExecutor` (`:519`). **The only node with
no executor test is the only node that never worked** — that absence is the direct reason this
survived, and it belongs in the dossier as a finding about the tests, not just the code.

Add `class TestSubagentSpawnExecutor`, modelled on `TestInstructExecutor:420-501`.

**The failing test comes first** — `test_spawn_fails_fast_on_failure_port`: call `execute` with a
valid config; assert `outcome.state is StepState.FAILED`, `outcome.port == "failure"`,
`outcome.park is False`, and a non-empty `outcome.error`. **Fails today**: the current code
returns `state=RUNNING, port="success", park=True` (`subagent_spawn.py:100-107`).

Then:

- `test_spawn_creates_no_instance_and_no_redis_key` — patch `OrchestrationFacade` and
  `get_redis`; assert `spawn_subagent`, `ensure_subagent_root` and `redis.set` are **never
  awaited**. **Fails today**: all three run (`:64,68,83`). This is the assertion that pins the
  actual defect.
- `test_wait_for_all_false_also_fails_fast` — with `{"wait_for_all": False}`, same assertions.
  **Fails today**: returns `SUCCEEDED`/`success` (`:109-113`) with an instance id in
  `output_variable` — the "workflow that silently lies" case.
- `test_output_variable_is_not_populated` — assert the configured `output_variable` is absent
  from `ctx.variables`. **Fails today**: set at `:77`.
- `test_executor_default_timeout_matches_schema` — assert the executor default is ≤ 600 and
  equals `docs/workflow.schema.json`'s declared default. **Fails today**: 3600 versus 180.

**Linter** — `backend/tests/unit/test_workflow_reference_scoping.py` hosts the `validate_definition`
tests (there is no `test_linter.py`). Add `test_subagent_spawn_emits_advisory_warning`: the issue
appears in `result.warnings`, `result.valid is True`, `result.errors` empty. **Fails today**: no
such rule exists.

**Frontend** — `frontend/src/slices/workflow/components/config/__tests__/` has **no test file at
all** today. Add one asserting the unavailability notice renders and comes from `$t()`, not a
literal. Extend `frontend/src/slices/workflow/__tests__/WorkflowEditorView.test.ts` for the
palette badge. **Fails today**: `constants.ts:23,35` carry no qualification.

**Not testable today, and say so.** F-27/F-28/F-29/F-30 have no production caller, so no test
written now can fail for the right reason. The single existing pointer is
`backend/tests/unit/test_orchestration_services.py:742`, which asserts the *absence* of
enforcement — **the feature dossier must invert that test, not add to it.**

## 9. Risks and Rollback

| Risk | Impact | Mitigation |
|---|---|---|
| **R1 — semantic change for `on_error.strategy: continue` nodes.** Runs now proceed *past* the node with `output_variable` unset, so a downstream `{{ var }}` interpolates empty. Previously the run never got there. | real behaviour change | Make it an explicit acceptance criterion and name it in the error string. |
| **R2 — a blocking lint rule would lock authors out.** `workflow_service.py:135-140,179-184` share one validator and `:185-189` raises on any error, so a blocking rule blocks the edit that removes the node. | authors cannot edit | Advisory warning only (Q-5). |
| **R3 — deleting the dead worker tasks would break in-flight runs.** | job-not-found errors | Keep both handlers registered (Q-6). |
| **R4 — orphaned rows from before the fix are not cleaned up.** | pre-existing leak persists | Acceptable; state it plainly. Do **not** claim the fix cleans up. |
| **R5 — removing the node from the palette risks breaking round-tripping** of saved definitions. | editor breakage | Badge rather than remove (Q-4). |
| **R6 — "sub-agents cancelled" misreading.** | stakeholder confusion | The code comment and the dossier must both say *deferred to the feature dossier*, not *removed*. |

**Rollback.** Fix A is a single-function revert in one file; B is one literal; C is additive i18n
and template. No migration, no schema change, no persisted state written or destroyed. The three
revert independently with no ordering dependency.

## 10. Acceptance Criteria

- [x] AC-1: `test_spawn_fails_fast_on_failure_port` (§8) fails against current code and passes
      after the fix. *Verified: failed with `state=RUNNING, port=success, park=True,
      timeout_ms=3600000` before the fix; passes after.*
- [x] AC-2: executing a `subagent_spawn` node creates **no** `agent_instances` row and writes
      **no** `wf:subagent_callback` key. *`test_spawn_creates_no_instance_and_no_redis_key` —
      failed with "ensure_subagent_root awaited 1 times" before the fix.*
- [x] AC-3: both `wait_for_all: true` and `wait_for_all: false` take the `failure` port; neither
      returns `success`, and `output_variable` is never populated.
      *`test_wait_for_all_false_also_fails_fast`, `test_output_variable_is_not_populated`.*
- [x] AC-4: the node's error string names the capability as not implemented and points at the
      feature dossier, so the failure is self-diagnosing. *`test_error_is_self_diagnosing`.*
- [x] AC-5: a run whose node carries `on_error.strategy: continue` proceeds past the node with
      `output_variable` unset — verified deliberately, since it is R1's behaviour change.
      **Only true after the engine fix in D-2** — as approved, this AC asserted behaviour the
      engine could not deliver. See D-2; covered by
      `test_on_error_continue_proceeds_with_output_variable_unset` plus
      `test_continue_advances_normally_when_the_resolved_port_is_wired` and
      `test_continue_with_no_matching_edge_fails_the_run_instead_of_stalling`.
- [x] AC-6: the executor's default `timeout_seconds` equals the schema's declared default and is
      within the schema's maximum. *`test_executor_default_timeout_matches_schema` reads
      `docs/workflow.schema.json` at runtime, so the two cannot drift again. See D-3.*
- [x] AC-7: the workflow editor marks the node unavailable, in both locales, with no hardcoded
      strings. *5 tests in `components/config/__tests__/SubagentSpawnConfigForm.test.ts` +
      2 in `WorkflowEditorView.test.ts`; locale parity asserted for `en` and `zh-TW`.*
- [x] AC-8: `validate_definition` emits a warning, not an error, for a definition containing the
      node — and saving such a definition still succeeds, including an edit that removes it.
      *`test_subagent_spawn_emits_advisory_warning` (`valid is True`, `errors == []`) and
      `test_removing_subagent_spawn_still_validates`.*
- [x] AC-9: `workflow_subagent_timeout` and `workflow_subagent_complete` remain registered
      worker tasks. *`test_subagent_tasks_stay_registered` asserts both are in
      `WorkerSettings.functions`.*
- [x] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` pass in `frontend/`.
      All four backend test tiers were run, the last three against a live
      `docker-compose.yml + compose.test.yml` stack at schema head `0071`:

      | Tier | Command | Result |
      |---|---|---|
      | unit | `pytest tests/unit` | **6222 passed**, 6 skipped |
      | integration | `pytest -m "integration and not wiring and not db"` | **250 passed** |
      | db | `pytest -m db` | **52 passed**, 3 skipped |
      | wiring | `pytest -m wiring` | **55 passed**, 3 skipped, 2 failed |

      The 2 wiring failures are `test_rag_ingestion.py::test_process_document_indexes_registered_doc`
      and `::test_process_document_reprocesses_failed_doc`. **Proven pre-existing**: both fail
      identically at base commit `65aa8ac` in a clean worktree, and neither touches any file this
      task changed. Recorded as FU-14.

      `ruff check .` and `ruff format --check .` clean over 889 files; `mypy .` clean over 888.
      Frontend: `pnpm test` 932/932, `pnpm lint`, `pnpm typecheck`, `pnpm build` all green.

## 11. SRS Delta

None. `[R15.18]`–`[R15.23]` remain live and unamended — this dossier does not descope the
sub-agent feature, it makes its absence honest until the feature dossier delivers it. Amending
the SRS here would assert the platform had decided not to build G.8, which is not the decision
being made.

## 12. Deviation Log

- **D-1 — deleted `TestSubagentSpawnClaimTtl`, which §8 asserted did not exist.**
  §8 states "`backend/tests/unit/test_workflow_executors.py` has no `SUBAGENT_SPAWN` class" and
  lists seven test classes. That was true when this dossier was written (2026-07-22) and false
  by build time: `2026-07-23-claim-ttl-single-source` landed a day later and added
  `TestSubagentSpawnClaimTtl`, which pinned `outcome.park is True` and the TTL of the very
  `redis.set` AC-2 deletes. The class characterised a code path this dossier removes, so it was
  deleted rather than weakened; `TestSubagentSpawnExecutor` now pins that **no** claim key is
  written. `domain/claim_ttl.py`'s comment naming `subagent_spawn` as a producer was corrected
  in the same commit. **Agreed with the user before any code was written.**

- **D-2 — AC-5 and §9 R1 asserted behaviour the engine could not deliver; the engine was fixed.**
  Both claim a node with `on_error.strategy: continue` "proceeds past the node". It did not.
  `_apply_on_error` CONTINUE hardcoded `port="default"` (`run_engine.py:761-766`), but
  `_ALLOWED_PORTS["subagent_spawn"] = {"success","failure"}` (`linter.py:39`) makes a `default`
  edge a rule-3 **blocking save error**, so no saved workflow can have one. `_advance_from`
  matched nothing and returned silently (`:735-736`), leaving the branch stopped and the run
  `RUNNING` until the watchdog force-failed it on `idle_max_seconds` — the same 30-minute death
  this dossier exists to remove, reinstated for every `continue` node. §3 Q-2's claim that
  "`retry`/`fallback`/`continue` all behave" was wrong for `continue`.
  Reported before implementing; **the user chose to fix the engine inside this task** over
  correcting the claim or re-speccing. Scope therefore expanded to `run_engine.py`:
  - `_CONTINUE_PORTS` / `_continue_port()` resolve the port per node type — `success` for
    `agent_invocation` / `instruct` / `subagent_spawn`, the declared `default_port` for
    `condition`, `default` elsewhere.
  - **`approval_gate` resolves to `rejected`, never `approved`.** Routing an errored approval
    gate to `approved` would be an authorization bypass. It only fires when the executor raises
    *before* parking (`approval_gate.py:141-146` parks on `default`; the real verdict arrives
    later via `resume_at_port`), so it can never override a resolved vote. Fail-closed before
    and after. Pinned by `test_on_error_continue_never_manufactures_an_approval`.
  - `_advance_from` now returns whether an edge matched; a `continue` node whose resolved port
    has no outgoing edge fails the run immediately, naming the port and carrying the original
    error, instead of stalling.
  This changes `on_error` semantics for `agent_invocation`, `instruct` and `approval_gate` too —
  all three previously stalled on `continue` and now follow their declared path. §11's "SRS
  Delta: none" still holds; `docs/workflow.schema.md`'s `continue` description was updated to
  match.

- **D-3 — fix B landed as a module constant, not an edited literal.**
  §7 B says change `subagent_spawn.py:47` "from 3600 to 180". Fix A deletes the config read
  entirely, so there is no literal left to edit. The value became
  `DEFAULT_TIMEOUT_SECONDS = 180` at module scope, and
  `test_executor_default_timeout_matches_schema` parses `docs/workflow.schema.json` at runtime
  and asserts equality plus min/max containment — so the divergence §2 documented cannot recur.
  This is §7 B's own preferred form ("better still, hoist the schema default into a shared
  constant"), and it satisfies AC-6 without leaving an unreachable branch.

- **D-4 — corrected `retention.py`'s `_sweep_orphaned_subagent_roots` docstring.**
  Listed in §13 FU-8 as a follow-up, but fix A made it a direct, immediate falsehood rather than
  a latent one: `ensure_subagent_root` / `ensure_root_instance` now have **zero** production
  callers (verified repo-wide), so a docstring describing synthetic-root creation in the present
  tense misdescribes live code. Corrected in place with a pointer to this dossier; the sweep
  itself is untouched, and §9 R4 still holds — pre-existing orphaned rows are not cleaned up.

- **D-6 — post-review round; two more siblings of D-2's port defect fixed, one regression
  in D-2's own guard corrected.** `/code-review` over the branch found that D-2 had fixed the
  `continue` path but left the same hardcoded-`"default"` mismatch at two other sites, and that
  the new guard over-reached. All verified against the code before acting:
  - **The dry-run mock had the identical defect** (`run_engine.py:650-655`). `_DRY_RUN_SAFE_TYPES`
    excludes all four multi-port types, so it synthesised `port="default"` for exactly the nodes
    that cannot emit it — **every dry run dead-ended at its first `agent_invocation`**. Fixed by
    the same helper, renamed `_continue_port` → `_normal_completion_port` since it now serves both
    callers. Pre-existing, but the same bug this dossier exists to remove and one block away from
    D-2's fix, so it was treated as a confirmed sibling per /build's sibling-sweep rule.
  - **`resume_at_port` discarded the new `_advance_from` boolean** (`:451`). Rule 13 stops
    requiring port coverage once `on_error` is `continue`, and W3 (a `wait_for_event` with no
    `timeout` edge) is advisory only, so a resolver can legitimately resume onto an unwired port
    and the branch stalled. It now fails the run with a named port — and still returns `True`,
    because the resume *did* happen and the caller must consume its single-shot claim; returning
    `False` would start a restore-and-retry loop that cannot succeed.
  - **Regression in D-2's guard, introduced by this task.** Linter rule 5 deliberately permits a
    `wait_for_event` with **no** outgoing edges (`linter.py:302`, "permanent listener?", a warning
    not an error). The guard would have failed the whole run for such a node, cancelling healthy
    parallel siblings. Now gated on `_has_outgoing`, which distinguishes "the author wired nothing
    here on purpose" from "the author wired other ports but not this one".
  - The executor docstring claimed rule 13 means "every saved workflow containing this node
    already has that path wired" — false precisely in the `continue` case it names as the
    exception. Removed. The editor copy told authors to wire `failure`, which is the wrong port
    under `continue`; both locales now name both cases.
  - A stale comment at `run_engine.py:420` still listed `subagent_spawn` as a parked executor.
    Corrected. (The review also flagged `workflow_steps.py:28` for the same drift — **checked and
    rejected**: that docstring is about parallel fan-out and never mentions `subagent_spawn`.)

  **One review finding rejected on the merits.** It called `DEFAULT_TIMEOUT_SECONDS` dead code
  whose test only proves a copy matches its source. That is accurate as far as it goes, but the
  constant is what AC-6 and §7 B ask for, and D-3 records why it took this shape. Removing it
  would fail an approved acceptance criterion, so it stays; if the constant is genuinely unwanted,
  that is an AC-6 amendment for the user, not a review-time edit.

- **D-5 — documentation touched beyond the spec.** `docs/workflow.schema.md` §5.2 gained the W7
  advisory rule, and its `continue` on-error description was rewritten for D-2. The spec named
  neither, but leaving the normative schema doc contradicting the code it describes is the exact
  class of drift §2 records as this defect's aggravating factor.

## 13. Follow-ups

The four deferred findings, recorded here so the feature dossier inherits them rather than
rediscovering them. Each is quoted from `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md`.

- **FU-1 (a2a F-27)** — R15.22 inheritance is written to `run_context`
  (`subagent_service.py:257-283`, stored at `:155`) and read by nothing. The only non-test
  readers of `run_context` read `synthetic_root` / `workflow_run_id` only
  (`retention.py:508-511`, `repositories.py:465,494`). `graphrag_config_id` is also **absent**
  from the dict although `G-orchestration.md:197` requires it forced null.
- **FU-2 (a2a F-28)** — `workflow_steps.py:121` awaits `resume_at_port` and discards the `bool`,
  while `subagent_service.py:250-251` has already deleted the claim. `run_engine.py:336-341`
  states the contract that a `False` on a non-terminal run **must** restore the claim and retry.
  Compliant siblings to copy: `workflow_approvals.py:167-196` and `workflow_signals.py:53-64`.
  `_emit_resumed` is also never called, so the `workflow.resumed` audit every other resume path
  emits is missing.
- **FU-3 (a2a F-29)** — `workflow_steps.py:91-94` guards only on run state, and `force_fail`
  takes only `run_id`. `run_engine.py:637-642` concedes the engine "can only observe *this run is
  WAITING*, not which parked node it is waiting on", and `:649-652` enqueues the deferred job
  with a trailing `None` job_id so it cannot be cancelled. The callback key is keyed on instance
  id, not `(run_id, node_id)`, so the timeout job has nothing to claim. Contrast
  `workflow_signals.py:27-64`, which `GETDEL`s a node-scoped key and self-cancels when stale.
- **FU-4 (a2a F-30)** — `subagent_service.py:188-192` calls `destroy` with no check on
  `instance.destroyed_at`, and `repositories.py:518-523` re-stamps unconditionally, so
  `SUBAGENT_CONCURRENCY.dec()` (`:195-197`) runs once per call. `_fire_workflow_callback` uses
  non-atomic `get`/`delete` (`:235`, `:251`) where siblings use `getdel`. Double workflow-advance
  is refuted (`run_engine.py:346-347`); the residual is a negative Prometheus gauge and a
  re-stamped `destroyed_at` pushing the row further out of the purge window.
- **FU-5** — `count_alive_children` (`repositories.py:537-546`) counts `destroyed_at IS NULL`,
  which never transitions, so `max_alive_simultaneously` behaves as a **lifetime** cap per
  workflow run and the 4th spawn raises `SubagentConcurrencyExceeded`. The predicate is *correct*
  for a working system — do not "fix" it by relaxing it.
- **FU-6** — `max_alive_simultaneously` is sourced from the workflow node config
  (`subagent_spawn.py:45,72`), never from an agent column, so R15.20's "configurable per parent
  agent" has no implementation. Recorded as the a2a audit's own FU-1.
- **FU-7** — The most valuable reuse for the feature dossier: `agent_invocation` already solves
  "run an agent turn from a workflow node and get a reply back", including timeout and error
  propagation, via `facade.a2a_call(from_agent_id=None, ..., workflow_run_id=ctx.run_id)`
  (`agent_invocation.py:41-47`; `orchestration/interfaces/facade.py:60-79`;
  `a2a_service.py:124-190`). The feature's likely shape is spawn row + inheritance-restricted
  `a2a_call`-style turn + destroy — **not** a new execution stack.
- **FU-8** — `subagent_spawn.py:1-12`'s module docstring documents an "Orchestration completion
  hook" that "OrchestrationFacade **should**" implement. The aspirational mood is the defect,
  written down; this fix must rewrite it. Likewise `retention.py:494-496` is a sweep whose
  docstring documents the bug it works around — it should carry a pointer to the feature dossier
  rather than read as a permanent subsystem. **Both done in this task** (the executor docstring
  as part of fix A, the retention docstring as D-4).

Discovered during /build's Definition of Done and deliberately **not** fixed here:

- **FU-9 (quality gate, pre-existing)** — **a `failure` port edge is never traversed for any
  multi-port node.** Under the default `fail` strategy `_apply_on_error` returns the outcome
  unchanged (`run_engine.py:828-829`) and `_execute_node` calls `_fail_run` at `:709-711`
  *before* reaching `_advance_from` at `:714`. So the `failure` edges that linter rule 13
  **forces** authors to wire on `agent_invocation`, `instruct`, `approval_gate` and
  `subagent_spawn` are dead in the default configuration — the linter demands a path the engine
  never takes. Not blocking here: AC-1 is about the `StepOutcome`'s port, and the run still fails
  in milliseconds with a self-diagnosing error. Predates this task and affects three other node
  types identically, so it needs its own dossier rather than a widened `continue` fix.

- **FU-10 (security gate, hardening)** — rule 13 exempts a node from port coverage whenever
  `on_error.strategy == "continue"` (`linter.py:545`), so an author can save an `approval_gate`
  with `continue` and no wired ports at all. D-2's guard now fails such a run immediately and
  names the port, which is fail-closed and diagnosable, but `continue` on an approval gate is a
  questionable authoring pattern that deserves its own advisory warning beside W7.

- **FU-11 (security gate, hardening, pre-existing)** — executor error text flows into audit
  metadata and the `workflow_channel(run_id)` WebSocket publish via `_finalize_run(reason=...)`
  (`run_engine.py:898-912`), on both the pre-existing `fail` path and D-2's new guard. No path
  was found where a decrypted provider key reaches `outcome.error` — keys are envelope-encrypted
  and never materialised into exception text — but this was not proven exhaustively across all
  11 executors. Worth a scrub review of executor error construction generally.

- **FU-12 (quality gate)** — `OrchestrationFacade.ensure_subagent_root`
  (`orchestration/interfaces/facade.py:331`) and `SubagentService.ensure_root_instance`
  (`subagent_service.py:54`) now have **zero** production callers, joining `destroy` (FU-4).
  Deliberate, and recorded so the feature dossier revives them rather than a later dead-code
  sweep deleting them.

- **FU-13 (verification gap — mostly closed)** — the `integration`, `db` and `wiring` tiers have
  now been run against a live stack; see AC-10 for the counts. The two files that touch this
  task's surface, `tests/integration/test_retention_subagent_root_sweep.py` and
  `tests/integration/test_workflow_join_epoch.py`, both **pass**, as does
  `tests/wiring/test_wiring.py::test_workflow_golden_run`. **What remains:** the full-app visual
  check of AC-7 (Definition of Done gate 4) is still satisfied only at component level — the
  tests mount the real components with the real i18n bundle, but no one has looked at the running
  editor. That needs `backend-web` + the Vite dev server, not just the data services.

- **FU-14 (pre-existing, unrelated)** — `tests/wiring/test_rag_ingestion.py::
  test_process_document_indexes_registered_doc` leaves its document in `INGESTING` and
  `::test_process_document_reprocesses_failed_doc` leaves it `FAILED`, where both assert `READY`.
  Reproduced identically at base commit `65aa8ac`, so they predate this task and are recorded
  rather than fixed here. Likely an unmet ingestion dependency in the local stack (the CI wiring
  job brings up only `postgres redis vault mailhog`, and these passed there historically) —
  worth confirming against CI before assuming it is environmental.

- **FU-15 (code review, low)** — `WorkflowEditorView.test.ts` and
  `SubagentSpawnConfigForm.test.ts` call `i18n.global.mergeLocaleMessage` in `beforeAll` against
  the app-wide singleton from `@shared/i18n` with no teardown, so a test that asserts on an
  *unresolved* key becomes order-dependent. Latent only — Vitest isolates per file — and it is
  the established convention in this repo (`prompt-studio/__tests__/kit.ts:13`,
  `skills/__tests__/kit.ts:12` do the same). Changing only these two files would make them
  inconsistent with the pattern, so the fix belongs to a sweep across all four.

**Local-stack note for whoever re-runs this.** `compose.test.yml` sets `POSTGRES_DB: smap_test`,
which only takes effect on **first** volume init; a developer with an existing `smap` volume must
`CREATE DATABASE smap_test` rather than `down -v`, which would destroy their dev data. Migrations
must also be applied through the **bind-mounted** source (`--volume <repo>/backend:/app`), not the
baked image, or the schema stops at whatever migration the cached image shipped — that surfaced
here as `column agents.wakeup_last_refreshed_at does not exist` across 41 wiring tests.
</content>
