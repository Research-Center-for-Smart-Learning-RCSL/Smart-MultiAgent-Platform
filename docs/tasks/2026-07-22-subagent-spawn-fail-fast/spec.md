---
type: bugfix
status: draft
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

- [ ] AC-1: `test_spawn_fails_fast_on_failure_port` (§8) fails against current code and passes
      after the fix.
- [ ] AC-2: executing a `subagent_spawn` node creates **no** `agent_instances` row and writes
      **no** `wf:subagent_callback` key.
- [ ] AC-3: both `wait_for_all: true` and `wait_for_all: false` take the `failure` port; neither
      returns `success`, and `output_variable` is never populated.
- [ ] AC-4: the node's error string names the capability as not implemented and points at the
      feature dossier, so the failure is self-diagnosing.
- [ ] AC-5: a run whose node carries `on_error.strategy: continue` proceeds past the node with
      `output_variable` unset — verified deliberately, since it is R1's behaviour change.
- [ ] AC-6: the executor's default `timeout_seconds` equals the schema's declared default and is
      within the schema's maximum.
- [ ] AC-7: the workflow editor marks the node unavailable, in both locales, with no hardcoded
      strings.
- [ ] AC-8: `validate_definition` emits a warning, not an error, for a definition containing the
      node — and saving such a definition still succeeds, including an edit that removes it.
- [ ] AC-9: `workflow_subagent_timeout` and `workflow_subagent_complete` remain registered
      worker tasks.
- [ ] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` pass in `frontend/`.

## 11. SRS Delta

None. `[R15.18]`–`[R15.23]` remain live and unamended — this dossier does not descope the
sub-agent feature, it makes its absence honest until the feature dossier delivers it. Amending
the SRS here would assert the platform had decided not to build G.8, which is not the decision
being made.

## 12. Deviation Log

Appended by /build.

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
  rather than read as a permanent subsystem.
</content>
