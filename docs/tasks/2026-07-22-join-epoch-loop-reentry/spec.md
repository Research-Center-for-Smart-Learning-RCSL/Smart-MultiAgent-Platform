---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R14.01, R14.02]
depends_on: []
---

# Join epoch does not advance on loop re-entry, so an `any`/`count` join fires once and stalls

## 1. Summary

A `join` node reached a second time by a loop back-edge never fires again. The one-shot
`fired` latch that makes an `any`/`count` join fire exactly once per fan-in is claimed at
`fire_threshold` arrivals but only released at `total_branches` arrivals
(`backend/contexts/workflow/application/executors/join.py:51-61`). For `any` those two
numbers differ by construction, so the latch outlives the fan-in it was guarding and the
next pass through the join is swallowed. The branch returns `skip_edges=True`
(`join.py:129-133`), the engine follows no edge (`run_engine.py:656-657`), and the run sits
in RUNNING with no pending work until `workflow_watchdog` force-fails it on
`idle_max_seconds` (`app/workers/tasks/workflow_watchdog.py:68-72`) with a reason that
describes idleness rather than the swallowed loop. Loops are a documented engine feature
(R14.01; `domain/models.py:203-206`; `run_engine.py:552-559`), so this is a silent stall in
a supported topology, presented to the user as a timeout.

## 2. Observed vs Expected

**Observed.** With `join(mode: any)` fed by an entry edge and a loop back-edge:

- Pass 1, entry edge arrives. `arrivals = 1 >= fire_threshold = 1` (`join.py:86-87,51`), so
  `SET fired NX` succeeds and `is_finalizer = 1` (`join.py:52-54`). The join fires
  downstream via `port="default"` (`join.py:136-140`).
- The drain block is skipped: `arrivals = 1 < total_branches = 2` (`join.py:56`), where
  `total_branches` counts every incoming edge (`join.py:79-81`). The `fired` key survives
  with its 86400 s TTL (`join.py:52`, `_JOIN_TTL_SECONDS` at `:67`), and the epoch key —
  written only at `join.py:59` and nowhere else in the repo — is never bumped.
- Pass 2, back-edge arrives. The epoch is unchanged, so the arrival lands in the *same*
  set (`join.py:42-45`). `SET fired NX` fails against the live key, `is_finalizer = 0`
  (`join.py:52-54`), and the executor returns `skip_edges=True` (`join.py:129-133`).
- `run_engine._execute_node` returns without calling `_advance_from` (`run_engine.py:656-657`).
  Nothing else is enqueued; the run stays RUNNING.
- `workflow_watchdog` eventually force-fails it: `latest_activity_at` stops advancing, so
  the idle check at `workflow_watchdog.py:68-72` fires with
  `"idle_max_seconds exceeded (...)"`.

**Expected.** The module's own contract states the intended invariant at `join.py:10-12`:
the `fired` latch suppresses re-firing *within one fan-in*, "while the epoch is only bumped
once the fan-in is fully drained (all incoming branches seen), keeping each loop pass
isolated." Each pass of a loop through a join must therefore start against clean arrival
state and fire once. R14.01 makes self-looping topologies normative ("each node is an FSM
sub-unit that can self-loop"), and R14.02 lists `join` as a first-class node type; the
loop guard at `domain/models.py:203-206` exists precisely because cycles are expected to
run many passes. The intent source is the executor contract plus R14.01, not a port table.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is the epoch's isolation unit a fan-in wave or a loop pass? | A loop pass, delimited by arrival on a back-edge. | `join.py:10-12` says "keeping each loop pass isolated". Arrival counts alone cannot separate a late fan-in straggler from a loop re-entry — both are a previously-unseen incoming edge. The only signal that distinguishes them is topological: a back-edge's source is reachable *from* the join, a fan-in edge's source is not. That is computable from `ctx.workflow_def["edges"]` with no extra state. |
| Q-2 | Should `total_branches` keep counting back-edges? | No — drain accounting counts fan-in edges only. | `join.py:79-81` counts every incoming edge. A back-edge cannot arrive before the join fires, so including it makes the drain condition at `:56` unsatisfiable for `any`/`count` and makes `fire_threshold` unsatisfiable for `all` (`:90-91`) — see the confirmed ALL-mode sibling in §6. |
| Q-3 | Is the `all`-mode deadlock in scope for this dossier? | Yes. | It is the same expression (`join.py:79-81`) and the same edit. Splitting it would leave the fix touching one arm of a two-arm bug and force a second pass over the same file. |
| Q-4 | Does the fix need a schema or linter change? | No. | Back-edge classification is derived from the definition at execution time. Rule 14 (`linter.py:563-626`) already accepts a two-edge join, and `docs/workflow.schema.md:144` requires exactly that. Nothing an author writes changes. |
| Q-5 | **OPEN — needs user.** The bug lives in Lua (`join.py:41-63`), which no current test tier executes: `TestJoinExecutor` mocks `redis.eval` (`tests/unit/test_workflow_executors.py:377-378`), `tests/integration/conftest.py` exposes no Redis fixture, and `fakeredis` is not a dependency (same constraint recorded at `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md`). Add a Redis-backed integration fixture, or accept unit coverage of the Python-side arguments only? | Proposed: do both — unit tests on the computed `eval` arguments (no Redis, fails today) plus one new integration test that runs the real script. | The argument computation carries the topology fix and is unit-testable today; the Lua carries the epoch fix and is not. Covering only the arguments would let a Lua regression through. Needs a decision because it adds an integration fixture the tier does not currently have. |
| Q-6 | **OPEN — needs user.** Residual case: a back-edge that arrives *before* the fan-in has drained (a loop faster than a straggler branch) closes the epoch early; the straggler then lands in the new epoch and can re-fire an `any` join. Accept as a documented limitation, or extend the design to seal per-epoch straggler sets? | Proposed: accept and document; file as a follow-up. | It requires a topology with both a multi-branch fan-in *and* a back-edge into the same join, plus a loop body faster than the slowest sibling branch. Sealing straggler sets roughly doubles the script's state. Recording the limit is honest; silently shipping it is not. |
| Q-7 | Relationship to F-36's dossier — `depends_on` or coordination note? | Coordination note, `depends_on: []`. | See the end of §6 for the line ranges. No semantic dependency exists in either direction; the risk is a textual conflict in two `StepOutcome` returns and a semantic coupling in what "the current fan-in is still open" means. |

## 4. Reproduction

Deterministic; no timing dependence.

Preconditions: a project with a workflow the caller may trigger, and a running Redis (the
join's state is entirely in Redis; there is no DB component to the stall).

1. Author a workflow: `trigger -> join1`, `join1 -> body`, `body -> join1`. Give `join1`
   `config: {"mode": "any"}`. Two incoming edges satisfies rule 14 (`linter.py:582-592`);
   the definition validates.
2. Trigger the run.
3. Pass 1: `join1` fires; `body` executes; the back-edge re-enters `join1`.
4. Observe: no further step rows for `body`. `workflow_runs.state` stays `running`.
   The debug line at `join.py:113-123` logs `finalizer=False` for the pass-2 arrival.
5. Inspect Redis: `wf:join:{run_id}:join1:0:fired` is present with a ~86400 s TTL;
   `wf:join:{run_id}:join1:epoch` does not exist.
6. After `idle_max_seconds` (default 1800, `domain/models.py:198-200`),
   `workflow_watchdog` force-fails the run with `"idle_max_seconds exceeded"`
   (`workflow_watchdog.py:71-75`).

Variant confirming the sibling in §6: the same topology with `config: {"mode": "all"}`
never fires even on pass 1 — `fire_threshold = total_branches = 2` (`join.py:81,90-91`)
and the second edge is the back-edge, which cannot arrive until the join has fired.

## 5. Root Cause Analysis

1. `total_branches` is computed as the count of *all* incoming edges, with no distinction
   between fan-in edges and loop back-edges — `join.py:79-81`.
2. The `fired` latch is claimed once `arrivals >= fire_threshold` — `join.py:51-55`. For
   `any`, `fire_threshold = 1` (`:86-87`); for `count`, `required_count` (`:88-89`).
3. The latch, the arrival set, and the epoch are released only once
   `arrivals >= total_branches` — `join.py:56-61`. Whenever `fire_threshold < total_branches`,
   claim and release are governed by different conditions.
4. In a loop topology, the arrival that would satisfy the release condition is the
   back-edge — which is also the first arrival of the *next* pass. It is evaluated against
   the previous pass's latch at `join.py:51-55` *before* the drain at `:56-61` runs, so it
   loses the latch it should have won and the epoch it opens is empty.
5. `is_finalizer = 0` yields `skip_edges=True` — `join.py:125-133`.
6. `_execute_node` returns without advancing — `run_engine.py:656-657`. No enqueue is
   appended, so `dispatch_enqueues` (`run_engine.py:497-500`) has nothing to dispatch and
   the run has no pending work.
7. The run row is still RUNNING; the watchdog's idle branch fires
   (`workflow_watchdog.py:68-75`).

**Root cause: link 1.** The executor has no representation of a loop pass. `total_branches`
conflates "how many branches this fan-in waits for" with "how many edges point here",
so the drain condition at `join.py:56` is keyed to a quantity that a loop topology can
never reach in a single pass. Correct that, and links 3-7 do not occur.

Link 3 (claim and release under different predicates) is an **aggravating factor**, not the
root cause: it is what turns the miscount into a permanent latch rather than a one-pass
delay. The 86400 s TTL at `join.py:67` is a second aggravator — it guarantees the latch
outlives any realistic run rather than self-clearing.

Not the root cause: the watchdog. `workflow_watchdog.py:64-72` reports accurately on the
state it can observe; the misleading failure reason is a symptom.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every workflow whose definition routes a cycle through a join:

- `any` / `count` joins in a loop stall after one pass (this finding).
- `all` joins in a loop never fire at all (sibling S-1, below) — strictly worse.
- Affected runs are force-failed by the watchdog after `idle_max_seconds` with a reason
  that names idleness, so the defect is invisible in run history and in the audit trail
  (`workflow_watchdog.py:72`).
- No cross-tenant or cross-project reach: every key is scoped by `run_id`
  (`join.py:42,45`).
- No incorrect data is persisted. Step rows written before the stall are accurate; the run
  is FAILED, which is true, for a stated reason that is not.

**Sibling suspects.** The class under test: per-run bookkeeping keyed by `(run, node)` with
no loop-pass dimension, such that a second pass observes first-pass state.

**S-1 — `total_branches` in `all` mode. CONFIRMED, in scope.**
`join.py:79-81` counts all incoming edges and `:90-91` sets `fire_threshold = total_branches`.
An `all` join with a back-edge therefore waits for an edge that cannot arrive until it has
already fired. Rule 14 catches only the condition-fed variant of this deadlock
(`linter.py:594-625`, which walks predecessors looking for a `condition` node and stops at
any node with more than one incoming edge, `:621-625`) — a back-edge feed is never
diagnosed. Fixed by the same edit as the primary defect (§7 C-1).

**S-2 — `wf:retry:{run_id}:{node_id}`. CONFIRMED, same shape, out of scope.**
`run_engine.py:756-760` increments a per-`(run, node)` retry counter with a 3600 s TTL, and
`app/workers/tasks/workflow_steps.py:69-72` documents that it is deliberately never deleted
on success ("deleting it would reset the counter to 0 and cause an infinite retry loop").
There is no pass dimension, so a node inside a loop that burns k retries on pass 1 enters
pass 2 with k already spent, and `retry_max` (`run_engine.py:762`) becomes a budget for the
whole run rather than per attempt-sequence. The same TTL cuts the other way on a long run:
after an hour the counter vanishes mid-run and the budget silently resets. This is the same
"no epoch" defect on a different key. Kept out of scope because the correct behavior of a
retry budget across loop passes is a product question, not a restoration of documented
behavior — see FU-1.

**S-3 — `RunContext.node_visit_counts`. CONFIRMED, inverse shape, out of scope.**
`domain/models.py:186` declares it and `run_engine.py:553-559` enforces
`loop_guard.max_visits_per_node` against it. But `RunContext` is rebuilt from scratch on
every entry point — `run_engine.py:178-185` (start), `:267-274` (`_prepare_continuation`,
used by `run_step`), `:308-315` (`retry_node`), `:356+` (`resume_at_port`) — and the field
is a plain in-memory `dict` with no Redis or DB backing (repo-wide grep for
`node_visit_counts` returns only `models.py:186` and `run_engine.py:553-557`). So the count
resets to zero on every Arq hop. Any loop whose body crosses a parallel fan-out, a retry
backoff, or a park/resume — which is most non-trivial loops — is never guarded at all. This
is the mirror image of the join bug (state that should persist across passes does not,
rather than state that should reset does not), it is not covered by any finding in the
audit, and fixing it requires choosing a durable store for the counter. See FU-2.

**S-4 — `RunContext.active_branches`. CLEARED here, owned by F-10.**
`models.py:187`, assigned once at `run_engine.py:725`, no reader anywhere in `backend/`
(the only other hit is the assertion at `tests/unit/test_workflow_run_engine.py:353`). Dead
rather than stale, and already the subject of F-10 → `docs/tasks/2026-07-22-workflow-run-cancellation/`.

**S-5 — `wf:wait:{run_id}:{node_id}`. CLEARED.**
Keyed by `(run, node)` with no pass dimension (`executors/wait_for_event.py:54-55`), but
the write is a plain `redis.set`, so a second pass overwrites the key with a freshly built
payload rather than reading pass-1 state; the consumer is a `GETDEL`
(`wait_for_event.py:8-9`), so a consumed claim leaves nothing behind. The index member
`f"{ctx.run_id}:{node.id}"` (`wait_for_event.py:79`) is likewise pass-less, but `SADD` makes
re-entry a no-op. The real defect on this key is the restore/re-index gap, which is F-37 —
a different shape.

**S-6 — `wf:approval:{approval.id}`. CLEARED.** `executors/approval_gate.py:87-88` keys on
the approval row id, which is created per execution, so each pass gets a distinct key.

**S-7 — `wf:instruct:{instruction.id}`. CLEARED.** `executors/instruct.py:64-65`, same
argument: keyed on a per-execution instruction id.

**S-8 — `wf:subagent_callback:{instance.id}`. CLEARED.** `executors/subagent_spawn.py:82-83`
keys on the spawned instance id, new per spawn. The known defect on this path is the
timeout's node scoping (F-29), which is unrelated to loop passes.

Summary: of the per-run Redis and context state in the workflow engine, exactly two keys
carry the "no pass dimension" defect — the join epoch (this dossier, plus S-1) and the retry
counter (S-2) — and one carries its inverse (S-3). The remaining four are keyed on
per-execution row ids or are write-overwrite, and are cleared.

**Coordination note — F-36 (`docs/tasks/2026-07-22-wait-for-event-timer-and-join-ports/`).**

- **This dossier's edit surface in `join.py`**: the module docstring (`:8-12`), the script
  header comment (`:33-40`), the Lua body (`:41-63`), the edge counting and thresholds
  (`:78-91`), and the `eval` argument list (`:100-109`).
- **That dossier's surface in `join.py`**: it explicitly decides **not** to implement the
  join timeout (its Q-5), and instead adds an advisory lint warning plus an editor notice.
  So it touches `linter.py` and the frontend, not `join.py`'s execution path.

**Verdict: no `depends_on`.** Two couplings remain and are handled by note:

1. *Textual.* Both dossiers may touch the `StepOutcome` returns at `:129-133` and
   `:136-140` — this one only if it surfaces the back-edge classification in the output dict.
   Resolvable in place.
2. *Semantic.* Any future implementation of the join timeout (that dossier's FU-1) must arm
   only while the current fan-in is genuinely open. Today "open" is defined by the same
   broken `total_branches` this dossier rewrites (`join.py:79-81`), and a join in a loop is
   *never* open by that definition. **Recommended ordering: this dossier lands first**, so
   any later timeout work is built against a correct definition of an open fan-in.

## 7. Fix Design

**C-1 — classify incoming edges by topology (`join.py:78-91`).**
Split the incoming edges of the join into fan-in edges and back-edges: an incoming edge is a
back-edge when its `from` node is reachable from `node.id` by following `edges` forward. A
plain forward reachability walk over `ctx.workflow_def["edges"]` decides this; the linter
already performs an equivalent bounded graph walk with a `visited` set at
`linter.py:602-604`, so the technique is established in this codebase. Then:

- `total_branches = max(len(fan_in_edges), 1)` — the drain condition at `join.py:56` becomes
  reachable in one pass, and `all` mode's `fire_threshold` (`:90-91`) becomes satisfiable.
  This is the single change that fixes both the primary defect and S-1.
- Pass a per-arrival `is_reentry` flag (whether `ctx.arrived_via` is a back-edge) as a new
  script argument at `join.py:100-109`.

**C-2 — close the epoch on re-entry (`join.py:41-63`).**
Before registering the arrival, if `is_reentry` is set, drain the current epoch: `DEL` the
set and the `fired` key, `INCR` the epoch, and recompute `set_key` / `fired_key` from the
new epoch. The arrival is then registered in the fresh epoch and evaluated against a latch
that belongs to it. The existing threshold and drain logic at `:51-61` is unchanged in
structure.

Under C-1 and C-2:

- `any` loop (entry + back-edge): pass 1 has `total_branches = 1`, so the entry arrival both
  fires and drains. The back-edge arrival opens epoch 1 and fires. The loop runs.
- `any` fan-in of three, no loop: `total_branches = 3`, `fire_threshold = 1`. Branch 1 fires
  and claims the latch; branches 2 and 3 are suppressed by the latch exactly as today; the
  third arrival drains. The one-shot guarantee that OBS-5 (`join.py:8-12`) was written for is
  preserved.
- `count(2 of 4)`: fires at 2, drains at 4. Unchanged.
- `all` loop: `total_branches = 1` (fan-in only), so it fires on the entry arrival instead of
  deadlocking (S-1).

**Why this corrects rather than masks.** The symptom could be suppressed by shortening
`_JOIN_TTL_SECONDS` (`join.py:67`), by clearing the latch whenever `is_finalizer` is false,
or by dropping `skip_edges` at `:129-133`. Each trades this stall for a worse one: a short
TTL makes a slow legitimate fan-in re-fire; clearing the latch on a non-finalizer arrival
destroys the one-shot guarantee for every `any` fan-in; dropping `skip_edges` makes every
straggler branch advance the workflow. All three preserve the root cause — an executor with
no notion of a loop pass — and merely move which topology breaks. C-1 gives the executor
that notion, derived from the definition it already holds, and C-2 spends it. After the fix
the invariant the module docstring claims at `join.py:10-12` is actually true.

**Data repair.** None required, and none possible. All affected state is Redis with a TTL
(`join.py:48,52,60`, bounded by `_JOIN_TTL_SECONDS` at `:67`), so stale latches from before
the deploy expire on their own within 24 hours; a deploy-time flush of `wf:join:*` would
shorten that but is not necessary and would disrupt legitimately in-flight fan-ins. Runs
already force-failed by the watchdog are terminal and were never partially committed to a
wrong state — their step rows are accurate, only the recorded failure reason is misleading.
They cannot be resumed and must be re-triggered by the user. No migration, no backfill.

## 8. Regression Test Plan

Failing tests first, in this order.

**T-1 (unit, no Redis) — `backend/tests/unit/test_workflow_executors.py`, `TestJoinExecutor`
(`:358-412`).** Extend the `_run_join` helper (`:359-381`) to accept an explicit edge list
rather than only the generated fan-in shape at `:370`, and to return the captured
`mock_redis.eval` call args. Note that every current case builds `incoming_edges` distinct
sources with no outgoing edges (`:370`), so no existing test exercises a cycle, and
`mock_redis.eval` is stubbed at `:377-378`, so **no existing test can fail on this bug** —
they assert only the Python branch on `lua_result`.

- `test_loop_back_edge_excluded_from_total_branches`: definition
  `join1 -> body`, `body -> join1`, `entry -> join1`, mode `any`, arriving via the entry
  edge. Assert the `total_branches` argument passed to `eval` is `"1"`.
  **Fails today**: `join.py:79-81` counts both incoming edges and passes `"2"`.
- `test_all_mode_in_loop_fires_on_fan_in_only`: same definition, mode `all`. Assert the
  `fire_threshold` argument is `"1"`. **Fails today**: `:90-91` derives it from the
  all-edges count and passes `"2"` — the S-1 deadlock.
- `test_reentry_flag_set_for_back_edge` / `..._clear_for_fan_in_edge`: same definition,
  arriving via the back-edge and via the entry edge respectively. Assert the re-entry
  argument is `"1"` and `"0"`. **Fails today**: `eval` is called with exactly six arguments
  (`join.py:100-109`) and no such argument exists, so the lookup raises `IndexError`.
- `test_pure_fan_in_unchanged`: the existing three-source shape at `:370`. Assert
  `total_branches == "3"` and the re-entry argument is `"0"`. **Passes after the fix** —
  this is the guard that C-1 does not disturb acyclic joins, and it must be written
  alongside the failing ones.

The four existing cases at `:383-412` must continue to pass unmodified; they pin the
`is_finalizer` → `skip_edges` / `port` mapping at `join.py:125-140`, which this fix does not
change.

**T-2 (integration, real Redis) — new
`backend/tests/integration/test_workflow_join_epoch.py`, marked `integration`.** Pending Q-5:
this tier has no Redis fixture today (`tests/integration/conftest.py` contains no Redis
setup; `fakeredis` is not a dependency), so this test requires adding one. It runs
`_JOIN_ARRIVE_LUA` (`join.py:41-63`) directly against Redis:

- `test_any_join_fires_on_every_loop_pass`: arrive on the entry edge
  (`is_reentry=0`, `total=1`), assert `is_finalizer == 1`; arrive on the back-edge
  (`is_reentry=1`), assert `is_finalizer == 1` again and that the epoch key incremented.
  **Fails today**: the second call returns `is_finalizer = 0` because the epoch-0 `fired`
  key is still live (`join.py:52`), which is the defect verbatim.
- `test_any_fan_in_fires_once_and_drains`: three arrivals on three distinct edges with
  `total=3`, `threshold=1`, `is_reentry=0`. Assert `is_finalizer` is `1, 0, 0`, and that
  after the third the set and `fired` keys are gone and the epoch is `1`.
  **Passes today and must keep passing** — the anti-regression guard on OBS-5.
- `test_retried_branch_does_not_inflate_arrivals`: the same edge id twice. Assert
  `arrivals` stays at 1. Guards the ASYNC-9 idempotence property (`join.py:47`, `:93-97`)
  against the C-2 rewrite.

**T-3 (unit) — `backend/tests/unit/test_workflow_run_engine.py`.** No change required; the
engine side (`run_engine.py:656-657`) is untouched by this fix. Listed so /build does not
add engine-level coverage for a defect that is entirely inside the executor.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| Back-edge classification misreads a topology and treats a genuine fan-in edge as a back-edge, causing an `any` join to re-fire per straggler | The walk is pure forward reachability over `edges`; T-1's `test_pure_fan_in_unchanged` and T-2's `test_any_fan_in_fires_once_and_drains` pin the acyclic behavior in both the argument computation and the script |
| The reachability walk is O(V+E) per join arrival on a large definition | Bounded by the definition size, which is already fully parsed on every node execution (`run_engine.py:562`, `:705-706`); no new I/O. Memoization is available if a profile shows it matters |
| C-2 changes a Lua script under concurrent branch arrivals | The script stays a single atomic `eval` (`join.py:100-109`); the re-entry drain is added inside the same indivisible unit, so no new interleaving is introduced |
| Deploy straddles the change: in-flight runs hold epoch state written by the old script | Key shapes are unchanged (`join.py:42,45-46`); the worst case is one already-stalled run behaving as it does today, and its keys expire within `_JOIN_TTL_SECONDS` (`:67`) |
| S-1's fix turns a previously-deadlocked `all`-in-loop workflow into one that actually runs, with real agent invocations on the user's key | Correct by intent (R14.01), but it is a behavior change for any definition that was silently dead. Release-note it |
| Q-6's residual (back-edge before drain) re-fires an `any` join | Documented limitation, FU-3; requires a fan-in and a back-edge into the same join plus a loop faster than the slowest sibling |

**Rollback.** Revert the single file `backend/contexts/workflow/application/executors/join.py`.
No migration, no schema change, no key-shape change, so a revert needs no cleanup; stale
epoch keys written by the fixed script are read correctly by the old script (it reads the
epoch and treats it as an opaque suffix, `join.py:42-45`).

## 10. Acceptance Criteria

- [ ] AC-1: every T-1 test listed as failing in §8 fails against current code and passes
      after the fix; `test_pure_fan_in_unchanged` passes both before and after.
- [ ] AC-2: `test_any_join_fires_on_every_loop_pass` (T-2) fails before and passes after,
      subject to the Q-5 decision on the Redis fixture.
- [ ] AC-3: `test_any_fan_in_fires_once_and_drains` and
      `test_retried_branch_does_not_inflate_arrivals` (T-2) pass both before and after — the
      OBS-5 one-shot guarantee and the ASYNC-9 dedupe are not regressed.
- [ ] AC-4: the four existing cases at `tests/unit/test_workflow_executors.py:383-412` pass
      unmodified.
- [ ] AC-5: an `all`-mode join with one fan-in edge and one back-edge fires on its fan-in
      arrival (S-1), asserted by `test_all_mode_in_loop_fires_on_fan_in_only`.
- [ ] AC-6: the §4 reproduction runs at least three loop passes and terminates through its
      own `end` node, with no `idle_max_seconds` force-fail
      (`workflow_watchdog.py:71-75`) and no reliance on `_JOIN_TTL_SECONDS` expiry.
- [ ] AC-7: the module docstring (`join.py:8-12`) and the script header (`:33-40`) describe
      the implemented rule, including that drain accounting counts fan-in edges only.
- [ ] AC-8: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
      `backend/`.
- [ ] AC-9: no change to `docs/workflow.schema.md`, `linter.py`, `run_engine.py`, or any
      migration — the fix is confined to `executors/join.py` plus tests. A diff touching
      anything else means the design in §7 was not followed.

## 11. SRS Delta

None. R14.01 already makes self-looping topologies normative and R14.02 already lists
`join`; the fix restores the behavior `join.py:10-12` claims. Two documentation notes belong
in the code, not the SRS: that drain accounting counts fan-in edges only (AC-7), and the
Q-6 residual (FU-3).

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 — `wf:retry` has no loop-pass dimension (S-2).** `run_engine.py:756-760` increments
  a per-`(run, node)` counter that `app/workers/tasks/workflow_steps.py:69-72` deliberately
  never clears, so retry budget is consumed once per run rather than once per attempt
  sequence, and its 3600 s TTL resets it silently mid-run on a long execution. Same defect
  class as this dossier. Needs a product decision on what `retry_max` means across loop
  passes before it can be specced.
- **FU-2 — `loop_guard` never fires across Arq hops (S-3).** `node_visit_counts`
  (`domain/models.py:186`) is in-memory on a `RunContext` rebuilt at `run_engine.py:178`,
  `:267`, `:308` and `:356`, so the guard at `:553-559` counts only within one synchronous
  recursion. Any loop crossing a fan-out, retry, or park/resume is unguarded. Not covered by
  any finding in the source audit. Requires choosing a durable store for the counter; note
  the interaction with this dossier — fixing the join makes more loops actually loop, which
  raises the value of a working guard.
- **FU-3 — early epoch close (Q-6).** A back-edge arriving before its fan-in has drained
  closes the epoch early and lets a straggler re-fire an `any` join. Requires per-epoch
  straggler sealing. Document the limitation in `join.py` as part of this fix (AC-7).
- **FU-4 — misleading watchdog failure reasons.** `workflow_watchdog.py:64-72` can only
  report `idle_max_seconds exceeded`, which reads as a timeout for what may be a stalled
  engine. A reason that names the last executed node and its outcome would have surfaced
  this defect years earlier. Cleared-but-fragile; worth hardening.
</content>
