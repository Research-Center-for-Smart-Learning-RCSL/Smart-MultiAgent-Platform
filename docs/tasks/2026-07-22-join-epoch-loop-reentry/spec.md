---
type: bugfix
status: approved
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
| Q-5 | The bug lives in Lua (`join.py:41-63`), which no current test tier executes: `TestJoinExecutor` mocks `redis.eval` (`tests/unit/test_workflow_executors.py:377-378`), `tests/integration/conftest.py` exposes no Redis fixture, and `fakeredis` is not a dependency (same constraint recorded at `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md`). Add a Redis-backed integration fixture, or accept unit coverage of the Python-side arguments only? | Both — unit tests on the computed `eval` arguments (no Redis, fails today) plus new integration tests that run the real script against a Redis fixture. | The argument computation carries the topology fix and is unit-testable today; the Lua carries the epoch fix and is not. Covering only the arguments would let a Lua regression through. Confirmed with the user 2026-07-25. |
| Q-6 | Residual case: a back-edge that arrives *before* the fan-in has drained (a loop faster than a straggler branch) closes the epoch early; the straggler then lands in the new epoch and can re-fire an `any` join. Accept as a documented limitation, or extend the design to seal per-epoch straggler sets? | Extend the design now: split arrival tracking into two independent Redis tracks, `fan` (fan-in edges, existing mode-derived threshold) and `pass` (back-edges, fixed `fire_threshold=1`), each with its own epoch counter (revised §7 C-2). A back-edge arrival never touches the fan track's keys, so a fan-in straggler is evaluated against the same still-open fan epoch it always would have been, no matter how many loop passes ran on the pass track meanwhile. | Confirmed with the user 2026-07-25. Sealing did not need *more* state than the drafted design — it needed the fan-in and loop-pass dimensions to stop sharing one counter. That also makes the originally-drafted drain-on-reentry step (which caused this very race by forcing the back-edge's arrival into the fan track) unnecessary; the corrected Lua body is a smaller diff than the draft, not a larger one. |
| Q-7 | Relationship to F-36's dossier — `depends_on` or coordination note? | Coordination note, `depends_on: []`. | See the end of §6 for the line ranges. No semantic dependency exists in either direction; the risk is a textual conflict in two `StepOutcome` returns and a semantic coupling in what "the current fan-in is still open" means. |
| Q-8 | Should a join fed by more than one back-edge (multiple loop bodies converging on the same join) require *all* of them to arrive before restarting a pass (rendezvous), or does any one restart it? | Any back-edge fires; the pass track's one-shot latch suppresses the rest for that pass — the same `any` semantics already used for fan-in-mode `ANY`, just applied to the pass dimension. | Confirmed with the user 2026-07-25. The join's `mode` config governs fan-in aggregation, not loop continuation. A rendezvous requirement would need new config surface and would stall the loop indefinitely if one loop body never completed a given pass; nothing in R14.01/R14.02 asks for that, and no topology in this dossier's scope needs it. |

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
`linter.py:602-604`, so the technique is established in this codebase. This is unchanged
from the earlier draft; what changes is what the classification feeds into (C-2 below).

- `total_fan_branches = max(len(fan_in_edges), 1)` — the fan-in drain condition becomes
  reachable in one pass, and `all` mode's `fire_threshold` becomes satisfiable. This is the
  single change that fixes S-1.
- `total_back_edges = max(len(back_edges), 1)` — new; used only by the pass track below, and
  only ever consulted on an arrival where `ctx.arrived_via` classifies as a back-edge.

**C-2 — split arrival tracking into two independent tracks, `fan` and `pass`
(`join.py:41-63`, `:100-109`).**

The earlier draft of C-2 closed the *shared* epoch whenever a back-edge arrived (`DEL` the
set and `fired` key, `INCR` the epoch, re-register the back-edge's own arrival in the fresh
epoch). Working through Q-6 showed why that races: it forces the back-edge's own arrival
into the *same* counter used for fan-in aggregation, so closing that epoch early discards
whatever fan-in state was still in flight — a straggler fan-in branch then lands in the
wrong epoch and can re-fire the join. The fix is not to add more state on top of that
design; it is to stop conflating two arrival populations that were never the same
dimension: "how many fan-in branches have shown up this wave" and "has the loop looped
back yet" are independent questions, and the original bug (root cause link 1) was already
exactly this conflation — the draft's C-2 reintroduced a narrower version of the same
mistake it was fixing.

Instead, keep the Lua script's existing one-shot-latch-and-drain body (`join.py:41-63`)
completely unchanged in structure, and parameterize its key names by a `track` argument
(`"fan"` or `"pass"`) so the two populations never share a counter:

- Key shape becomes `wf:join:{run_id}:{node_id}:{track}:epoch` for the epoch counter,
  `wf:join:{run_id}:{node_id}:{track}:{epoch}` for the arrival SET, and
  `...:{track}:{epoch}:fired` for the one-shot latch — each `track` gets its own
  independent epoch, SET, and latch. The Lua diff is one new ARGV segment (the track
  name) concatenated into the two key-building lines; no new conditional logic inside
  the script.
- Python decides, per arrival, which track and which `(fire_threshold, total_branches)`
  pair to pass, in place of the single computation at `join.py:83-91`:
  - Arrived via a fan-in edge: `track = "fan"`, `total_branches = total_fan_branches`,
    `fire_threshold` from the join's configured mode exactly as today (`any` → `1`,
    `count` → `required_count`, `all` → `total_fan_branches`).
  - Arrived via a back-edge: `track = "pass"`, `total_branches = total_back_edges`,
    `fire_threshold = 1` fixed — confirmed by Q-8: the first back-edge to arrive in a pass
    restarts the loop, and the pass track's own latch suppresses any others in the same
    pass, mirroring `any` fan-in semantics rather than the join's configured `mode` (mode
    governs fan-in aggregation, not loop continuation).
- `branch_id` (ASYNC-9's idempotent dedup by incoming-edge id) is unchanged and applies to
  whichever track's SET the arrival lands in, so a retried fan-in step and a retried
  back-edge step are each deduped within their own track.
- The `is_reentry` flag from the earlier draft is no longer needed as a Lua argument — the
  `track` string is the only new argument, and Python already knows which track applies
  before calling `eval`.

Under C-1 and the revised C-2:

- `any` loop (single entry + single back-edge): `total_fan_branches = 1`, so the entry
  arrival fires and drains the fan track (epoch 0 → 1). The back-edge arrival fires and
  drains the pass track independently (its own epoch 0 → 1). The loop runs; each pass drains
  its own pass epoch.
- `any` fan-in of three, no loop: only the `fan` track is ever touched (no back-edges to
  classify). Branch 1 fires and claims the fan latch; branches 2 and 3 are suppressed by the
  same latch exactly as today; the third arrival drains. The OBS-5 one-shot guarantee
  (`join.py:8-12`) is preserved, and the key shape for a plain acyclic join is a strict
  superset of today's (adds the `fan` segment) with identical arrival semantics.
- `count(2 of 4)`, no loop: unaffected, `fan` track only.
- `all` loop: `total_fan_branches = 1`, so it fires on the entry arrival instead of
  deadlocking (S-1); the pass track handles every subsequent loop pass independently.
- **Q-6's straggler race, closed:** two fan-in edges (A, B) plus one back-edge, mode `any`.
  A arrives (`fan` epoch 0): fires, does not drain (`1 < total_fan_branches=2`). The loop
  runs and loops back before B arrives: the back-edge arrival is on the `pass` track — it
  never touches the `fan` track's keys. B finally arrives: it lands in the *same*
  still-open `fan` epoch 0 as A, `SET NX` on `fan:0:fired` fails (already claimed by A), so
  `is_finalizer = 0` — B is correctly suppressed regardless of how many loop passes ran on
  the `pass` track in between — and the fan-in drains normally once B's arrival brings the
  count to `total_fan_branches`.
- **Q-8's multi-back-edge case:** two back-edges (loopA, loopB) into the same join,
  `total_back_edges = 2`. loopA arrives first: `pass` arrivals `1 >= fire_threshold(1)` →
  fires, does not yet drain (`1 < 2`). loopB arrives in the same pass: arrivals `2 >= 1` but
  `pass:0:fired` is already claimed → suppressed (`is_finalizer = 0`); drain condition
  `2 >= total_back_edges(2)` is now met, so the pass epoch advances for the next pass.

**Why this corrects rather than masks.** The symptom could be suppressed by shortening
`_JOIN_TTL_SECONDS` (`join.py:67`), by clearing the latch whenever `is_finalizer` is false,
or by dropping `skip_edges` at `:129-133`. Each trades this stall for a worse one: a short
TTL makes a slow legitimate fan-in re-fire; clearing the latch on a non-finalizer arrival
destroys the one-shot guarantee for every `any` fan-in; dropping `skip_edges` makes every
straggler branch advance the workflow. All three preserve the root cause — an executor with
no notion of a loop pass — and merely move which topology breaks. C-1 gives the executor
that notion, derived from the definition it already holds, and the revised C-2 spends it by
giving the fan-in wave and the loop pass their own independent counters instead of
overloading one counter for both. After the fix the invariant the module docstring claims
at `join.py:10-12` is actually true, including across the straggler interleaving Q-6
identified.

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

- `test_fan_in_edge_uses_fan_track_with_fan_only_total`: definition `join1 -> body`,
  `body -> join1`, `entry -> join1`, mode `any`, arriving via the entry edge. Assert the
  `track` argument is `"fan"` and `total_branches` is `"1"`. **Fails today**: `join.py:79-81`
  counts both incoming edges and passes `"2"`, and no `track` argument exists at all.
- `test_all_mode_fan_track_fire_threshold_matches_fan_total`: same definition, mode `all`.
  Assert `track == "fan"` and `fire_threshold == "1"`. **Fails today**: `:90-91` derives the
  threshold from the all-edges count and passes `"2"` — the S-1 deadlock.
- `test_back_edge_uses_pass_track_with_fixed_threshold_one`: same definition, arriving via
  the back-edge. Assert `track == "pass"`, `fire_threshold == "1"`, `total_branches == "1"`.
  **Fails today**: `eval` is called with exactly six positional arguments (`join.py:100-109`)
  and no `track` concept exists — the back-edge is computed identically to a fan-in arrival.
- `test_multiple_back_edges_pass_total_counts_only_back_edges`: definition with two
  back-edges (`loopA -> join1`, `loopB -> join1`) plus one entry edge, mode `any`, arriving
  via `loopA`. Assert `track == "pass"`, `total_branches == "2"`, `fire_threshold == "1"`
  (Q-8 — fixed at 1 regardless of how many back-edges exist). **Fails today**: same
  IndexError/no-track failure as the previous case, and even a naive fix that reused the
  join's configured `mode` for back-edges would fail this — the pass track's threshold is
  fixed, not mode-derived.
- `test_pure_fan_in_unchanged`: the existing three-source acyclic shape at `:370` (no
  back-edges present). Assert `track == "fan"`, `total_branches == "3"`, and
  `fire_threshold` per mode exactly as today. **Passes after the fix** — the guard that C-1
  and the revised C-2 do not disturb a join with no back-edges, written alongside the
  failing cases.

The four existing cases at `:383-412` must continue to pass unmodified; they pin the
`is_finalizer` → `skip_edges` / `port` mapping at `join.py:125-140`, which this fix does not
change.

**T-2 (integration, real Redis) — new
`backend/tests/integration/test_workflow_join_epoch.py`, marked `integration`.** Per Q-5:
this tier has no Redis fixture today (`tests/integration/conftest.py` contains no Redis
setup; `fakeredis` is not a dependency), so this task adds one. Tests run
`_JOIN_ARRIVE_LUA` (`join.py:41-63`) directly against Redis, passing `track` explicitly:

- `test_any_join_fires_on_every_loop_pass`: arrive on the entry edge (`track="fan"`,
  `total="1"`), assert `is_finalizer == 1`; arrive on the back-edge (`track="pass"`,
  `total="1"`), assert `is_finalizer == 1` again and that the `pass` epoch key incremented
  while the `fan` epoch key is untouched. Repeat the back-edge arrival for a third pass and
  assert it fires again. **Fails today**: there is no `track` dimension at all, and the
  second call returns `is_finalizer = 0` because the single shared epoch-0 `fired` key is
  still live — the defect verbatim.
- `test_any_fan_in_fires_once_and_drains`: three arrivals on three distinct fan-in edges
  (`track="fan"`, `total="3"`, `threshold="1"`). Assert `is_finalizer` is `1, 0, 0`, and that
  after the third the `fan` set and `fired` keys are gone and the `fan` epoch is `1`.
  **Passes today (under the pre-track key shape) and must keep passing** — the
  anti-regression guard on OBS-5.
- `test_retried_branch_does_not_inflate_arrivals`: the same fan-in edge id twice on the
  `fan` track. Assert `arrivals` stays at 1. Guards the ASYNC-9 idempotence property
  (`join.py:47`, `:93-97`) against the track-split rewrite.
- `test_straggler_fan_in_suppressed_after_early_loop_pass` — **direct regression test for
  Q-6.** Topology: two fan-in edges (A, B) plus one back-edge, mode `any`
  (`total_fan_branches=2`, `total_back_edges=1`). Sequence: A arrives on `fan` epoch 0 →
  `is_finalizer == 1`, `fan` epoch stays `0` (only 1 of 2 fan-in arrivals seen). The
  back-edge arrives on `pass` epoch 0 → `is_finalizer == 1`, `pass` epoch advances to `1`;
  assert the `fan` epoch and its `fired` key are **untouched** by this call. B (the
  straggler) then arrives on `fan` epoch 0 → assert `is_finalizer == 0` (correctly
  suppressed, not a second fire) and that the `fan` epoch now advances to `1` (drain
  completes on `total_fan_branches`). **Fails today**: with no track split, the back-edge
  arrival would collide with the single shared epoch counter and either re-arm a spurious
  fire or corrupt the drain count — this test would also fail against the originally
  drafted drain-on-reentry version of C-2, which is the version Q-6 was raised against.
- `test_multi_back_edge_any_fires_and_drains` — **regression test for Q-8.** Topology: one
  entry edge plus two back-edges (`loopA`, `loopB`), mode `any` (`total_back_edges=2`).
  loopA arrives → `is_finalizer == 1`, `pass` epoch stays at its current value (`1 < 2`
  arrivals). loopB arrives in the same pass → `is_finalizer == 0` (latch already claimed,
  correctly suppressed as a duplicate for this pass) and the `pass` epoch advances
  (`2 >= total_back_edges`). **Fails today**: no `track`/multi-back-edge concept exists.

**T-3 (unit) — `backend/tests/unit/test_workflow_run_engine.py`.** No change required; the
engine side (`run_engine.py:656-657`) is untouched by this fix. Listed so /build does not
add engine-level coverage for a defect that is entirely inside the executor.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| Back-edge classification misreads a topology and treats a genuine fan-in edge as a back-edge, causing an `any` join to re-fire per straggler | The walk is pure forward reachability over `edges`; T-1's `test_pure_fan_in_unchanged` and T-2's `test_any_fan_in_fires_once_and_drains` pin the acyclic behavior in both the argument computation and the script |
| The reachability walk is O(V+E) per join arrival on a large definition | Bounded by the definition size, which is already fully parsed on every node execution (`run_engine.py:562`, `:705-706`); no new I/O. Memoization is available if a profile shows it matters |
| The revised C-2 changes the Lua script's key names and adds a `track` argument under concurrent branch arrivals | The script stays a single atomic `eval` per call, and each track's key namespace is fully independent (`fan` calls never touch `pass` keys and vice versa), so no new cross-track interleaving is introduced; T-2's `test_straggler_fan_in_suppressed_after_early_loop_pass` asserts the two tracks stay isolated under concurrent-style interleaving |
| Deploy straddles the change: in-flight runs hold epoch state written by the old script | Key shapes change (old: `wf:join:{run}:{node}:epoch`/`:{epoch}`/`:{epoch}:fired`; new: same prefix plus a `fan`/`pass` track segment before `epoch`/`{epoch}`), so the new script does not read or reinterpret old-shape keys — it simply starts fresh under the new names. The worst case is one already-stalled run's old-shape keys sitting unread until they expire within `_JOIN_TTL_SECONDS` (`:67`); no wrong-shape read occurs in either direction |
| S-1's fix turns a previously-deadlocked `all`-in-loop workflow into one that actually runs, with real agent invocations on the user's key | Correct by intent (R14.01), but it is a behavior change for any definition that was silently dead. Release-note it |
| A join fed by more than one back-edge behaves under Q-8's "any back-edge fires" rule rather than a rendezvous of all loop bodies | Confirmed with the user 2026-07-25 as the intended semantics, not a residual gap; a future topology genuinely needing rendezvous is a product decision requiring new config surface, not a defect in this fix |

**Rollback.** Revert the single file `backend/contexts/workflow/application/executors/join.py`.
No migration, no schema change. The key shape changes (new `track` segment, split epoch
counters — see the deploy-straddle row above), but a revert needs no cleanup: the reverted
script goes back to reading only the old (pre-track) key names, ignoring whatever `fan`/
`pass`-prefixed keys the fixed script wrote; those simply expire within
`_JOIN_TTL_SECONDS` unread.

## 10. Acceptance Criteria

- [ ] AC-1: every T-1 test listed as failing in §8 fails against current code and passes
      after the fix; `test_pure_fan_in_unchanged` passes both before and after.
- [ ] AC-2: `test_any_join_fires_on_every_loop_pass` (T-2) fails before and passes after,
      per the Q-5 decision to add a Redis-backed integration fixture.
- [ ] AC-3: `test_any_fan_in_fires_once_and_drains` and
      `test_retried_branch_does_not_inflate_arrivals` (T-2) pass both before and after — the
      OBS-5 one-shot guarantee and the ASYNC-9 dedupe are not regressed.
- [ ] AC-4: the four existing cases at `tests/unit/test_workflow_executors.py:383-412` pass
      unmodified.
- [ ] AC-5: an `all`-mode join with one fan-in edge and one back-edge fires on its fan-in
      arrival (S-1), asserted by `test_all_mode_fan_track_fire_threshold_matches_fan_total`.
- [ ] AC-6: a multi-branch fan-in with a back-edge does not let a fan-in straggler re-fire
      an `any` join after a fast loop pass has already looped back, asserted by
      `test_straggler_fan_in_suppressed_after_early_loop_pass` (T-2). This closes Q-6 as a
      fixed defect rather than deferring it as a follow-up.
- [ ] AC-7: a join fed by more than one back-edge fires on the first back-edge to arrive
      each pass and treats additional same-pass back-edges as duplicates, asserted by
      `test_multiple_back_edges_pass_total_counts_only_back_edges` (T-1) and
      `test_multi_back_edge_any_fires_and_drains` (T-2), per Q-8.
- [ ] AC-8: the §4 reproduction runs at least three loop passes and terminates through its
      own `end` node, with no `idle_max_seconds` force-fail
      (`workflow_watchdog.py:71-75`) and no reliance on `_JOIN_TTL_SECONDS` expiry.
- [ ] AC-9: the module docstring (`join.py:8-12`) and the script header (`:33-40`) describe
      the implemented rule, including that drain accounting counts fan-in edges only for the
      `fan` track and back-edges only for the `pass` track, and that the two tracks never
      share a counter.
- [ ] AC-10: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
      `backend/`.
- [ ] AC-11: no change to `docs/workflow.schema.md`, `linter.py`, `run_engine.py`, or any
      migration — the fix is confined to `executors/join.py` plus tests. A diff touching
      anything else means the design in §7 was not followed.

## 11. SRS Delta

None. R14.01 already makes self-looping topologies normative and R14.02 already lists
`join`; the fix restores the behavior `join.py:10-12` claims. One documentation note
belongs in the code, not the SRS: that the `fan` and `pass` tracks account for fan-in edges
and back-edges respectively and never share a counter (AC-9).

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
- **FU-3 — misleading watchdog failure reasons.** `workflow_watchdog.py:64-72` can only
  report `idle_max_seconds exceeded`, which reads as a timeout for what may be a stalled
  engine. A reason that names the last executed node and its outcome would have surfaced
  this defect years earlier. Cleared-but-fragile; worth hardening.
</content>
