---
type: bugfix
status: implemented
created: 2026-07-22
requirements: [R14.08]
depends_on: []
---

# Workflow run cancellation does not reach sibling parallel branches

`depends_on: []` is a positive claim, not an omission. This dossier changes
`run_engine.py` only in the node-execution and run-termination paths
(`:216-229`, `:276-317`, `:402-482`, `:532-560`, `:659-692`, `:813-843`) plus
`domain/models.py:177-192`. The two neighbouring a2a-audit dossiers touch
disjoint ranges of the same files — `2026-07-22-workflow-dispatch-reliability`
owns `run_engine.py:484-530` (`dispatch_enqueues`, F-33) and the worker task
modules; `2026-07-22-join-epoch-loop-reentry` owns
`executors/join.py:51-61,79-89,125-133` (F-11). Neither produces a behavioural
precondition for this fix. §6 records the one textual adjacency that will require
a merge-order decision rather than a dependency.

## 1. Summary

When one branch of a `parallel` fan-out fails under the default
`on_error.strategy = fail`, the run row is marked FAILED but no signal reaches
the sibling branches, which execute in separate Arq worker processes. A sibling
that has already passed its job-entry liveness check keeps walking its entire
remaining branch — inserting step rows, invoking agents, and issuing instructs —
against a run that is already terminal. On a BYO-key product this is a direct
spend defect: the provider calls those zombie branches make are billed to the
user's own API key, for work whose result can never be consumed, and the exposure
is the whole remainder of the branch rather than a single in-flight call. The
same gap applies to user-initiated cancellation (`cancel_run`), to watchdog
force-fail, and to a run that finishes successfully while siblings are still
running.

## 2. Observed vs Expected

**Observed.** `_execute_node` guards only on the in-process flag `ctx.cancelled`
(`backend/contexts/workflow/application/run_engine.py:546`). `_fail_run` sets
that flag on its own `RunContext` (`run_engine.py:815`), updates the run row,
calls `cancel_pending_for_run` (`run_engine.py:822`, a step-row UPDATE only —
`backend/contexts/workflow/infrastructure/repositories.py:432-443`), and
publishes `workflow.run_finished` (`run_engine.py:835-843`). No cross-process
signal is emitted. A sibling branch runs in its own Arq job with its own
`RunContext` built by `_prepare_continuation` (`run_engine.py:231-274`), whose
`cancelled` field defaults to `False` (`domain/models.py:188`). The run-state
check happens exactly once, at `run_engine.py:241-243`. After that the branch
recurses through `_advance_from` → `_execute_node`
(`run_engine.py:719-720`, `:695`) with no further liveness check, executing each
node at `run_engine.py:594` and writing a step row at `run_engine.py:572`.

`RunContext.active_branches` is declared (`domain/models.py:187`) and assigned
once (`run_engine.py:725`); a repo-wide search finds no reader outside the test
that asserts the assignment (`backend/tests/unit/test_workflow_run_engine.py:338-353`).
It is a vestigial field, not a working mechanism.

**Expected.** `docs/workflow.schema.md:162` states normatively: "`fail`: mark run
failed, cancel all sibling branches (`parallel` branches honor this by emitting
cancellation events)." `[R14.08]` defines `cancelled` as a real run state whose
meaning is that the run has stopped. A branch must not begin executing a node
belonging to a run that has already reached SUCCEEDED, FAILED, or CANCELLED.

The intent source describes a mechanism ("emitting cancellation events") that
does not exist and that this fix deliberately does not build; §11 corrects the
document rather than the code.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What does "cancel" mean for a branch that is mid-provider-call? | The provider request already in progress runs to completion and its cost is incurred. For a multi-round tool turn, its remaining rounds are then cancelled at the existing turn-engine cancellation boundary; otherwise the branch stops at its next node boundary. | A blocked coroutine cannot interrupt the active provider stream. The existing `cancel_check` is observed only between tool rounds and before final synthesis, which sets the honest ceiling on this improvement. |
| Q-2 | Can the in-flight *turn* be aborted from outside, using the existing `cancel_check` hook? | Yes, for remaining rounds of multi-round, tool-using turns. This task adds the run-to-call correlation mapping and signals it from every run terminator. | The hook is already wired for workflow-originated turns. A call already inside the provider stream, and a single-round call with no subsequent cancellation point, still completes; the mapping limits the newly reachable work to the cancellation mechanism this task fixes. |
| Q-3 | Should a branch reaching an `end` node with `status: success` also stop its still-running siblings? | Yes. The guard is on "run is terminal", not "run failed". | `_execute_node`'s END block sets SUCCEEDED and returns (`run_engine.py:659-687`) while siblings continue. That is the same defect with a cheerier run state and identical spend consequences: the run is over, the money is still going out. |
| Q-4 | Detect terminality by re-reading the run row, or by a Redis kill-switch key? | Re-read the run row. | The run row is the single source of truth for `[R14.08]`; a Redis flag is a second one that can be lost. Cost is one indexed primary-key SELECT per node against an engine that already issues `insert_step` (`:572`), `update_step` (`:613`), and `update_variables` (`:627`) per node — a fourth round trip is not a meaningful regression. |
| Q-5 | Does the re-read actually observe another worker's commit inside an already-open transaction? | Yes. | No `isolation_level` is configured on the engine (`shared_kernel/db/session.py:40-53`), so Postgres default READ COMMITTED applies and each statement takes a fresh snapshot. The repository reads via a Core `select` returning a row tuple (`infrastructure/repositories.py:227-233`), not an identity-mapped ORM entity, so there is no session-level cache to serve a stale value. This is a load-bearing precondition of the whole fix. |
| Q-6 | Should the guard also cover `retry_node` and `resume_at_port`? | `retry_node` inherits it automatically; `resume_at_port` needs no change. | `retry_node` re-checks state at `run_engine.py:284-285` and then calls `_execute_node` (`:317`), which will carry the new guard. `resume_at_port` refuses a non-WAITING run before advancing (`:319-400`) and its `_advance_from` at `:399` reaches the guarded `_execute_node`. |
| Q-7 | Remove the dead `active_branches` field, or make it work? | Remove it (`domain/models.py:187`, assignment at `run_engine.py:725`, and the test at `test_workflow_run_engine.py:338-353`). | A per-process integer cannot coordinate cross-process branches; it can only ever be misleading. The correct mechanism is the run row's own state. Leaving a field that looks like branch accounting invites a future author to build on it. |
| Q-8 | Should a branch stopped by the guard write a `cancelled` step row for the node it declined to run? | No. Structured log line only, no step row and no new audit action. | `cancel_pending_for_run` (`repositories.py:432-443`) has already sealed every row that represents work actually started. A row per node never reached would inflate the trace surfaced by `[R14.10]` with records of things that did not happen. Audit action names are a stable public surface and should not grow for an internal early return. |
| Q-9 | Should terminating a run proactively delete the resume claim keys (`wf:wait:*`, `wf:approval:*`, `wf:instruct:*`) held by parked siblings? | No — out of scope, recorded as FU-2. | The keys are self-healing: every resume task checks terminality before claiming (`app/workers/tasks/workflow_common.py:34-40`) and `resume_at_port` refuses a terminal run. The keys expire on their own TTLs (e.g. `executors/instruct.py:64-68`). This is untidiness, not spend or correctness. |

**Q-2 in detail — what is and is not reachable.** The turn engine takes an
optional `cancel_check` (`backend/contexts/agents/application/runtime/turn_engine.py:2647`)
and consults it at exactly two points: the top of each tool round
(`:2652-2653`) and immediately before the final tool-free synthesis turn
(`:2704-2705`). It is *already wired* for workflow-originated turns: a workflow
`agent_invocation` calls `facade.a2a_call` (`executors/agent_invocation.py:41-47`),
and the A2A handler binds `cancel_check` to
`a2a_rendezvous.is_call_cancelled(correlation_id)` for every CALL envelope
(`contexts/orchestration/application/a2a_handler.py:177-188`). Today the only
caller of `mark_call_cancelled` is the caller's own timeout path
(`a2a_service.py:174-175`).

The obstruction is that `correlation_id` is generated locally inside
`a2a_service.call` (`:144`) and recorded in Redis only under keys derived *from*
that id (`a2a_rendezvous.py:35-44`). Nothing maps a `run_id` to its outstanding
correlation ids, so `_fail_run` has no way to name the calls it would cancel. A
fix would add a per-run set of live correlation ids, written next to
`register_expected_responder` (`a2a_service.py:161`) and drained on reply or
timeout.

Even with that plumbing, the honest ceiling is: **a turn already inside
`self._router.call_stream` (`turn_engine.py:2673`) cannot be stopped at all** —
there is no cancellation point inside the streaming loop, and a single-round
turn with no tool calls returns at `:2686` without ever revisiting `:2652`. The
gain is bounded to multi-round tool-using turns, which would drop their
*remaining* rounds. Provider spend already committed for the round in flight is
unrecoverable in every case.

**Decision:** include Q-2 in this task. The node-boundary guard removes the
unbounded remainder-of-branch exposure, while the run-to-call mapping also drops
remaining rounds of a multi-round tool turn. The provider request already in
progress remains uninterruptible.

## 4. Reproduction

Deterministic; no timing race required.

Preconditions: one project, one workflow, two agents bound to a key group with a
live provider key.

1. Author a workflow: `trigger` → `parallel` → two branches.
   - Branch A: a single `agent_invocation` node configured to fail — point it at
     a deleted `agent_id` so `facade.a2a_call` raises and the executor returns
     the FAILED outcome (`executors/agent_invocation.py:69-75`). Leave
     `on_error.strategy` at its default `fail`.
   - Branch B: a chain of three `agent_invocation` nodes, B1 → B2 → B3, each
     with a slow prompt, ending at an `end` node.
2. Start the run manually.
3. Observe: `_advance_from` fans out both branches as independent
   `run_workflow_step` jobs (`run_engine.py:726-731`, dispatched by
   `dispatch_enqueues` at `:484-530`, executed by
   `app/workers/tasks/workflow_steps.py:17-46`).
4. Branch A fails fast; `_fail_run` marks the run FAILED
   (`run_engine.py:813-843`). Branch B is by then inside B1's provider call.
5. **Result:** the run row reads `failed`, `ended_at` is set, and the UI shows
   the run as finished — while B2 and B3 still execute and bill the user's key.
   Their step rows are inserted *after* the run's `ended_at`, which is the
   cheapest query-level signature of the defect:

   `SELECT s.node_id, s.started_at, r.ended_at FROM workflow_steps s JOIN workflow_runs r ON r.id = s.run_id WHERE s.run_id = :run_id AND s.started_at > r.ended_at;`

A second, subtler observation in the same run: `cancel_pending_for_run`
(`repositories.py:432-443`) marks B1's in-flight row `cancelled`, and B1's own
`update_step` (`run_engine.py:613`) then overwrites it back to `succeeded`. The
final value is the truthful one — B1 really did complete — so this is a symptom
of the missing coordination, not an independent defect.

The narrower case where the run is already terminal *before* the sibling job is
dequeued is already handled: `_prepare_continuation` refuses it at
`run_engine.py:241-243`. That existing check is why the defect looks intermittent
in the field; it closes the window only for branches that have not yet started.

## 5. Root Cause Analysis

1. A parallel fan-out enqueues one independent Arq job per branch
   (`run_engine.py:726-731`), each of which opens its own DB session and its own
   `RunContext` (`workflow_steps.py:33-41` → `run_engine.py:216-218`). There is
   no shared in-process state between branches, by design.
2. The only cross-branch coordination the design provides is the run row's
   `state` column, read once per job at `run_engine.py:241-243`.
3. Once past that check, a branch executes its nodes by direct recursion:
   `_execute_node` → `_advance_from` → `_execute_node` for the single-successor
   case (`run_engine.py:695`, `:719-720`). A whole linear branch therefore runs
   inside one job, with no return to the job boundary where the state check
   lives.
4. The only guard on that recursive path is `if ctx.cancelled` at
   `run_engine.py:546`, and `ctx.cancelled` is set only by `_fail_run` on
   *its own* context object (`run_engine.py:815`) — an in-process boolean on a
   `RunContext` that no other worker holds a reference to
   (`domain/models.py:188`).
5. Consequently a sibling's `ctx.cancelled` is permanently `False`, and every
   subsequent node executes: step row inserted (`:572`), executor invoked
   (`:594`), provider call billed.

**Root cause:** the engine's liveness check is scoped to the *job* rather than
to the *node*. `_execute_node` (`run_engine.py:532-560`) — the single funnel
through which every node in every branch passes — never re-reads the
authoritative run state, and relies instead on a process-local flag that cannot
propagate across the process boundary the parallel design deliberately creates.
Correcting that one link prevents every downstream symptom.

**Aggravating factors, not causes:**

- The run terminators do not all agree on what "terminate" involves. `_fail_run`
  (`:822`), `force_fail` (`:415`), and `cancel_run` (`:447`) call
  `cancel_pending_for_run`; the END-node block (`:659-687`), the W8
  workflow-deleted path (`:255`), and `_mark_run_failed_isolated` (`:464-482`)
  do not. This inconsistency widens the blast radius but is not why siblings
  keep running — even the terminators that do call it emit no cross-process
  signal.
- `active_branches` (`domain/models.py:187`, `run_engine.py:725`) gives the
  false impression that branch accounting exists.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- *Spend.* Uncontrolled and unbounded by anything except branch length. Every
  `agent_invocation` (`executors/agent_invocation.py:41-47`) and every
  `instruct` (`executors/instruct.py:39-43`) remaining on a doomed branch spends
  the user's provider budget. Because SMAP is BYO-key, this is not platform cost
  absorbed by an operator — it is a charge on the user's own account for output
  no one will read, with no billing surface where SMAP could detect or refund
  it. Severity is **major** on functional grounds and effectively higher in user
  impact than the classification suggests.
- *Side effects.* Zombie branches issue real instructions to real agents
  (`executors/instruct.py:39-43`) and can write agent-visible state. The effect
  outlives the run.
- *Data.* Step rows are inserted against terminal runs (`run_engine.py:572`),
  with `started_at` later than the run's `ended_at`. Any analytics or retention
  logic assuming a run's steps precede its end is reading inconsistent data.
- *Tenancy.* No cross-tenant exposure. Everything stays within the run's own
  project; `_enforce_workflow_tenant` (`a2a_service.py:419-448`) still gates each
  call. This is a waste and correctness defect, not an isolation breach.

**Sibling suspects.**

| Site | Verdict | Evidence |
|---|---|---|
| `cancel_run` — user-initiated (`run_engine.py:433-460`) | **Confirmed — same defect** | Sets CANCELLED at `:442-446` and calls `cancel_pending_for_run` at `:447`, but emits no cross-process signal. Reached from the API at `app/api/v1/workflows.py:552-561` → `workflow_service.py:405-419` → `interfaces/facade.py:108-109`. A user pressing Cancel to stop the spend gets a UI that says "cancelled" while branches keep billing. This is the most user-visible instance and the fix must cover it. |
| `force_fail` — watchdog (`run_engine.py:402-431`) | **Confirmed — same defect** | Identical shape: `update_state` at `:414`, `cancel_pending_for_run` at `:415`, no signal. Its callers (`workflow_steps.py:94`, the watchdog task) fire precisely when a run has overrun its budget, so an unstopped branch here compounds the overrun the watchdog exists to bound. |
| END node reached by one branch (`run_engine.py:659-687`) | **Confirmed — same defect, plus a second gap** | Sets SUCCEEDED or FAILED and returns without calling `cancel_pending_for_run` at all, so sibling step rows are left `running` forever *and* the branches keep executing. Both halves are in scope. |
| `_mark_run_failed_isolated` (`run_engine.py:464-482`) | **Confirmed — narrower** | Writes FAILED on a fresh session (`:475-480`) and nothing else: no step sealing, no signal. Called from `run_step`'s crash path (`:228`). The node-boundary guard covers the sibling-stopping half; the missing `cancel_pending_for_run` is a separate one-line correction included here for consistency. |
| W8 workflow-deleted path (`run_engine.py:249-265`) | **Confirmed — narrowest** | Marks FAILED at `:255` with no step sealing. Same one-line correction. It cannot leak spend on its own branch (it returns `None` immediately) but leaves siblings running exactly like the others. |
| Loop-guard failure (`run_engine.py:554-559`) | **Cleared — already covered** | Routes through `_fail_run`, so it inherits the fix with no separate change. |
| Retry backoff path (`run_engine.py:750-786`, `workflow_steps.py:49-75`) | **Cleared** | `retry_node` re-checks run state at `:284-285` before doing anything, and its `_execute_node` call at `:317` will carry the new guard. A retry scheduled by a branch whose run has since died is refused. |
| Parked-branch resume tasks (`workflow_common.py:34-40`; `resume_at_port`, `run_engine.py:319-400`) | **Cleared with evidence** | `_run_is_terminal` explicitly tests `SUCCEEDED / FAILED / CANCELLED` before any resume is attempted, and `resume_at_port` refuses a non-WAITING run. A branch parked on `wait_for_event`, `approval_gate`, or `instruct` when the run dies is already correctly prevented from resuming. Only the stale Redis claim keys survive, harmlessly, until TTL (FU-2). |
| `dispatch_enqueues` (`run_engine.py:484-530`) | **Cleared here — owned elsewhere** | F-33 (a mid-loop failure dropping pending branches) lives in this function and belongs to `2026-07-22-workflow-dispatch-reliability`. It is an *enqueue*-side defect; this dossier changes only *execution*-side guards. No line overlap. Note the interaction for whoever merges second: after this fix, a branch job that F-33 drops and a branch job stopped by the guard become indistinguishable in the logs unless F-33's fix logs the drop distinctly. Flagged, not blocking. |
| `executors/join.py` (F-11, `:51-61,79-89,125-133`) | **Cleared — disjoint** | The join-epoch dossier changes latch and drain logic inside the executor. The only adjacency is textual: the `skip_edges` early return at `run_engine.py:656-657` sits immediately above the END block this dossier edits at `:659-687`. Give the reviewer the line ranges; the semantic surfaces do not overlap. |
| Chatroom-triggered agent turns (`contexts/conversation/application/triggers.py`) | **Cleared — different lifecycle** | Not run-scoped; there is no workflow run whose termination should stop them. Out of scope by construction. |

## 7. Fix Design

Six commits, each independently revertible.

**C1 — a terminal-state predicate in the domain layer.** Add
`RunState.is_terminal` (`domain/models.py:45-50`) returning true for SUCCEEDED,
FAILED, CANCELLED. `app/workers/tasks/workflow_common.py:34-40` currently
hardcodes that set; repoint it at the new predicate. SoC: the predicate belongs
in `domain/`, and `app/workers` may import downward from a context's domain, so
no boundary is crossed. Pure refactor, no behaviour change.

**C2 — the node-boundary guard (the actual fix).** At the top of `_execute_node`
(`run_engine.py:546`), replace the `ctx.cancelled` check with an authoritative
re-read:

- fetch the run via `self._runs.get(ctx.run_id)`;
- if the run is missing or `state.is_terminal`, set `ctx.cancelled = True` (so
  the rest of this in-process call chain short-circuits without further reads)
  and return, logging once with `run_id`, `node_id`, and the observed state;
- otherwise proceed unchanged.

Keep the `ctx.cancelled` fast path ahead of the read so a branch that has
already observed terminality does not re-query on each subsequent recursion.

**Why this corrects the root cause rather than masking it.** The root cause is
that the liveness check is scoped to the job while execution is scoped to the
node. This moves the check to the same granularity as the thing it must gate,
at the single funnel every node passes through — `_execute_node` is reached from
`run_step` (`:221`), `retry_node` (`:317`), `_advance_from` (`:720`), and the
fallback path (`:798`), so there is no second entrance to keep in sync. It does
not paper over the symptom by, say, filtering zombie step rows on read or
skipping the billing record; it prevents the provider call from being made.

**What it deliberately does not claim.** Per Q-1, a provider request already
inside `call_stream` is not interrupted. The guaranteed bound after this fix is
one in-flight provider request per sibling branch; multi-round turns do not start
their remaining rounds once the cancellation signal is observed.

**C3 — make every terminator seal its steps.** Add the missing
`cancel_pending_for_run` call to the END block (`run_engine.py:659-687`), the W8
path (`:249-265`), and `_mark_run_failed_isolated` (`:464-482`, on its own
isolated session). Brings all six terminators to the same contract:
*state → terminal, pending steps → cancelled*. C2 is what stops the branches;
C3 is what stops the trace from lying about them.

**C4 — delete `active_branches`.** Remove the field (`domain/models.py:187`),
the assignment (`run_engine.py:725`), and the test asserting it
(`test_workflow_run_engine.py:338-353`). Per Q-7.

**C5 — correct `docs/workflow.schema.md:162`.** See §11.

**C6 — cancel live workflow A2A calls.** Maintain a TTL-bound Redis set from a
workflow run id to its live call correlation ids. Every run terminator marks the
set cancelled and wakes callers; the existing turn-engine `cancel_check` then
stops a callee before its next tool round or final synthesis. A registration that
races with termination observes the run cancellation marker and is never sent.

**Data repair: none, deliberately.** Step rows written by zombie branches record
work that genuinely happened — agents really were invoked, tokens really were
spent, instructions really were delivered. Rewriting those rows to `cancelled`
would falsify the execution trace `[R14.10]` exposes and erase the only record
of spend the user incurred. The rows whose `started_at` exceeds their run's
`ended_at` are accurate history of a defective period and should stay that way.
No migration, no backfill, no cleanup job. The reproduction query in §4 is
offered as a read-only way to quantify past exposure, not as the basis for a
mutation.

## 8. Regression Test Plan

All cases go in `backend/tests/unit/test_workflow_run_engine.py`, following the
existing conventions there: `MagicMock` db, `AsyncMock` repositories,
`monkeypatch` on the `run_engine` module for `audit.emit` and `Publisher`
(pattern at `backend/tests/unit/test_workflow_k4.py:390-410`). No DB or Redis.

**RT-1 — `test_execute_node_refuses_a_terminal_run` (the failing test; write this first).**
Build a `RunContext` with a fresh `cancelled=False` and a definition containing
one `set_variable` node. Stub `engine._runs.get` to return a run with
`state=RunState.FAILED`. Stub `engine._recorder` and call
`await engine._execute_node(ctx, "n1")`. Assert
`engine._recorder.insert_step.assert_not_awaited()` and that the registered
executor was never invoked (patch `get_executor` and assert the returned mock
was not awaited).
*Why it fails today:* `run_engine.py:546` tests only `ctx.cancelled`, which is
`False` on a sibling's context. Control falls straight through to `insert_step`
at `:572` and the executor at `:594`, so `insert_step` **is** awaited and the
assertion fails. This is the exact production symptom in one assertion.

**RT-2 — `test_branch_stops_at_the_node_after_a_sibling_failure`.**
Definition: `a --e1--> b`, both `set_variable`. Stub `engine._runs.get` with
`side_effect=[running_run, failed_run]` so the run is live when `a` starts and
terminal when `b` is reached. Execute `a`. Assert exactly one `insert_step`
await, and that its `node_id` kwarg is `"a"`.
*Why it fails today:* `_advance_from` recurses into `_execute_node(b)` at
`:719-720` with no state consultation, producing two `insert_step` awaits. This
is the mid-branch continuation the audit identified as the wide window.

**RT-3 — `test_cancel_run_is_observed_by_a_running_branch`.**
Same shape as RT-1 but with `state=RunState.CANCELLED`.
*Why it fails today:* identical to RT-1 — CANCELLED is not represented anywhere
in the in-process `ctx.cancelled` flag, so a user pressing Cancel changes
nothing for a branch already executing. Separated from RT-1 because it pins the
user-facing sibling from §6, which must not regress independently.

**RT-4 — `test_end_node_cancels_pending_sibling_steps`.**
Definition with one `end` node, `config={"status": "success"}`. Stub
`engine._runs.get` to return a RUNNING run so the guard passes, `engine._steps`
as an `AsyncMock`, and `audit.emit` / `Publisher` per the existing pattern.
Execute the END node and assert
`engine._steps.cancel_pending_for_run.assert_awaited_once_with(ctx.run_id)`.
*Why it fails today:* the END block at `run_engine.py:659-687` calls
`update_state`, the metric, `audit.emit`, and `Publisher` — and never touches
`self._steps`. Zero awaits, assertion fails.

**RT-5 — `test_mark_run_failed_isolated_cancels_pending_steps`.**
Patch `shared_kernel.db.session.async_session` with an async context manager
yielding a mock session; patch `WorkflowRunRepository` and `WorkflowStepRepository`
as constructed inside the function. Call `_mark_run_failed_isolated` and assert
both `update_state` and `cancel_pending_for_run` were awaited.
*Why it fails today:* `run_engine.py:474-480` calls only `update_state`.

**RT-6 — `test_execute_node_proceeds_while_the_run_is_live` (guard-does-not-overreach).**
Stub `engine._runs.get` to return a RUNNING run; assert the executor runs and
`insert_step` is awaited exactly once.
*Why it is needed:* RT-1 through RT-3 are all satisfiable by a guard that
refuses everything. RT-6 is what makes the suite meaningful. It passes today and
must still pass after — it is the regression fence around the fix itself, not a
demonstration of the bug.

**RT-7 — `test_terminal_guard_reads_the_run_once_per_branch`.**
Definition `a → b → c`, run RUNNING throughout. Assert
`engine._runs.get.await_count == 3` — one read per node, no accidental
per-recursion amplification, and no caching that would defeat Q-5's freshness
requirement.
*Why it is needed:* Q-4 accepted a per-node DB read on an explicit cost
argument. This test pins the cost at the level that argument assumed.

The audit's coverage note records that no existing test touches this area, so
none of the above can be satisfied by an existing case, and none of the existing
`test_workflow_run_engine.py` cases should change except the `active_branches`
test deleted by C4.

## 9. Risks and Rollback

**R-1 — the guard stops a branch that should have continued.** The failure mode
is a false positive on terminality. Mitigated by reading the authoritative row
rather than a cache or a flag, by RT-6, and by the fact that `WAITING` and
`RUNNING` are both non-terminal — a run parked by *another* branch stays
executable, which is exactly the one-wait-per-run-at-a-time behaviour documented
at `run_engine.py:636-646`. Getting this wrong would deadlock every parallel
workflow, so it is loud rather than silent.

**R-2 — added DB load.** One primary-key SELECT per node. Bounded by RT-7 and
argued in Q-4. If it ever matters, the mitigation is a short-TTL Redis
kill-switch in front of the read, not removal of the read.

**R-3 — the guard runs inside the branch's open transaction.** Depends entirely
on READ COMMITTED (Q-5). If a future change sets REPEATABLE READ on the worker
sessionmaker (`shared_kernel/db/session.py:54`), the guard silently stops seeing
sibling commits and this defect returns with no test failure. Mitigation: a
comment at the guard stating the isolation dependency, per the project rule that
comments record constraints the code cannot show.

**R-4 — merge interaction with the two neighbouring dossiers.** No line overlap
(§6), but three dossiers editing `run_engine.py` in the same window will
conflict textually if merged carelessly. Mitigation: merge order by dossier, and
the reviewer note in §6 about F-33's drop logging.

**Rollback.** Six independent commits. Reverting C2 alone restores the current
behaviour exactly; C1, C3, C4, C5, and C6 are independently revertible. No
migration, no persisted state, no feature flag needed.

## 10. Acceptance Criteria

- [ ] AC-1: RT-1 fails against current `main` and passes after C2.
- [ ] AC-2: RT-2 through RT-5 each fail before their corresponding commit and pass after.
- [ ] AC-3: RT-6 and RT-7 pass, demonstrating the guard neither over-refuses nor amplifies reads.
- [ ] AC-4: After a branch fails under `strategy=fail`, no sibling branch begins executing a node that had not already started. The reproduction query in §4 returns zero rows for a run created after the fix.
- [ ] AC-5: The same holds for user-initiated `cancel_run`, for watchdog `force_fail`, and for a run terminated by an `end` node on another branch.
- [ ] AC-6: Every one of the six run terminators listed in §6 leaves the run's step rows sealed — no row remains `pending` or `running` under a terminal run.
- [ ] AC-7: `active_branches` no longer appears anywhere in `backend/`.
- [ ] AC-8: `docs/workflow.schema.md:162` describes the mechanism that exists.
- [ ] AC-9: The dossier states, and the implementation does not contradict, that a provider request already in flight is not interrupted; only remaining rounds of a multi-round tool turn are cancellable.
- [ ] AC-10: Q-2 is answered by the user before `/build` starts and its complete run-to-call cancellation path is covered by regression tests.
- [ ] AC-11: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`.

## 11. SRS Delta

The numbered requirements need no change. `[R14.08]` already defines `cancelled`
as a run state; this fix makes the state mean what it says.

One **documentation correction is required**, because the intent source
describes a mechanism that does not exist and will still not exist after this
fix. `docs/workflow.schema.md:162` currently reads:

> `fail`: mark run failed, cancel all sibling branches (`parallel` branches honor this by emitting cancellation events).

There is no cancellation event and none is being added — cross-branch
coordination happens through the run row's state, checked at every node
boundary. C5 replaces the line with a description of the real contract,
including its honest limit:

> `fail`: mark the run failed. Sibling `parallel` branches observe the terminal
> run state at their next node boundary and stop; a branch already inside an
> agent turn completes that turn first, so cancellation is bounded by one
> in-flight invocation per branch, not immediate.

Also noted for the record: the a2a audit cites `[R14.01]` as F-10's intent
source. `[R14.01]` (`REQUIREMENTS.md:710`) is about the hybrid DAG/FSM engine
shape and says nothing about cancellation. The load-bearing intent source is
`docs/workflow.schema.md:162`, with `[R14.08]` supporting. This dossier's
frontmatter reflects that rather than propagating the mis-citation. No
correction to the audit file is proposed — audit findings are not renumbered or
rewritten after review.

## 12. Deviation Log

Appended by `/build`.

- **D-1 (2026-07-22):** After adversarial lifecycle, concurrency, error-path,
  and client-trace verification, the user approved repairing all five confirmed
  findings in this dossier. The implementation additionally makes step creation
  conditional on a live run, makes terminal state transitions a single-winner
  operation, refuses losing resume/retry claims, retries failed A2A cancellation
  through Arq with exponential backoff plus a durable marker in the existing run
  context (re-dispatched by the watchdog), and refreshes step traces on
  run-terminal socket events. These changes extend C2/C6 to close their audited
  race and delivery gaps; no data migration is required.

## 13. Follow-ups

- **FU-2 — proactively drop resume claim keys on run termination.** Delete
  `wf:wait:*`, `wf:approval:*`, `wf:instruct:*` for a terminated run instead of
  waiting out their TTLs (`executors/instruct.py:64-68`). Cleared as harmless in
  §6 (`workflow_common.py:34-40` already refuses terminal resumes); tidiness
  only.
- **FU-4 — a spend ceiling per run.** The node guard bounds *post-termination*
  spend. Nothing bounds a healthy run's total provider spend. On a BYO-key
  product a per-run or per-workflow token budget is the structural answer to the
  class of problem this finding is one instance of. Feature-sized; needs its own
  dossier and a user decision on enforcement semantics.
- **FU-5 — reviewer note for whoever merges second.** After this fix, a branch
  job dropped by F-33 (`run_engine.py:484-530`) and a branch job stopped by the
  new guard look identical in the logs. Whichever dossier merges second should
  confirm the two are distinguishable in structured log output.
</content>
