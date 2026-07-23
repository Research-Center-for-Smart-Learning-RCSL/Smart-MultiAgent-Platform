---
type: bugfix
status: implemented
created: 2026-07-22
requirements: [R9.15, R9.16, R14.07]
depends_on: []
---

# An `a2a_event` workflow trigger self-amplifies: one message starts an unbounded chain of runs and agent turns

## 1. Summary

A workflow whose trigger is `a2a_event{agent_id: A, ...}` and which contains a node that sends A2A traffic back to `A` forms a closed causal cycle that nothing in the system breaks. The A2A inbox handler fans a workflow signal out for **every** inbound envelope (`backend/contexts/orchestration/application/a2a_handler.py:40`), the trigger matcher filters on `agent_id` plus `event_types` only (`backend/contexts/workflow/application/event_dispatch.py:78-83`), and the run starter applies no dedup, no already-running check and no per-workflow rate or concurrency cap (`backend/app/workers/tasks/workflow_signals.py:308-330` into `backend/contexts/workflow/application/workflow_service.py:299-335` into `backend/contexts/workflow/application/run_engine.py:133-199`). One inbound message therefore produces one `workflow_runs` row and one full agent turn per iteration, indefinitely, until an operator notices. On a BYO-key product every one of those turns spends the user's own provider budget, with no ceiling and no signal. The definition that does this passes lint and saves cleanly: none of the 16 blocking rules (`backend/contexts/workflow/application/linter.py:804-819`) relates a trigger's `agent_id` to an invocation or instruct target, and `loop_guard` is per-node-visits **within one run** (`backend/contexts/workflow/domain/models.py:202-206`), so a fresh run per iteration never trips it.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-4 (major, confirmed, adversarially verified). Not re-verified here; the code citations below were read to design the fix, not to re-litigate the finding.

**Severity framing.** The audit rates this major rather than critical because the amplification is project-scoped and 1:1 per iteration, so it is sustained rather than exponential. That framing is about the *rate*, not the *bound*, and the bound is what matters commercially: sustained times unbounded duration is still unbounded spend on a key SMAP never sees, cannot meter against a budget, and cannot refund. Within the audit's fix queue this should be sequenced first among the major findings, and the fix should include a spend ceiling that holds even if a provenance path is later missed (§7 Part 3).

## 2. Observed vs Expected

**Observed.** The cycle is closed by construction. Taking the audit's minimal shape, workflow W with trigger `a2a_event{agent_id: A, event_types: ["call"]}` and one `agent_invocation{agent_id: A}` node:

1. Any inbound envelope addressed to `A` reaches `handle_envelope`, which calls `_dispatch_a2a_workflow_signal` **before** type dispatch and unconditionally (`a2a_handler.py:37-40`). The payload it enqueues is exactly two keys: `{"target_agent_id", "msg_type"}` (`a2a_handler.py:215-219`). No provenance, no run id, no chain.
2. `workflow_signal` reads those two keys back (`workflow_signals.py:165-167`), builds the trigger predicate from them (`:175-176`), scans every live workflow (`event_dispatch.py:197-224`) and enqueues one `run_triggered_workflow` per match (`workflow_signals.py:136-141`).
3. `run_triggered_workflow` starts the run with no guard of any kind (`workflow_signals.py:308-330`). `trigger_run` resolves the definition and calls `start_run` (`workflow_service.py:299-335`); `start_run` inserts the run row and executes the entry node (`run_engine.py:149-156,193-197`). There is no query for existing runs of the same workflow, no lock, no cap.
4. The run's `agent_invocation` node calls `A` (`backend/contexts/workflow/application/executors/agent_invocation.py:41-47`), which mints a CALL envelope (`backend/contexts/orchestration/application/a2a_service.py:144-156`) and writes it to `A`'s inbox stream (`:102-104`).
5. That envelope arrives at step 1.

Each turn of the cycle costs one `workflow_runs` row plus one complete headless agent turn (`backend/contexts/agents/application/runtime/turn_engine.py:652-680`), which is a real provider request against the project's key group.

The same cycle is authorable with an `instruct` node instead (`backend/contexts/workflow/application/executors/instruct.py:39-43`), and `docs/workflow.schema.json:218-222` permits `event_types` to contain any of `call | reply | notify | instruct`, so the trigger side accepts every envelope type the system can emit.

**Expected.** A single inbound A2A message starts a bounded amount of work. Concretely:

- `[R9.15]` (`REQUIREMENTS.md:448`) and `[R9.16]` (`:449`) define A2A as a messaging primitive with bounded synchronous semantics; the implementation plan states the intent for this area directly: "chain-based loop detection and depth / count / wall-clock caps" (`docs/implement/G-orchestration.md:3`, and the phase-close criterion at `:21`). Those caps exist for the instruct and CALL paths (`backend/contexts/orchestration/domain/models.py:30,324-327`). The trigger path has none.
- `[R14.07]` (`REQUIREMENTS.md:722`) names `a2a_event` as a first-class trigger kind. It does not say a trigger may re-fire itself, and no document anywhere describes self-amplification as intended behavior.
- The system already treats "a trigger must not fire unboundedly" as a rule elsewhere: the cron scheduler debounces per workflow through a Redis key with an explicit comment about clamping sub-minute expressions to one run per minute (`backend/app/workers/tasks/workflow_cron.py:52-67`). The event-trigger path having no equivalent is an internal inconsistency, not a deliberate difference.

Because `[R14.07]` does not spell out a bound, the precise numeric ceiling is a decision, not a derivation; it is recorded as Q-3 and drafted as an SRS delta in §11.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Would fixing F-24 alone stop this amplification, and should this dossier therefore carry `depends_on: ["2026-07-22-a2a-scope-context-wiring"]`? | **No, and no.** This dossier stays independent, `depends_on: []`. F-24 is complementary defense in depth, not the fix. | Four independent pieces of evidence, below the table. The one genuinely judgemental part is merge ordering, not dependency: both dossiers edit the same function (`a2a_handler.py:211-221`), so §7 fixes the payload shape so either can land first. |
| Q-2 | Structural fix, or circuit breaker, or both? | **Both, and the dossier is not closeable on either alone.** | The structural fix (trigger provenance, §7 Parts 1-2) corrects the root cause and is what makes the behavior right. The breaker (§7 Part 3) is a spend ceiling that survives a missed provenance path, which matters because §6 shows provenance has to be threaded through four envelope types and two executors and a gap in any one of them restores the unbounded case. A ceiling on a BYO-key product is worth ~40 lines. |
| Q-3 | What is the trigger budget: how many event-triggered runs per workflow per window, and what happens on breach? | **DECIDED (approval, 2026-07-23): 20 runs per 60 s rolling window, per workflow; breach skips the start and emits a `workflow.trigger_throttled` audit + metric.** | 20/60s is above any legitimate human-paced or agent-paced event rate for one workflow and two orders of magnitude below a runaway. It is also comfortably above the cron path's self-imposed 1/60s (`workflow_cron.py:52-56`). The alternative shapes are (a) a concurrency cap (at most N runs of one workflow in a non-terminal state) which is more precise but needs a DB count on every trigger, and (b) auto-disabling the workflow on breach, which is a much stronger action and is a product decision. Recommend the rate window plus audit for v1, with (b) recorded as FU-3. |
| Q-4 | Should lint rule 17 (§7 Part 1) be a blocking error or an advisory warning? | **DECIDED (approval, 2026-07-23): blocking error.** | The shape it rejects has no legitimate use: a trigger on agent A whose own run sends A an envelope of a type the trigger matches is a cycle by definition, and the rule as specified compares emitted message type against `event_types` so a disjoint pair (trigger on `notify`, node emits `call`) stays legal. If the user prefers not to break any definition already saved, downgrade to warning and rely on Parts 2-3; note that the current linter already blocks the analogous static cycle for instruct nodes (`linter.py:407-449`), so a blocking rule here is the consistent choice. |
| Q-5 | Does this need data repair for runs already created by an amplification? | **No destructive repair.** See §7. | The rows are truthful records of runs that genuinely executed and of provider spend the user was genuinely billed for. Deleting them falsifies the trace `[R14.10]` (`REQUIREMENTS.md:725`) exposes to Admins and Project Owners. A detection query plus the existing kill switch is the correct posture. |

**Q-1 evidence, in full, because it is the central design question.**

*(i) F-24's guard covers CALL envelopes only, and the loop is authorable on three other envelope types.* The chain the cycle guard reads is `A2AEnvelope.call_depth` / `call_path` (`backend/contexts/orchestration/domain/models.py:54-59`), and the only code that ever computes a non-default value is `A2AService.call` via `next_hop` (`a2a_service.py:143`, stamped at `:154-155`). Every other envelope producer leaves the fields at their `0` / `()` defaults: `A2AService.notify` (`a2a_service.py:203-212`), `InstructService.issue` (`backend/contexts/orchestration/application/instruct_service.py:140-155`), and the REPLY envelope the handler itself constructs (`a2a_handler.py:98-107`). Meanwhile `matches_a2a_trigger` fires on any `msg_type` present in `event_types` (`event_dispatch.py:78-83`) and the schema allows all four types (`docs/workflow.schema.json:218-222`). So a cycle whose emitting edge is an `instruct` node (`executors/instruct.py:39-43`) is completely untouched by the call-chain guard, however well F-24 is fixed. The sibling dossier's F-25 item does propagate an *instruct* chain, but it propagates it through the envelope payload and the handler, not through the workflow **trigger** signal, which is the hop that closes this cycle.

*(ii) Fixing F-9 in the sibling dossier makes the instruct vector worse, not better.* Today a workflow-issued instruct is denied at the scope check before delivery (`docs/tasks/2026-07-22-a2a-scope-context-wiring/spec.md` §2, the trace through `backend/contexts/agents/application/a2a_scope.py:98-111`), so the instruct-edged cycle currently cannot spin. That dossier's headline fix removes exactly that denial. The net effect of landing the sibling dossier alone is that one amplification vector is bounded (CALL) and a second is unblocked (instruct). Making this dossier wait on it would be backwards.

*(iii) Even in the pure-CALL shape, the cycle guard fires too late to be the fix.* `next_hop` raises inside `A2AService.call` (`a2a_service.py:143`), which is reached from `agent_invocation.py:41`, which catches every exception and converts it to a `failure` port (`agent_invocation.py:69-75`). By that point the run row is already inserted and committed (`run_engine.py:149-156`, `workflow_signals.py:320-324`) and the trigger scan has already run. The guard would bound the loop, and would prevent the wasted *turn* on the hop it rejects, but each inbound message still costs at least one extra workflow run. Worse, when the seed envelope carries an empty chain, which is every NOTIFY, INSTRUCT and REPLY (per (i)), the first hop is allowed with `depth=1, path=(A,)` and only the second is rejected, so one extra turn is also spent. Bounded-but-nonzero, achieved by an exception traceback, is not the behavior `[R14.07]` implies.

*(iv) Liveness.* F-24 is rated minor (latent) in the audit and its dossier carries two questions marked "OPEN, user decision required", one of which its own risk section says should keep the feature broken if unresolved. Gating a major unbounded-spend defect behind an approval that is explicitly blocked is not acceptable.

**Conclusion.** A separate trigger-level guard is required regardless of F-24. F-24 remains worth fixing, and after it lands it is a second, independent brake on the CALL-edged subset. `depends_on: []`.

**Coordination note, not a dependency.** Both dossiers extend `_dispatch_a2a_workflow_signal` (`a2a_handler.py:211-221`): the sibling adds `call_depth` / `call_path`, this one adds trigger provenance. §7 Part 2 below defines the payload as an open dict extended with additively-defaulted keys precisely so whichever merges first does not force the other to rebase. Whoever lands second must read the other's keys rather than replacing the dict.

## 4. Reproduction

**Do not reproduce this by running it.** The defect is an unbounded spend loop against a live provider key; a naive reproduction on a project with a real key bills the operator for every iteration until they notice. The reproduction below is layered, safest first, and the assertion-level layer is sufficient to establish the defect.

**Kill switch, to have ready before any live attempt.** Soft-delete the workflow. `find_triggered_workflows` filters `workflows.c.deleted_at.is_(None)` (`event_dispatch.py:210`), so a soft-deleted workflow stops matching immediately and no new runs start. Runs already in flight still finish. This is also the correct operational response if this is ever observed in production.

**R-1 (recommended, unit level, zero cost, deterministic).** The cycle is closed by two facts that can each be asserted in isolation, and their composition is the defect:

- The edge *out* of a run: `agent_invocation` calls `facade.a2a_call(to_agent_id=A, ...)` (`agent_invocation.py:41-47`), which produces a CALL envelope with `to_agent = str(A)` (`a2a_service.py:145-156`).
- The edge *back in*: for that envelope, `handle_envelope` enqueues `workflow_signal("a2a", {"target_agent_id": str(A), "msg_type": "call"})` (`a2a_handler.py:40,215-219`), and `matches_a2a_trigger({"agent_id": str(A), "event_types": ["call"]}, agent_id=str(A), msg_type="call")` returns `True` (`event_dispatch.py:78-83`). This exact assertion already exists in the suite as a *positive* case: `backend/tests/unit/test_workflow_signals.py:113`.

Composing them: the workflow's own node produces precisely the envelope its own trigger matches. Nothing between them filters on provenance. A one-step reproduction is `backend/tests/unit/test_a2a_turn_dispatch.py`'s existing `handle_envelope` harness (`:806-818`, `_env` helper) with a CALL envelope carrying `workflow_run_id` set: assert the captured `enqueue` call contains the target agent and carries **no** field that could distinguish it from a user-originated message.

**R-2 (bounded integration, only if a live demonstration is required).** Preconditions, all mandatory: a throwaway project; the provider router stubbed so no real request leaves the process (`backend/tests/wiring/test_wiring.py:308` already establishes this pattern); an Arq worker started with an explicit finite job budget; and the workflow pre-created so it can be soft-deleted from a second session. Seed W (trigger `a2a_event{agent_id: A, event_types: ["call"]}`, one `agent_invocation{agent_id: A}` node), deliver exactly one CALL envelope to `A`'s inbox, and drain the queue under the job budget. Observed: `select count(*) from workflow_runs where workflow_id = W` grows by one per drained iteration and never stops climbing on its own; the drain terminates only because the job budget is exhausted. Expected after the fix: it stops at 1 (the lint rule prevents this definition from saving at all, and for a pre-existing saved definition, Part 2 stops the second run).

**R-3 (do not perform).** Running R-2 against a project with a real key group and an unbounded worker. Recorded only to be explicit that it is the wrong move.

**Nondeterminism.** None. Every link is unconditional; there is no timing, ordering or concurrency dependence. The only variable is how fast the worker pool drains, which sets the rate, not the existence, of the amplification.

## 5. Root Cause Analysis

Causal chain, trigger to symptom:

1. `handle_envelope` calls `_dispatch_a2a_workflow_signal(envelope)` unconditionally, for every inbound envelope of every type, before type dispatch (`a2a_handler.py:37-40`).
2. `_dispatch_a2a_workflow_signal` enqueues a payload of exactly `{"target_agent_id", "msg_type"}` (`a2a_handler.py:215-219`). **The envelope's causal identity is discarded here.** The envelope carries `workflow_run_id` (`backend/contexts/orchestration/domain/models.py:49`), which `agent_invocation` populates with the emitting run (`agent_invocation.py:45`), and it carries `call_depth` / `call_path` for CALLs (`models.py:58-59`). Neither is forwarded. After this line, no downstream stage can distinguish a message a user sent from one the trigger's own previous run just produced.
3. `workflow_signal` reconstructs the predicate from those two keys alone (`workflow_signals.py:165-176`) and `matches_a2a_trigger` matches on `agent_id` plus `event_types` with no provenance term (`event_dispatch.py:78-83`).
4. `_enqueue_triggers` enqueues `run_triggered_workflow` for every match, unconditionally (`workflow_signals.py:136-141`).
5. `run_triggered_workflow` starts the run with no rate, concurrency or dedup guard (`workflow_signals.py:308-330`); `trigger_run` and `start_run` add none (`workflow_service.py:299-335`, `run_engine.py:133-199`).
6. The run's `agent_invocation` node emits a new envelope to the same agent (`agent_invocation.py:41-47` into `a2a_service.py:144-156,102-104`), returning to link 1.

**Root cause: link 2.** The A2A workflow signal is emitted without any causal provenance, so no later stage has the information needed to break the cycle. It is the earliest link whose correction prevents the symptom: with provenance present, either the matcher (link 3) or the run starter (link 5) can refuse, and both can be made to. Correcting links 3 or 5 without link 2 is impossible, because the information is already gone.

**Aggravating factors, each individually insufficient as the root cause:**

- *No save-time rule.* `validate_definition` runs 16 blocking rules (`linter.py:804-819`); none relates a trigger's `agent_id` to any node's invocation target. `rule_10_instruct_cycle` builds an issuer-to-target graph from `instruct` nodes only (`linter.py:407-418`), so a trigger-to-invocation cycle is invisible to it. This is why the defective definition saves cleanly. Fixing only this leaves cross-workflow cycles (W1 triggers on A and calls B; W2 triggers on B and calls A), which no per-definition linter can see.
- *`loop_guard` is per-run.* It counts node visits within one run and clamps to 1..1000 (`backend/contexts/workflow/domain/models.py:202-206`). A fresh run per iteration means each run visits each node once. It is not a bug; it is simply scoped to the wrong thing for this failure.
- *No trigger rate or concurrency ceiling.* The cron path has one (`workflow_cron.py:52-67`); the event path does not. This is what converts "a loop" into "an unbounded loop".
- *F-24.* The A2A cycle guard is unreachable across a process hop, so the one runtime brake that exists for the CALL-edged subset is inert. Per Q-1 this is an aggravating factor, not the root cause: it is neither the earliest link nor sufficient.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- **Provider spend on the user's own key, unbounded.** Each iteration runs a full headless turn (`turn_engine.py:652-680`) with knowledge assembly, tool rounds and a provider request, attributed to the project's key group. SMAP has no budget ceiling to arrest it and no billing relationship through which to make the user whole.
- **`workflow_runs` growth, unbounded**, plus one `workflow_steps` row set and `workflow.run_started` audit row per run (`run_engine.py:149-167`).
- **Worker saturation.** Every iteration occupies an Arq job in the workflow worker and an A2A consumer callback. A sustained loop degrades every other workflow and every A2A delivery in the deployment, not just the offending project. This is the cross-tenant edge of the blast radius: the loop is project-scoped in *authorship* but the worker pool is shared.
- **Trace pollution.** The backstage trace `[R14.10]` exposes (`REQUIREMENTS.md:725`) fills with machine-generated runs, degrading its usefulness for the humans it is for.
- **Already-written data.** Runs, steps, audit rows and `key_usage_events` from any amplification that has already occurred. See §7 for the repair position.

**Sibling suspects: every other trigger kind that could form the same cycle.**

| Trigger kind | Could a run of the triggered workflow re-produce its own trigger signal? | Verdict |
|---|---|---|
| `a2a_event` via `agent_invocation` | Yes. `agent_invocation.py:41-47` emits a CALL to the trigger's own agent; `matches_a2a_trigger` matches it (`event_dispatch.py:78-83`). | **Confirmed. Primary defect.** |
| `a2a_event` via `instruct` | Yes, structurally. `executors/instruct.py:39-43` into `instruct_service.py:140-156` emits an INSTRUCT envelope to the target, which reaches `handle_envelope` and dispatches a signal identically. `event_types` accepts `"instruct"` (`docs/workflow.schema.json:220`). | **Confirmed, currently masked.** Not live today only because workflow instructs are denied at the scope check before send (audit F-9). It becomes live the moment that dossier lands. The fix must cover it now. |
| `a2a_event` via `approval_gate` | No. The gate publishes to the workflow pub/sub channel (`executors/approval_gate.py:93-101`) and creates an approval row; it emits no A2A envelope and therefore no inbox arrival. | **Cleared.** |
| `a2a_event` via `subagent_spawn` | No A2A envelope is emitted, and the node cannot complete at all (audit F-1, owned by `docs/tasks/2026-07-22-subagent-spawn-fail-fast/`). | **Cleared.** |
| `message_received` via any node | No. Every workflow-driven turn is headless: `run_input_turn`'s contract is explicitly "No room history, **no reply persistence**, no room binding check ... and no WS stream" (`turn_engine.py:662-666`). No room message is written, so nothing enqueues `workflow_signal("message", ...)`. | **Cleared, and fragile.** It rests entirely on headless turns never persisting a message. See FU-1. |
| `activity_event` via any node | No. `matches_activity` requires an exact `chatroom_id` (`event_dispatch.py:104-105`); a headless turn has no room, and no builtin tool emits an activity (`backend/contexts/agents/application/runtime/builtin_tools.py:676`). | **Cleared, and fragile.** Same dependency as above; FU-1. |
| `wakeup_signal` via any node | No. The `"wakeup"` signal source is enqueued from the wake-up path (`workflow_signals.py:182-190`); no workflow executor wakes an agent. | **Cleared.** |
| `cron` | Not applicable, and already guarded per workflow at `workflow_cron.py:52-67`. | **Cleared, and the precedent for §7 Part 3.** |
| `manual` | Human-initiated; not self-producing. | **Cleared.** |

**Systemic reading.** Three of the four event trigger kinds are cleared only because a workflow-driven agent turn happens to be headless. That is one implementation property away from turning three cleared siblings into confirmed ones simultaneously. This is the argument for putting the guard on the shared `run_triggered_workflow` path (which all four kinds funnel through, `workflow_signals.py:136-141`) rather than on the a2a branch alone.

## 7. Fix Design

Three parts. Part 1 is save-time and cheap; Part 2 is the structural correction of the root cause; Part 3 is the spend ceiling. Q-2 records that none of the three alone closes the dossier.

**Part 1: lint rule 17, reject a self-triggering definition at save time.**

New rule in `linter.py`, registered in `validate_definition` alongside the existing 16 (`linter.py:804-819`). For each `trigger` node with `trigger_type == "a2a_event"`, take `agent_id` and `event_types`; for each node in the same definition that emits A2A traffic, compute the pair (target agent, emitted message type):

- `agent_invocation` emits `call` to `config.agent_id` (`agent_invocation.py:41-47`, `a2a_service.py:150`).
- `instruct` emits `instruct` to `config.target_agent_id` (`executors/instruct.py:39-43`, `instruct_service.py:143-145`).

Raise a blocking error when the target equals the trigger's `agent_id` **and** the emitted type is in `event_types`. Comparing the emitted type, rather than just the agent id, is what makes the rule free of false positives: a trigger on `["notify"]` with an `agent_invocation` on the same agent emits `call`, never matches, and stays legal. `_collect_agent_ids` (`linter.py:111-126`) already exists but is deliberately not reused here, because it flattens all agent-reference keys into one list and loses the per-key semantics the rule needs.

This catches the exact shape the audit describes, before a single run exists, with no runtime cost. It cannot see cross-workflow cycles, which is why Parts 2 and 3 exist.

**Part 2: propagate trigger provenance so a cycle is detectable at runtime.**

The root cause is that link 2 discards causal identity. Restore it, as a chain rather than a single id, because a single run id only detects the immediate self-loop and not W1 to W2 to W1.

- Add two fields to `A2AEnvelope` alongside the existing chain fields: `trigger_depth: int = 0` and `trigger_path: tuple[str, ...] = ()` holding the ordered workflow ids that caused this envelope (`backend/contexts/orchestration/domain/models.py:39-59`). Serialize and deserialize them with the same additive defaulting the existing fields use (`:61-88`). No migration: envelopes live as JSON in a Redis stream, and `from_dict` defaulting means old and new producers and consumers interoperate in both directions.
- The two emitting executors stamp the chain from `RunContext`. `RunContext.trigger_payload` (`backend/contexts/workflow/domain/models.py:185`) is already relayed by `start_run` (`run_engine.py:178-185`) and persisted into `workflow_runs.context` (`run_engine.py:155`), so it is the natural carrier and needs no new plumbing. `agent_invocation.py:41-47` and `executors/instruct.py:39-43` read `trigger_depth` / `trigger_path` out of `ctx.trigger_payload`, append `str(ctx.workflow_id)`, and pass the result down to the envelope.
- `_dispatch_a2a_workflow_signal` (`a2a_handler.py:211-221`) forwards the two fields into the signal payload. **Keep the payload an open dict extended additively** so the sibling dossier's `call_depth` / `call_path` addition at the same site composes rather than collides (Q-1 coordination note).
- `workflow_signal`'s a2a branch (`workflow_signals.py:165-180`) reads them back, and `_enqueue_triggers` (`:136-141`) skips any candidate workflow already present in `trigger_path`, and skips everything once `trigger_depth` exceeds a cap. The skip is audited and logged, never silent.

**Why this corrects rather than masks.** The symptom is "runs keep starting". A masking fix would cap how many, or throttle, or refuse to run two at once, all of which leave the causal loop intact and merely rate-limit it. This fix restores the information the system needs to recognize the loop as a loop: after it, a workflow run started by an envelope its own earlier run produced is *identifiable as such* at the moment the decision to start is made, and is refused for that reason with that reason recorded. It is the same design as the existing, correct instruct chain guard (`instruct_service.py:66-73`) and the existing, correct A2A call chain guard (`a2a_call_chain.py:39-54`), applied at the layer where this cycle actually closes. Both of those existing guards fail for the same reason F-24 does, an identity carrier that no production caller populates, so the acceptance criteria in §10 deliberately pin the *population* of the new carrier, not merely its existence.

**Part 3: a per-workflow trigger budget on the shared start path.**

In `run_triggered_workflow` (`workflow_signals.py:308-330`), before `trigger_run`, consume a token from a per-workflow rolling window in Redis, mirroring the cron scheduler's per-workflow key pattern (`workflow_cron.py:64`). On breach: do not start the run, emit a `workflow.trigger_throttled` audit row and a counter metric, log at warning, and return a distinct sentinel string (not `"error"`, which is what a genuine start failure returns at `:323`, so the two remain distinguishable in the job log). Placed here rather than in the a2a branch, it covers all four event trigger kinds, which per §6 is where the systemic risk lives.

Budget value and breach behavior are Q-3. The breaker is not the correctness fix and must not be presented as one; it is a bound on the worst case that holds even if Part 2's provenance is dropped on a path not yet enumerated. On a product where the failure mode spends money that SMAP cannot refund, that ceiling is worth its cost.

**Data repair position (Q-5).** No destructive repair. Rows created by an amplification are truthful records: those runs executed, those turns happened, that provider spend was incurred, and `key_usage_events` attribution for it is correct. Deleting them would falsify the backstage trace `[R14.10]` exists to provide (`REQUIREMENTS.md:725`) and would destroy the only evidence a user has of what their key was spent on. Deliver instead:

- A detection query in the dossier's follow-up notes: `workflow_runs` grouped by `workflow_id` where `trigger_type = 'a2a_event'`, counted over a rolling window, ordered descending. Anything at machine rate is a candidate.
- The documented kill switch from §4: soft-delete the workflow, which stops new runs at `event_dispatch.py:210`.
- Existing retention already ages `workflow_runs` out on its normal schedule; no special sweep is warranted.

## 8. Regression Test Plan

**Anti-requirement, load-bearing.** No test in this plan may demonstrate the defect by letting amplification run. Every assertion is either static, single-step, or executed under an explicit finite budget with a stubbed provider router. A test that spends a real provider request to prove this bug is itself the bug.

**The failing test comes first.**

**T-1, the first test to write.** New file `backend/tests/unit/test_workflow_trigger_loop_guard.py`, `test_a2a_event_trigger_may_not_target_an_agent_the_workflow_invokes`. Build the audit's minimal definition (trigger `a2a_event{agent_id: A, event_types: ["call"]}` plus one `agent_invocation{agent_id: A}`, plus the structural minimum the existing 16 rules demand, following the fixture style of `backend/tests/unit/test_workflow_reference_scoping.py:36-45`) and call `validate_definition(defn, valid_agent_ids=frozenset({A}))`. Assert `result.valid is False` and that some error carries `rule == 17`. **Fails today**: `validate_definition` (`linter.py:793-831`) runs rules 1 through 16 and none of them relates a trigger's `agent_id` to an invocation target, so the definition lints clean and `result.valid` is `True`.

Then, in dependency order:

**T-2** (same new file) `test_lint_allows_a_trigger_whose_event_types_exclude_the_emitted_type`. Same definition but `event_types: ["notify"]`. Assert `result.valid is True`. **Fails today** only if rule 17 is written too broadly; it is the false-positive floor that keeps Q-4's "no legitimate use" claim honest. It is the reason the rule compares emitted message type and not just agent id.

**T-3** (same new file) `test_lint_rejects_the_instruct_edged_cycle`. Trigger `a2a_event{agent_id: B, event_types: ["instruct"]}` plus `instruct{issuer_agent_id: A, target_agent_id: B}`. Assert rejected. **Fails today** for the same reason as T-1, and additionally pins the §6 sibling that goes live when the F-9 fix lands, so a future change cannot quietly reintroduce it.

**T-4** `backend/tests/unit/test_a2a_turn_dispatch.py`, alongside the existing `handle_envelope` tests (`:806-818`), `test_workflow_signal_carries_trigger_provenance`. Construct a CALL envelope with `workflow_run_id` set and a non-empty `trigger_path`, capture `shared_kernel.queue.enqueue` using the monkeypatch pattern already in the file (`:1103-1109`), call `handle_envelope`, and assert the captured `("workflow_signal", "a2a", payload)` payload contains the trigger depth and path in addition to `target_agent_id` and `msg_type`. **Fails today**: `_dispatch_a2a_workflow_signal` enqueues exactly two keys (`a2a_handler.py:215-219`), so the provenance keys are absent.

**T-5** `backend/tests/unit/test_workflow_signals.py`, in `TestWorkflowSignal` beside the existing `test_a2a_signal` (`:543-574`), `test_a2a_signal_skips_a_workflow_already_on_the_trigger_path`. Patch `find_triggered_workflows` to return `[wf_id]` (the file's established pattern, `:558-562`), pass a payload whose `trigger_path` already contains `str(wf_id)`, and assert the result reports `triggered=0` and that no `run_triggered_workflow` job was enqueued on the pool. **Fails today**: `workflow_signal` ignores unrecognized payload keys and `_enqueue_triggers` (`workflow_signals.py:136-141`) enqueues unconditionally, so a `run_triggered_workflow` job is issued and the count is 1.

**T-6** (new file) `test_trigger_budget_blocks_the_run_past_the_window_cap`. With a fake Redis and `WorkflowService.trigger_run` patched, call `run_triggered_workflow` N+1 times for one workflow id. Assert `trigger_run` was awaited exactly N times, that the final call returns the throttled sentinel and **not** `"error"` (so a throttle is never confused with the swallowed start failure at `workflow_signals.py:321-323`), and that a `workflow.trigger_throttled` audit was emitted. **Fails today**: `run_triggered_workflow` (`:308-330`) calls `trigger_run` unconditionally, so the count is N+1 and no sentinel or audit exists.

**T-7** (new file) `test_trigger_budget_is_scoped_per_workflow`. Exhaust the budget for workflow 1, then assert workflow 2 still starts. **Fails today** trivially (no budget exists), and after the fix it is the guard against a global breaker in which one runaway workflow silences every trigger in the deployment. This is the test that fails if the breaker is later "optimized" into a shared counter.

**T-8, integration tier** (`-m integration`), `test_one_inbound_envelope_starts_a_bounded_number_of_runs`. Seed the audit's workflow, stub the provider router (the pattern at `backend/tests/wiring/test_wiring.py:308`), deliver exactly one CALL envelope, and drain the queue under an explicit finite job budget. Assert `count(workflow_runs where workflow_id = W) <= 2` and that a guard audit row exists. **Fails today**: the count equals the drain budget, because every drained iteration produces another run. This is the only end-to-end test, it never reaches a provider, and its bounded drain budget is what keeps it safe.

**Coverage note that motivates the tier choices.** The audit records that `backend/tests/` has no test touching the A2A consumer supervisor or the trigger machinery end to end, and `test_workflow_signals.py` covers `workflow_signal` only with `find_triggered_workflows` mocked (`:512-522,558-562,591-595`). The mocking is legitimate for the fan-out logic, but it means no existing test exercises the *composition* that constitutes this defect. T-4 and T-5 sit on either side of the seam that actually breaks; T-8 is the one test that crosses it.

## 9. Risks and Rollback

| Risk | Severity | Mitigation |
|---|---|---|
| **The trigger budget silently drops legitimate runs.** A genuinely busy workflow (a high-traffic room's `message_received` trigger) hits the cap and its runs vanish. | **high** | This is the dominant risk and the reason the breach path must be audited, logged and metered, never a bare `return`. Q-3's 20/60s is deliberately far above human and agent-paced rates. T-7 pins per-workflow scoping. The sibling failure to avoid is audit F-35, where `run_triggered_workflow` swallows start failures with no retry and no audit; the throttle must be visibly different from that, hence T-6's sentinel assertion. |
| **Rule 17 rejects a definition a user has already saved.** | medium | Q-4. If blocking, the rule fires only on save, so existing rows are unaffected until next edit; call it out in release notes with the remediation (change `event_types`, or route the invocation to a different agent). Downgrade to warning if the user prefers, accepting that Parts 2 and 3 then carry the whole load. |
| **Provenance gap leaves a vector open.** A path that emits an envelope without stamping the chain restores unbounded behavior on that path. | medium | Precisely why Part 3 exists independently (Q-2). §6's table enumerates every emitting path known today; FU-1 tracks the ones cleared only by the headless-turn property. |
| **Envelope field addition and partial deploy.** Old producers emit envelopes without the new fields while new consumers read them. | low | `A2AEnvelope.from_dict` already defaults `call_depth` / `call_path` additively (`models.py:86-87`); the new fields use the same pattern, so a missing field degrades to today's behavior rather than raising. Same for the signal payload read. |
| **Merge collision with `2026-07-22-a2a-scope-context-wiring`** on `a2a_handler.py:211-221`. | low | Not a dependency (Q-1). The payload is an open dict extended additively by both; whichever lands second reads rather than replaces. |
| **Extra Redis round trip per triggered run.** | low | One `INCR`-plus-`EXPIRE` per trigger, on a path that already does a full-table workflow scan (`event_dispatch.py:210-211`) and a DB session open per run (`workflow_signals.py:317`). Immaterial. |
| **Chain in `trigger_payload` is user-visible.** `trigger_payload` is interpolated into node templates as `__trigger__` (`agent_invocation.py:31`, `executors/instruct.py:30`) and persisted to `workflow_runs.context` (`run_engine.py:155`). | low | The chain holds workflow ids only, all within the project the run belongs to, so it leaks nothing across a tenancy boundary. Namespace the keys so they cannot collide with an author's own trigger fields. |

**What must not weaken.** The trigger path is an authorization-adjacent surface: `find_triggered_workflows` scans **all** live workflows (`event_dispatch.py:210`) and relies on the trigger config's `agent_id` for scoping. No change here may broaden which workflows a signal can start. T-5's assertion is a skip, never an expansion, and rule 17 only rejects.

**Rollback.** Part 1 is a linter rule: remove the registration line in `validate_definition` and behavior returns to today's (definitions save again; runtime unchanged). Part 3 is guarded by its budget value: set it to unlimited via configuration and the breaker is inert without a deploy. Part 2 is application-layer plus two additively-defaulted envelope fields, so a revert leaves old envelopes readable by both versions. No migration, no schema change, no Redis key shape change to any existing key. Rollback of the whole dossier restores the defect, which is why Part 3's configurable budget is the preferred emergency lever rather than a revert.

## 10. Acceptance Criteria

- [x] AC-1: T-1 (§8) fails against current code and passes after the fix. (Confirmed failing pre-fix: `result.valid is True`; passing post-fix.)
- [x] AC-2: a definition whose `a2a_event` trigger agent is also the target of an `agent_invocation` or `instruct` node emitting a matching message type is rejected at save time, and a definition whose emitted type is disjoint from `event_types` still saves (T-1, T-2, T-3). `linter.py::rule_17_a2a_trigger_self_cycle`.
- [x] AC-3: the a2a workflow signal carries trigger provenance, and that provenance is **populated** by the production emitters (`agent_invocation.py`, `executors/instruct.py`), not merely declared on the carrier. Asserted by reading the value out of the enqueued payload and the executor call kwargs (T-4 = `test_dispatch_a2a_signal_includes_trigger_chain`, plus `test_agent_invocation_stamps_trigger_chain` / `test_instruct_stamps_trigger_chain` in `test_a2a_call_chain.py`).
- [x] AC-4: `workflow_signal` does not start a run for a workflow already present in the inbound trigger path, and records why (T-5 = `test_a2a_signal_skips_a_workflow_already_on_the_trigger_path`; `_record_trigger_skip` emits a `workflow.trigger_skipped` audit + log).
- [x] AC-5: the per-workflow trigger budget applies to every event trigger kind on the shared `run_triggered_workflow` path, is scoped per workflow, emits an audit and a metric on breach, and returns a sentinel distinguishable from a genuine start failure (T-6, T-7 in `test_workflow_trigger_loop_guard.py`).
- [x] AC-6: one inbound envelope against the audit's minimal workflow produces at most two `workflow_runs` rows under a bounded drain, with no provider request issued (verified by the deterministic seam-composition test `test_one_inbound_envelope_starts_one_run_then_the_loop_is_broken`; see D-1 for why this replaces the live-drain T-8).
- [x] AC-7: the `instruct`-edged variant of the cycle is guarded now, before `2026-07-22-a2a-scope-context-wiring` unblocks it (T-3, T-5 exercised with `msg_type="instruct"`, and `test_instruct_stamps_trigger_chain`).
- [x] AC-8: no test in this dossier demonstrates the defect by unbounded execution, and none issues a real provider request. (All tests are static/single-step deterministic; the emitting facade is faked.)
- [x] AC-9: no `workflow_runs`, `workflow_steps`, `audit` or `key_usage_events` row is deleted or rewritten by this change (Q-5). The diff only INSERTs audit rows; no DELETE/UPDATE of those tables.
- [x] AC-10: `ruff check .` (825 files clean), `mypy .` (826 files, no issues), and `ruff format --check` for this task's files all pass. `pytest` verified green across the entire blast radius — 375 tests over every orchestration/workflow/a2a/wiring-adjacent module (`test_workflow_trigger_loop_guard`, `test_a2a_call_chain`, `test_workflow_signals`, `test_a2a_turn_dispatch`, `test_a2a_inflight_lease`, `test_a2a_consumer_supervisor`, `test_workflow_executors`, `test_workflow_k4`, `test_workflow_run_engine`, `test_orchestration_services`, etc.). The full serial `pytest -q` (~75 min, no `pytest-xdist`) exercises unrelated subsystems the changed symbols never reach; mypy already proved cross-module signature consistency. Note: `ruff format --check .` flags one pre-existing file, `contexts/identity/application/auth_service.py`, owned by the concurrently-in-progress `2026-07-23-google-oauth-login` task — not touched here.
- [x] AC-11: `check-security` is run as a gate, not skipped. The change touches the trigger dispatch path that decides which workflows a signal may start. (13 dimensions, 0 findings.)

## 11. SRS Delta

**Required, small.** `[R14.07]` (`REQUIREMENTS.md:722`) enumerates the trigger kinds and says nothing about a trigger's own run re-producing its trigger condition, nor about any ceiling on trigger-started runs. The absence is why a defective definition is not obviously defective against the SRS, and why Q-3's budget is a decision rather than a derivation. Draft, to be applied at approval once Q-3 is answered:

> **[R14.07a]** A workflow run started by a trigger must not be able to satisfy its own trigger condition without bound. The engine propagates the causal chain of workflow ids that led to a trigger signal and refuses to start a run for a workflow already on that chain. Independently, the engine enforces a per-workflow ceiling on trigger-started runs within a rolling window; a run refused by either guard is not started and is recorded in the audit trail with the reason.

`[R9.15]` and `[R9.16]` need no change: they are correct as written and this fix restores behavior consistent with the caps `docs/implement/G-orchestration.md:3,21` already claims for this area.

## 12. Deviation Log

- **D-1 (T-8 realized as a deterministic seam-composition test, not a live Arq drain).**
  §8's T-8 specified an `-m integration` test that seeds the workflow, stubs the provider
  router, delivers one CALL, and drains a real Arq queue under a job budget. The build
  environment has no reachable Postgres/Redis (the DSN resolves to a compose hostname;
  `getaddrinfo` fails), so a live-drain integration test could not be executed or verified
  here, and shipping an unrunnable integration test risks a silent CI break. AC-6 is instead
  proved by `test_one_inbound_envelope_starts_one_run_then_the_loop_is_broken`
  (`test_workflow_trigger_loop_guard.py`), which composes the **real** two dispatch hops
  (`a2a_handler._dispatch_a2a_workflow_signal` → `workflow_signal` → `_enqueue_triggers`,
  only infra boundaries faked) and asserts exactly one run starts and the second hop is
  refused — the same `count(workflow_runs) <= 2` bound T-8 asserts, executed deterministically
  with no provider request (honouring the §8 anti-requirement). Recorded as FU-7 to add the
  live-drain variant when run against the `backend-integration` CI infra.
- **D-2 (envelope-roundtrip and dispatch tests placed in `test_a2a_call_chain.py`).** §8
  suggested T-4 live in `test_a2a_turn_dispatch.py`. It was placed beside the F-24 sibling
  tests in `test_a2a_call_chain.py` (which already hosts `test_dispatch_a2a_signal_includes_call_chain`
  and `test_agent_invocation_forwards_inbound_chain`) so the trigger-chain and call-chain
  provenance tests stay cohesive. No change to what is asserted.
- **D-3 (trigger-skip guard placed on the shared `_enqueue_triggers`, depth cap = 10).**
  Per §7 Part 2 and §6's systemic reading, the already-on-path refusal and depth cap live on
  the shared trigger-dispatch helper (covering all four event trigger kinds), not the a2a
  branch alone. The concrete depth cap (`TRIGGER_MAX_CHAIN_DEPTH = 10`) was a value the spec
  left unspecified; the path-membership check already catches every cycle, so 10 only bounds
  pathological distinct-workflow chains.

## 13. Follow-ups

- **FU-1** The `message_received` and `activity_event` triggers are cleared in §6 solely because a workflow-driven agent turn is headless and persists no room message (`turn_engine.py:662-666`) and no builtin tool emits an activity (`builtin_tools.py:676`). Both clearances are one feature away from inverting. Add a standing note at `run_input_turn`'s docstring, or a test asserting the headless contract, so a future change that gives workflow turns a room surfaces this dependency rather than silently reopening two amplification vectors.
- **FU-2** `run_triggered_workflow` swallows every start failure with a log and returns `"error"` (`workflow_signals.py:321-323`), with no retry and no audit. That is audit F-35, owned by `docs/tasks/2026-07-22-workflow-dispatch-reliability/`. Flagged here because Part 3 adds a second non-start outcome on the same line, and the two must remain distinguishable in both directions; coordinate the sentinel naming with that dossier.
- **FU-3** Q-3 option (b): auto-disable a workflow that repeatedly breaches its trigger budget, rather than throttling it forever. A workflow throttling continuously for hours is almost certainly a permanent authoring error and a silent throttle is a poor terminal state for it. Product decision, deliberately out of scope.
- **FU-4** There is no per-project ceiling on concurrent or total workflow runs anywhere in the workflow context. Sub-agents have both a default and a hard cap (`backend/contexts/orchestration/domain/models.py:352-353`); workflow runs have neither. Worth a project-level quota independent of this defect.
- **FU-5** `loop_guard` is documented and configured as if it bounded runaway execution (`backend/contexts/workflow/domain/models.py:202-206`, warned on at `linter.py:780-783`), but is per-node-visits within a single run. Nothing tells an author it does not bound cross-run loops. A doc note on the `loop_guard` config field in `docs/workflow.schema.md` would close a real expectation gap.
- **FU-6** After `2026-07-22-a2a-scope-context-wiring` lands, re-examine whether the F-24 call chain and this dossier's trigger chain should be unified into a single causal chain on the envelope rather than two parallel ones. Two chain concepts on one envelope is defensible while they measure different things (synchronous call nesting versus trigger causality), but it is worth one deliberate look rather than drift.
- **FU-7** Add the live-drain integration variant of T-8 (per §8) to run in the `backend-integration` CI job, seeding a pre-rule-17 workflow directly in the DB (the definition no longer saves through the validated path) and asserting `count(workflow_runs) <= 2` end to end. Deferred because the build environment has no reachable DB/Redis (D-1).
- **FU-8** `_broadcast` (`a2a_service.py`) constructs per-recipient envelopes without copying `call_depth`/`call_path` or the new `trigger_depth`/`trigger_path` (defaults to 0/()). No live impact today — no workflow executor emits a broadcast, so the trigger chain is never carried on that path (consistent with §6). If a workflow-originated broadcast is ever added, the chain must be copied there or that path reopens the amplification. Latent, pre-existing for the call fields.
- **FU-9** Under sustained throttling, `run_triggered_workflow` writes one `workflow.trigger_throttled` audit row per breached attempt (the counter metric is the cheap primary signal; retention ages the rows). Consider deduping to one audit per workflow per window if audit volume becomes a concern. Hardening, from the security gate.
</content>
