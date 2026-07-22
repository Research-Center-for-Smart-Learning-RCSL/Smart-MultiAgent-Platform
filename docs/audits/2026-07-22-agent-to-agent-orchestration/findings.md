---
type: audit
status: draft
created: 2026-07-22
requirements: [R9.13, R9.15, R9.16, R9.17, R14.07, R14.10, R15.01, R15.02, R15.03, R15.04, R15.06, R15.08, R15.09, R15.12, R15.13, R15.15, R15.16, R15.17, R15.18, R15.19, R15.20, R15.21, R15.22, R28.07]
---

# Audit: Agent-to-Agent Orchestration Runtime

## 1. Scope

- **Area** — agent-to-agent runtime behavior: the `orchestration` context in full (A2A
  envelope transport, wake-up policy, instruct, sub-agents, approval gates), the
  concurrency and trigger-coalescing machinery of `contexts/agents/application/runtime/`,
  and the workflow executors and worker tasks that drive agent orchestration
  (`subagent_spawn`, `instruct`, `approval_gate`, `wait_for_event`, `join`, plus
  `app/workers/tasks/orchestration.py`, `workflow_signals.py`, `workflow_steps.py`,
  `workflow_approvals.py`, `workflow_cron.py`).

  This is the first of two dossiers agreed with the user. Agent-to-user behavior
  (streaming event ordering, WebSocket reconnect, observer release UX, structured
  activities, user-facing cancellation) is deliberately deferred to a second audit.

- **Intent sources** — `REQUIREMENTS.md` sections 9.4 (A2A), 14 (workflow engine),
  15 (wake-up / approval / instruct / sub-agents), 28 (observer agents);
  `docs/implement/G-orchestration.md`, `H-workflow.md`, `K-agent-runtime.md`,
  `N-conversation-a2a-fixes.md`; `docs/workflow.schema.md` and `workflow.schema.json`;
  `docs/audits/2026-07-03-observer-agents-audit/findings.md` for the known cross-room
  leak class. Intent sources for this area are strong: most findings below cite a
  numbered requirement rather than an internal inconsistency.

- **Depth** — thorough. Eight investigation lenses (A2A delivery; recursion and call
  chains; wake-up policy; sub-agent lifecycle; approval gates; turn-engine concurrency;
  isolation as correctness; event dispatch and error paths) produced 57 candidates. Every
  candidate went through one independent adversarial verification round whose explicit
  mandate was to refute it. 15 were refuted or reclassified out of the findings list
  (§4); 42 survived, most with severity corrected downward by the verifier.

## 2. Coverage

**Read in full**: `contexts/orchestration/` (all layers), `contexts/agents/application/runtime/`
(`turn_engine.py`, `tool_registry.py`, `builtin_tools.py`, `transcript.py`),
`contexts/agents/application/a2a_scope.py`, `contexts/agents/application/context.py`,
`contexts/workflow/application/` (`run_engine.py`, `linter.py`, `event_dispatch.py`, all
executors), `app/workers/tasks/` (orchestration, workflow_signals, workflow_steps,
workflow_approvals, workflow_cron, workflow_watchdog, approvals, retention),
`shared_kernel/realtime/distributed_lock.py`, `contexts/agents/infrastructure/turn_lock.py`,
`contexts/conversation/application/triggers.py`.

**Sampled, not read in full**: `contexts/skills/` (only `binding_service.py` scoping and
`facade.py` resolution were checked, for the "does a skill leak across rooms" question);
`contexts/agents/infrastructure/sandbox/docker_runsc.py` (1659 lines, not read — sandbox
escape and resource limits are `check-security` territory);
`contexts/conversation/` beyond `triggers.py` and `observation_service.py`;
`contexts/knowledge/` entirely.

**Not covered** (deferred to the agent-to-user dossier): frontend `slices/conversation`
and `slices/workflow` behavior, WebSocket connection lifecycle and reconnect,
`shared_kernel/realtime/connection.py`, message ordering and optimistic insert,
observation release UX, `contexts/activities/`.

**Lenses not applied**: performance and load characteristics; database migration
correctness; provider-adapter behavior; anything requiring a running stack to observe
(every finding here is derived statically, so timing-dependent findings are marked
`plausible` where the window could not be measured).

**Test-coverage note that bounds everything below**: `backend/tests/` contains no test
touching `turn_lock`, `distributed_lock`, `_mark_trigger_queued`/`_pop_queued_trigger`,
`A2AConsumerSupervisor`, `xautoclaim`, `soft_bounds`, `refresh_wakeup_config`,
`wakeup_refresh`, `delay_seconds`, join epochs, or `_sweep_orphaned_subagent_roots`. The
absence of a regression test is stated per finding only where it is load-bearing.

## 3. Findings

Ordered by severity. Never renumber — F-n identifiers are cited from spec dossiers.

Several findings are marked **latent**: the defect is real and verified, but the code path
has no production caller today. They are recorded because each is a precondition for
wiring the feature it belongs to, not because they misbehave now.

---

## F-1: The `subagent_spawn` workflow node can never succeed — it always parks for an hour and then fails the run

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/executors/subagent_spawn.py:68`
  (only production caller of `spawn_subagent`), `:79` (`wait_for_all` defaults to `True`),
  `:100-107` (parks with `timeout_ms=3_600_000`);
  `backend/contexts/orchestration/interfaces/facade.py:319` (`destroy_subagent`, zero
  production callers); `backend/app/workers/tasks/workflow_steps.py:78-99`
  (`workflow_subagent_timeout` force-fails the run). Nothing anywhere reads
  `agent_instances.task_description` to dispatch a turn — no `spawn_subagent` builtin tool
  exists despite `docs/implement/G-orchestration.md:183` specifying one, and
  `backend/app/workers/tasks/retention.py:495` states the condition outright: "Neither the
  synthetic root nor its workflow-spawned children are ever destroyed."
- **Failure scenario**: build any workflow containing a `subagent_spawn` node and run it.
  The node inserts an `agent_instances` row and parks. No worker ever executes the
  sub-agent's task, so `destroy` never runs, so `_fire_workflow_callback` never fires. At
  t=3600s the run is force-failed with `subagent_timeout`. 100% reproducible; the only way
  to avoid the hour-long hang is to hand-edit `wait_for_all: false` into the node JSON,
  which merely skips the wait while still never executing the agent.
- **Blast radius**: every user who selects the node. It is registered
  (`executors/registry.py:49`), linted (`linter.py:39,52`), and fully exposed in the
  editor palette with its own config form (`frontend/src/slices/workflow/constants.ts:35`,
  `components/NodeConfigPanel.vue:40`). No document marks sub-agent execution as unbuilt.
- **Intent source**: R15.18 (spawn performs the task), R15.21 (teardown on task end),
  R15.23 (usage attributed to the ephemeral id).

## F-2: `wait_for_event` with `event_type: "timer"` never fires — and that is the editor's default new-node config

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `docs/workflow.schema.json:371` requires `delay_seconds` for timer waits;
  repo-wide grep finds `delay_seconds` only in the schema, `docs/UI/08-workflow.md:497`,
  `frontend/src/slices/workflow/constants.ts:10`, and
  `components/config/WaitForEventConfigForm.vue:175-179` — **zero backend reads**.
  `backend/contexts/workflow/application/executors/wait_for_event.py:44-45,94-101` arms
  only `timeout_seconds`; `event_dispatch.py` and `workflow_signals.py:143-210` dispatch
  only message / a2a / wakeup / activity / variable events, so nothing ever enqueues
  `workflow_event_resume` for a timer wait. `docs/implement/H-workflow.md:80` lists timer
  as implemented.
- **Failure scenario**: drag a `wait_for_event` node onto the canvas. Its seeded config is
  `{event_type: 'timer', timeout_seconds: 300, delay_seconds: 60}`
  (`constants.ts:10`). The author wires the `default` port expecting a 60-second delay. The
  node parks; only the timeout task is armed; at t=300s `resume_at_port(..., "timeout")`
  seals the step `failed` (`run_engine.py:381`). If `timeout` is unwired — which
  `docs/workflow.schema.md` §5.1 rule 5 permits — `_advance_from` finds no edge
  (`run_engine.py:716-717`) and returns silently. The branch dies with no successor and no
  run-level failure; the run sits RUNNING until the watchdog kills it.
- **Blast radius**: every workflow using the default wait node. The linter does not reject
  `event_type: timer` (`linter.py:302-310,749-754` warn only).
- **Intent source**: `docs/workflow.schema.json:369-373`; `docs/workflow.schema.md` §2;
  `docs/implement/H-workflow.md:80`.

## F-3: The silence wake-up trigger is permanently dead for any agent bound to a room after a user joined it

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the only writer of the flag is
  `backend/contexts/orchestration/application/wakeup_service.py:258`, inside
  `on_presence_changed`. Repo-wide grep for `set_silence_active` returns that call site,
  the definition (`infrastructure/wakeup_state.py:133-144`), and
  `app/workers/tasks/retention.py:701` (which only ever passes `has_live_users=False`).
  `on_presence_changed` is reached only from `app/api/ws/chatroom.py:128`
  (`roster_size == 1`) and `:142` (`roster_size == 0`), and
  `contexts/conversation/application/triggers.py:128-137` snapshots the room's bindings at
  that instant. `evaluate_silence_trigger` (`wakeup_service.py:212`) hard-returns `False`
  for non-observers when the flag is absent, with no lazy set.
- **Failure scenario**: (1) Alice opens room R — the 0→1 edge fires while the room has no
  agents, so `evaluate_presence_change` returns early. (2) Alice binds agent A with
  `silence_minutes.enabled = true, t_minutes = 2`. (3) Alice stays connected and goes
  quiet. Every sweep reads a missing `wakeup:silence_active:{A}:{R}` key and returns
  `False`. A never wakes. The trigger only starts working after Alice drops every
  connection (roster→0) and rejoins — a second user joining does not help, since
  `roster_size == 1` is required.
- **Blast radius**: the most common setup order (create room → join → add agent) silently
  disables a headline feature. No error, no audit, no UI signal.
- **Intent source**: R15.02, R15.05b; `docs/implement/G-orchestration.md:81`.

## F-4: An `a2a_event` workflow trigger self-amplifies — one message starts an unbounded chain of runs and agent turns

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/application/a2a_handler.py:40` dispatches
  `_dispatch_a2a_workflow_signal` for **every** inbound envelope before type dispatch;
  `:211-221` sends `{target_agent_id, msg_type}`.
  `backend/app/workers/tasks/workflow_signals.py:175-180` fans that out;
  `contexts/workflow/application/event_dispatch.py:78-83` `matches_a2a_trigger` filters on
  `agent_id` + `event_types` only, with no provenance and no `workflow_run_id` exclusion.
  `workflow_service.py:299-335` → `run_engine.start_run:133-193` has no dedup, no
  already-running check, and no per-workflow concurrency cap. `loop_guard` is per-node-visits
  *within one run* (`linter.py:781`), so a fresh run per iteration never trips it. Linter
  rules 1-16 (`linter.py:804-819`) contain no rule relating a trigger's `agent_id` to an
  `agent_invocation` target — `rule_10_instruct_cycle` covers `instruct` nodes only.
- **Failure scenario**: workflow W with trigger `a2a_event{agent_id: A, event_types: ["call"]}`
  and one `agent_invocation{agent_id: A}` node. The definition passes lint and saves.
  Anything sends one CALL to A. The consumer enqueues `workflow_signal("a2a", ...)` → W
  triggers run r1 → r1's `agent_invocation` calls A
  (`executors/agent_invocation.py:41` → `a2a_service.call` → stream → handler) → the
  handler signals again → run r2. One new `workflow_runs` row and one full agent turn per
  iteration, indefinitely.
- **Blast radius**: unbounded provider spend on the user's own key (BYO-key), unbounded
  `workflow_runs` growth. Project-scoped and 1:1 per iteration, so sustained rather than
  exponential — which is why this is major rather than critical. The A2A depth guard
  cannot break it (see F-24).
- **Intent source**: R9.15, R9.16; `docs/implement/G-orchestration.md:3,21` ("chain-based
  loop detection and depth / count / wall-clock caps").

## F-5: A2A stream reclaim steals still-in-flight envelopes, re-running the callee's turn

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/infrastructure/a2a_streams.py:30`
  (`_CLAIM_MIN_IDLE_MS = 60_000`) and `:130-155` (XAUTOCLAIM filters on PEL idle time
  only); `application/a2a_consumer.py:52` (`_CLAIM_INTERVAL_SECONDS = 30.0`), `:299`
  (processed-marker check), `:323,326` (marker written only *after* `await handler(...)`
  returns). A CALL handler runs a full agent turn budgeted at 300s
  (`a2a_service.py:42`). The A2A path acquires **no** turn lock: `turn_lock` is used only
  on the room path (`turn_engine.py:590`, key `turn:lock:{agent}:{chatroom}`), while
  `a2a_handler.py:86` → `_run_turn_with_db:183` → `engine.run_input_turn`
  (`turn_engine.py:652`) takes none. `deploy/compose/docker-compose.prod.yml:143` runs
  `replicas: 3`, each starting its own supervisor (`app/workers/main.py:213-221`) with a
  per-process consumer name (`a2a_streams.py:41-50`).
- **Failure scenario**: agent A CALLs agent B. Worker W1 picks up the envelope and starts
  B's turn; the turn takes 90s (a normal multi-round tool turn, well inside the 300s
  budget). At t=60s W2's reclaim tick XAUTOCLAIMs the still-in-flight entry, finds no
  processed marker, and runs the full turn a second time. At t=120s W3 does it a third
  time. All three eventually `deliver_reply` on the same correlation id; the caller's
  BLPOP takes whichever landed first and the rest rot until the 900s TTL.
- **Blast radius**: duplicate provider spend on the user's own key, duplicate tool side
  effects (a turn that writes files or posts messages does so N times). Fires at zero load
  with a single message in flight — no contention required, only a turn exceeding 60s.
  Sub-60s turns ACK before any peer can claim, which is why this is major not critical.
- **Intent source**: `a2a_consumer.py:44-49` (the stated at-least-once/dedup guarantee);
  `docs/implement/N-conversation-a2a-fixes.md` FIX-08 (300s CALL budget). Note the N
  dossier's out-of-scope waiver covers two consumers racing for *different* new entries,
  not one consumer stealing an entry another is actively processing.

## F-6: A workflow's approval gate publishes into any chatroom UUID the caller supplies at trigger time, including another project's

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/executors/approval_gate.py:67` —
  `raw_room = config.get("chatroom_id") or ctx.trigger_payload.get("chatroom_id")`, UUID-parsed
  at `:68-71` and never scope-checked. `linter.py:354-372` (`rule_08_chatroom_scope`)
  inspects only `node["config"]["chatroom_id"]`, so the trigger-payload branch bypasses the
  rule entirely. `app/api/v1/workflows.py:180-181,459-474` accepts `trigger_payload:
  BoundedPayload` (`shared_kernel/validation.py:98` — size-bounded only, no key allowlist).
  No re-check exists downstream: `interfaces/facade.py:186-197` passes it through and
  `orchestration/application/approval_service.py:97-110` publishes to
  `room_channel(chatroom_id)`; `_notify_and_arm:151` copies it into the notify payload and
  `tool_registry.py:287` reuses it so `approval.resolved` (`:439-443`) lands in the same
  foreign room.
- **Failure scenario**: a member of project A with chat-create permission triggers any
  workflow containing an `approval_gate` node whose config omits `chatroom_id`, POSTing
  `{"trigger_payload": {"chatroom_id": "<project-B room uuid>"}}`. `_resolve_workflow`
  authorizes them for project A only, but the gate resolves to the project-B room, and
  every legitimate project-B member with that room open receives a fabricated
  `approval.requested` frame carrying an attacker-authored free-text `question`, followed
  later by `approval.resolved`.
- **Blast radius**: cross-project **event injection**, not disclosure — every field in the
  payload is the caller's own project data, so the caller learns nothing. The phishing
  surface against project-B members is the real harm. The disclosure direction is
  explicitly closed: `turn_engine.py:742-754` nulls `knowledge_chatroom_id` unless
  `is_agent_in_chatroom`, with a comment naming this exact case.
- **Intent source**: R15.10 (a gate declares its own participants and room); linter rule 8's
  stated guarantee at `workflows.py:138-139` ("a reference to another tenant's agent/chatroom
  is still rejected"), which is false for this path; CLAUDE.md multi-tenant AuthZ rule.

## F-7: An Arq-retried `wakeup_agent` re-runs an already-committed turn, double-posting the reply

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: arq 0.26.3 re-queues on `CancelledError` —
  `backend/.venv/Lib/site-packages/arq/worker.py:620-622`
  (`elif self.retry_jobs and isinstance(e, (asyncio.CancelledError, RetryJob))`).
  `backend/app/workers/main.py:252-312` sets neither `retry_jobs=False` nor `max_tries`, so
  the defaults (`True` / 5) apply. `wakeup_agent` is enqueued with no `_job_id`
  (`app/api/v1/messages.py:350`, `app/workers/tasks/orchestration.py:258`) and calls
  `run_turn` with no `request_id` (`orchestration.py:139`); `request_id` in
  `conversation/application/message_service.py:239` reaches only the audit row, never
  `messages.create` — there is no idempotency key anywhere.
  `turn_engine.py:2189` commits the reply, then six more awaits follow
  (`:2193,:2200,:2209,:2213,:2217`). The lock is released by `distributed_lock.py:110-116`'s
  `finally` during cancellation unwind, so the retry acquires freely.
- **Failure scenario**: a worker pod receives SIGTERM after `turn_engine.py:2189` commits
  the reply but before `_dispatch_agent_reply_wakeups` at `:2217` completes. Arq cancels
  the task and re-queues the job. A second worker acquires the now-free lock, re-assembles
  history (which now *includes* the just-posted reply), makes a second provider call, and
  commits a second agent message. The room shows two replies to one trigger, and clients
  receive `agent.token` deltas for a message that already finished.
- **Blast radius**: every rolling deploy and every pod eviction, for any turn in flight.
  Duplicate provider spend on the user's own key. The job-timeout path does *not* retry
  (`asyncio.wait_for` raises `TimeoutError`, handled at `arq/worker.py:623-629`), so the
  window is specifically the cancellation/shutdown one.
- **Intent source**: `docs/implement/K-agent-runtime.md:73,85` ("one concurrent turn per
  agent per room", "lock prevents concurrent double-turns"); REQUIREMENTS §19.

## F-8: An approval-request notification is rendered into whatever room the agent's next turn happens to run in

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:1595-1607` —
  the misroute filter's condition is `n.get("kind") == "released_observation" and (...)`;
  every other kind falls through to `usable`. There is no outer filter: `drain()`
  (`orchestration/infrastructure/pending_notify.py:43-64`) is keyed by agent id only. At
  `:1612-1627` the `approval_request` branch reads `n["chatroom_id"]` solely to key
  `allowed_approvals`, then appends `f"  Question: {n['question']}"` to the prompt
  unconditionally. The note carries a real room and a real interpolated question:
  `approval_service.py:146-155`, filled from `executors/approval_gate.py:32`
  (`interpolate(question_template, ctx.variables + __trigger__)`). The room path threads
  the actual room in at `turn_engine.py:1796-1798`, and `:744-748` documents that a gate's
  `chatroom_id` may be "an arbitrary in-project room set by the workflow author" — so
  room X ≠ room Y is a supported configuration, not a corner case.
- **Failure scenario**: a workflow gate targets room X with a question template
  interpolating run variables. Approver agent A is bound to both room X and room Y. Before
  the deferred `drive_approver_turn` job runs (`app/workers/tasks/approvals.py:90-94`,
  delayed 2s and retried up to 5×2s), a user posts in room Y and A takes a turn there. A's
  room-Y turn drains the room-X note and renders the interpolated question into its room-Y
  system prompt, where A is free to restate it to room-Y users.
- **Blast radius**: same structural class as the observer-agent leak fixed in
  `docs/audits/2026-07-03-observer-agents-audit/`, but materially weaker: the leaked text
  is workflow-author template output interpolated with run variables, not another room's
  private transcript. Note the gate is *not* starved — `allowed_approvals` is populated
  regardless of room (`:1619`, `tool_registry.py:275-288`), so A can still vote correctly.
- **Intent source**: R28.07 leak class; R9.16 (a notify is folded into the *addressed*
  context); the invariant stated in `turn_engine.py:1571-1577`, which the code only
  half-implements.

## F-9: R9.17 rule 3a is inert, so an instruct between two agents sharing a room is denied

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/a2a_scope.py:98-101` computes
  `shared_context` from `caller_invocation_context_id` ∈ `callee_attached_context_ids`;
  there is no same-room branch anywhere in `evaluate` (`:79-111`) — the only other grant is
  `is_call_only_enabled` at `:105`. Both inputs default to empty at every layer:
  `orchestration/application/a2a_service.py:63-65` (`send`), `:132-133` (`call`),
  `:199-200` (`notify`), `:319` (`_broadcast` hardcodes `frozenset()`);
  `interfaces/facade.py:51-52,68-69,88-89`. No production caller supplies either:
  `instruct_service.py:140-156` calls `self._a2a.send(envelope=envelope)` with no context
  arguments at all.
- **Failure scenario**: a workflow with an `instruct` node whose issuer and target are both
  attached to the same chatroom, where the target has not enabled
  `wakeup_config.triggers.call_only`. `send` at `a2a_service.py:88-97` runs
  `_enforce_scope`; `shared_context` is `False` because the caller context is `None`;
  `A2AForbidden` is raised and `executors/instruct.py` returns the `failure` port. An
  `a2a.forbidden` audit row is written — while the instruction row was already INSERTed and
  `instruct.issued` audited at `instruct_service.py:128-136`, before the send at `:156`.
- **Blast radius**: rule 3a can never fire in production. Every instruct between room-mates
  fails unless the target opts into project-wide `call_only`, and each failure leaves an
  orphan `issued` instruction row behind. Fails closed, so no isolation breach — the "false
  allow" half of the original candidate was refuted, since R9.17 rule 3b already grants
  `call_only` at project level by design.
- **Intent source**: R9.17 rule 3 (`REQUIREMENTS.md:453`).

## F-10: A failed workflow run does not stop its sibling parallel branches — they keep invoking agents

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `RunContext.active_branches` is declared
  (`contexts/workflow/domain/models.py:187`) and assigned once
  (`application/run_engine.py:725`); repo-wide grep finds **no reader**. `_fail_run`
  (`run_engine.py:813-843`) sets `ctx.cancelled` on its own in-process context, updates the
  run row, calls `cancel_pending_for_run` (`infrastructure/repositories.py:432-443` — a DB
  UPDATE of step rows only), and publishes `workflow.run_finished`. No cancellation event,
  no Redis kill switch, nothing a sibling worker process reads. `run_step` checks run state
  once at entry (`_prepare_continuation`, `:241-243`); after that the only guard is
  `ctx.cancelled` at `:546`, which is per-process.
- **Failure scenario**: parallel branches A and B. A's node fails with the default
  `strategy=fail`; `_fail_run` marks the run FAILED. B's `run_workflow_step` job is already
  past the entry state check and inside an `agent_invocation`. B completes its provider
  call **and then keeps walking its entire branch** via `_advance_from` → `_execute_node`,
  inserting step rows and invoking further agents or instructs against an already-FAILED
  run, until it reaches an end node or a park.
- **Blast radius**: the window is the remainder of the branch, not a single call — wider
  than a typical in-flight race. Provider spend on the user's key for work on a dead run,
  plus step rows written against a terminal run.
- **Intent source**: `docs/workflow.schema.md:162` — "`fail`: mark run failed, cancel all
  sibling branches (`parallel` branches honor this by emitting cancellation events)",
  written as normative behavior for an event that does not exist. R14.01.

## F-11: An `any`/`count` join stops advancing on the second pass of a loop

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/executors/join.py:51-55` claims the
  `fired` latch (SET NX, TTL 86400) at `arrivals >= fire_threshold`, but `:56-61` drains
  the arrival set, the latch, and the epoch only at `arrivals >= total_branches`. For ANY,
  `fire_threshold = 1` (`:86-87`) while `total_branches` counts every incoming edge
  (`:79-81`); for COUNT it is `required_count` (`:88-89`). The epoch key is written at
  `:59` and nowhere else, so nothing else can reset it. Cycles through a join are
  constructible: the only cycle rule is `rule_10_instruct_cycle`
  (`linter.py:403-444`, instruct edges only), and general cycles are a documented feature
  (`domain/models.py:204-205` `loop_guard`, `run_engine.py:552-559`).
- **Failure scenario**: `join(mode: any)` with two incoming edges — an entry edge and a
  back-edge from a loop. Pass 1: the entry edge arrives, `arrivals = 1 >= 1`, the latch is
  claimed, the join fires downstream; the drain does not run because `1 < 2`. Pass 2: the
  back-edge arrives, `SET fired NX` fails because the key is still live, `is_finalizer = 0`
  → `skip_edges = True` (`:125-133`) → `_execute_node` returns with no successor
  (`run_engine.py:656-657`). The loop dies after one pass and the run sits RUNNING until
  `workflow_watchdog` force-fails it on `idle_max_seconds`.
- **Blast radius**: any looping workflow using an `any` or `count` join. Not an infinite
  deadlock, but a silent stall with a misleading watchdog failure reason.
- **Intent source**: the module's own contract, `join.py:10-12` — "the epoch is only bumped
  once the fan-in is fully drained … keeping each loop pass isolated."

## F-12: An agent's first wake-up self-modification erases its designer-set soft bounds

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/domain/models.py:173-193` —
  `WakeupConfig.to_dict()` emits exactly `triggers`, `allow_self_open`,
  `refresh_every_hours`; `soft_bounds` is dropped.
  `application/wakeup_service.py:330-331` builds `d = fresh_cfg.to_dict()` from scratch and
  `contexts/agents/application/agent_service.py:609-610` does
  `values["wakeup_config"] = draft.wakeup_config` — a whole-column JSONB replace with no
  merge anywhere in the patch path. `_parse_soft_bounds` (`wakeup_service.py:447-456`) then
  returns empty bounds and `_clamp_n` (`:436-437`) falls back to the hard bound `N_MIN = 1`.
  The bounds *are* writable by a designer: `wakeup_config` is `BoundedConfig`
  (`app/api/v1/agents.py:89,123` → `shared_kernel/validation.py:90`), size-limited but
  otherwise free-form.
- **Failure scenario**: a designer sets `soft_bounds: {n_min: 5, n_max: 10}`. The agent
  calls `update_wakeup(every_n_messages=1)` — correctly clamped to 5, but the persisted
  config now has no `soft_bounds`. A second `update_wakeup(every_n_messages=1)` in the same
  hour lands at 1, unclamped, and emits no `agent.wakeup_clamped` audit because no clamping
  occurred. The agent now wakes on every single message.
- **Blast radius**: bounded by the hourly refresh, which restores from
  `wakeup_authored_snapshot` — the full human-written dict including `soft_bounds`
  (`agent_service.py:614-615`). So the escape lasts at most one sweep interval, not
  permanently. Within that window it is a direct cost lever on the user's own provider key.
- **Intent source**: R15.08; `docs/implement/G-orchestration.md:98`.

## F-13: `workflow_capabilities.can_create_subagent` / `can_instruct` are never checked anywhere

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `contexts/agents/infrastructure/repositories.py:85` loads
  `workflow_capabilities` onto the `Agent` model. Grep for
  `can_create_subagent|can_instruct|can_approve` across `backend/contexts/` returns only
  two **write** sites — `orchestration/domain/models.py:367-369` (the R15.22 inheritance
  table) and `subagent_service.py:278-280` (the child's inherited `run_context`). Zero read
  sites. `subagent_service.spawn:109-140` checks only `parent_instance.parent_id` and the
  concurrency count; `executors/subagent_spawn.py:64-73` and `executors/instruct.py:39-43`
  have no gate. The check is absent at every other layer too:
  `linter.py:793-822` `validate_definition` takes no capabilities parameter, and
  `workflow/interfaces/facade.py:46-57` passes only `valid_agent_ids` / `valid_chatroom_ids`.
- **Failure scenario**: agent X has `workflow_capabilities: {}` — the bootstrap default
  (`app/bootstrap/seed.py:274`). A designer names X as `parent_agent_id` on a
  `subagent_spawn` node, or as `issuer_agent_id` on an `instruct` node. Both succeed. The
  capability an operator set to deny the behavior has no effect.
- **Blast radius**: a policy bypass within the project, not a tenant-boundary bypass. It
  makes the agent-level capability switch cosmetic across the whole orchestration surface.
- **Intent source**: R15.18 (`REQUIREMENTS.md:791`); `docs/implement/G-orchestration.md:250`
  ("instruct + subagent require `workflow_capabilities`").

## F-14: `refresh_every_hours` is parsed, surfaced in the UI, and never used — every agent is reset hourly

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `refresh_every_hours` is written and read only in
  `orchestration/domain/models.py:140,170,192` and the frontend
  (`SWakeupEditor.vue:120`, `types/workflow.ts`); repo-wide grep finds zero consumers in
  `backend/app/workers/` or `wakeup_service.py`. `app/workers/main.py:322` registers
  `cron(wakeup_refresh, minute=0)`; `app/workers/tasks/orchestration.py:282-307` iterates
  every agent holding a snapshot and calls `refresh_wakeup_config` unconditionally;
  `wakeup_service.py:377-428` never reads the field and keeps no last-refresh timestamp.
- **Failure scenario**: a designer sets `refresh_every_hours: 24`, intending a day-long
  self-tuning window, and exposes that value in the editor. At 14:05 the agent calls
  `update_wakeup(silence_minutes=30)` to quiet itself during a long analysis. At 15:00 the
  sweep sees `current != authored` and snaps it back to the authored value. The
  self-modification survives at most 60 minutes rather than 24 hours.
- **Blast radius**: R15.06 self-modification is effectively capped at one hour for every
  agent regardless of configuration. The direction is conservative (over-resetting, not
  under-resetting), and `wakeup_service.py:390-392` early-returns for unmodified agents so
  there is no audit-row churn for the common case.
- **Intent source**: R15.09 (`REQUIREMENTS.md:763` — "The Agent Designer can configure a
  `refresh_every_hours` value. Every T hours…"); `docs/implement/G-orchestration.md:19,108,241`.

## F-15: An instruct that completes at its deadline can be overwritten as TIMEOUT, routing the workflow down its failure branch

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `contexts/orchestration/infrastructure/repositories.py:319-333` —
  `update_state` is a bare `UPDATE ... WHERE id = :id` with no state predicate and no
  version column; `get` at `:305-317` is a plain `select()` with **no** `with_for_update()`.
  `app/workers/tasks/workflow_approvals.py:214-224` reads the state, tests it, writes, and
  commits as four separate statements under READ COMMITTED with no row lock.
  `:160-165` then maps `TIMEOUT` to the `failure` port.
- **Failure scenario**: an instruct is parked and its deadline job fires. The job reads
  `DELIVERED` and passes the terminal-state guard. Between that read and the UPDATE, the
  A2A turn finishes and `a2a_handler.py:134` commits `state='completed'`. The timeout job's
  UPDATE then acquires the lock, sees the new committed row, and overwrites it with
  `TIMEOUT` without error. `workflow_resume_instruct` reads `TIMEOUT` and resumes on the
  **failure** port. A successfully completed instruct routes the workflow down its failure
  branch, and the audit trail holds an `instruct.issued` with no matching completion.
- **Blast radius**: the window is the two round-trips between read and write —
  milliseconds — and requires the turn to commit inside it. The **inverse** race is wider
  and equally unguarded: if the turn is still running at the deadline, the timeout commits
  first and `mark_instruct_completed` then clobbers `TIMEOUT` back to `COMPLETED`. The
  docstring at `workflow_approvals.py:134-135` ("completion and timeout can't disagree") is
  wrong in both directions.
- **Intent source**: R15.15-R15.17, the instruct state machine in
  `docs/implement/G-orchestration.md` (`completed` is terminal).

## F-16: A failed enqueue in `workflow_instruct_timeout` poisons its own retry, leaving the run to the watchdog

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `app/workers/tasks/workflow_approvals.py:207-228` — the guard at
  `:217-222` is a hard `return "noop"` that does not fall through; the order is guard →
  `mark_instruct_timeout` (`:223`) → `db.commit()` (`:224`) → session exit →
  `enqueue_job` (`:226`), with the enqueue after the commit and outside any try/except.
  `app/workers/main.py:252-312` sets no `max_tries`, so arq's default of 5 applies.
  The fallback does not take the author's port: `app/workers/tasks/workflow_watchdog.py:63-76`
  calls `RunEngine.force_fail`, which sets `RunState.FAILED` (`run_engine.py:414-415`).
- **Failure scenario**: the deadline fires, `TIMEOUT` commits, and `enqueue_job` raises on
  a Redis fault. Arq retries the job, which now reads its own committed `TIMEOUT` and
  returns `"noop"` without enqueuing. `wf:instruct:{id}` is never claimed,
  `workflow_resume_instruct` never runs, and the run stays WAITING until the watchdog
  force-fails it. The `failure` edge the author wired is never taken.
- **Blast radius**: narrower than it looks — a partial recovery exists via
  `a2a_handler.py:147-153`, which independently enqueues `workflow_resume_instruct` if the
  target's turn eventually finishes. The unrecoverable case is a target that never responds,
  which is precisely the case timeouts exist for.
- **Intent source**: `docs/workflow.schema.md` §2 (`instruct` → `success`/`failure` ports);
  §5.1 rule 13 (port coverage exists so a run cannot silently stall).

## F-17: The orphaned-sub-agent-root sweep applies its row limit before its eligibility test, so it starves

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `app/workers/tasks/retention.py:505-519` — the `synth` CTE filters only on
  `parent_id IS NULL AND run_context->>'synthetic_root' = 'true'` with `LIMIT 500` at
  `:512` and **no `ORDER BY`**; the `NOT EXISTS (SELECT 1 FROM workflow_runs ...)` orphan
  predicate sits in the outer query at `:516-518`, applied to the already-truncated 500
  rows. `retention_sweep` (`:755-789`) calls each policy exactly once per cron tick
  (`subagent_roots` at `:746`) — no loop, cursor, or offset.
- **Failure scenario**: a deployment accumulates 5000 synthetic roots, 4800 of which belong
  to live runs. Each nightly pass takes an arbitrary unordered 500 — Postgres typically
  returns the same heap-order prefix — filters them, and reaps only the orphans that happen
  to fall inside it. Genuinely orphaned roots past row 500 are never reached. Because these
  rows carry `destroyed_at IS NULL` they are also invisible to `_purge_agent_instances`, so
  the table grows unbounded.
- **Blast radius**: storage and index degradation only; no functional misbehavior. The
  sweep is not inert in general — `workflow/interfaces/facade.py:119-146` does archive and
  delete `workflow_runs` rows, so the orphan predicate does become true.
- **Intent source**: R15.21 (sub-agent rows deleted after 30 days); the function's own
  docstring at `retention.py:494-499`.

---

## F-18: A cancelled turn strands its coalesced trigger, leaving a mid-turn message unanswered

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:589-628` — `run_turn` has no `try/finally`; the
  `_pop_queued_trigger` at `:628` is plain post-loop code reachable only on normal return.
  `_run_locked`'s handler at `:2220` is `except Exception`, which does not catch
  `asyncio.CancelledError`. `_QUEUED_TRIGGER_TTL_S = 3600` (`:245`), and grep finds no
  sweeper for `turn:queued` outside `turn_engine.py:249,253,597,617,628`. The retry escape
  hatch does not apply: a job timeout surfaces as `TimeoutError`, which arq treats as a
  plain failure (`arq/worker.py:623-629`).
- **Failure scenario**: agent A is mid-turn; a user posts message M, and the blocked worker
  marks `turn:queued:A:R`. The turn exceeds `job_timeout=600` (`app/workers/main.py:310`)
  and is cancelled. Line 628 never runs. The mark sits in Redis until either an unrelated
  later trigger for the same (agent, room) pops it — answering M at an arbitrary later time —
  or it expires after an hour and M is answered by nobody, with no audit row and no error
  event.
- **Blast radius**: one message per cancelled turn; no duplicate, no corruption.
- **Intent source**: `docs/implement/K-agent-runtime.md:73`.

## F-19: `pending_notify.requeue` trims the newest entries instead of the oldest

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `orchestration/infrastructure/pending_notify.py:37-38` — `push` is RPUSH +
  `ltrim(key, -50, -1)`, keeping the newest tail. `:82-84` — `requeue` LPUSHes
  `reversed(notes)` to the head, then `ltrim(key, 0, 49)` keeps indices 0-49 from the head,
  discarding the tail, i.e. the newest. This contradicts the docstring's own stated intent
  at `:75` ("the cap still trims oldest-first"). Reachable from
  `turn_engine.py:1649` via `_requeue_notifications`, called from seven sites
  (`:897,:907,:1605,:1687,:2079,:2096,:2243`).
- **Failure scenario**: a turn drains 45 notes; 10 more arrive while it runs; the turn
  fails. Requeue leaves `old1..old45 + n1..n5` and deletes `n6..n10` — the newest, which is
  where a fresh approval-ballot request would sit.
- **Blast radius**: only when restored + concurrently-pushed exceeds 50 in one failed turn;
  below the cap the LTRIM is a no-op.
- **Intent source**: `docs/implement/N-conversation-a2a-fixes.md` APP-1, which already
  records this verbatim and remains unfixed.

## F-20: Consumer loops for deleted agents are never stopped

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `orchestration/application/a2a_consumer.py:238-250` — `_reconcile` only
  creates tasks; there is no branch that cancels or pops `self._loops`. Removal happens
  only in `_stop_all` (`:264-272`), on shutdown. `_discover_agents` (`:252-262`) derives
  membership purely from the existence of `a2a:agent:*` keys, and `run_consumer_loop:145`
  calls `ensure_consumer_group`, which is `xgroup_create(..., mkstream=True)`
  (`a2a_streams.py:58`) — so the key is recreated even if an operator deletes it. Agent
  deletion is soft-delete only (`agent_service.py:644-666`) and does no Redis work;
  `app/workers/tasks/retention.py` contains no Redis reference at all.
- **Failure scenario**: delete an agent. Every worker replica keeps an `a2a-consumer-{id}`
  task doing a 1s-blocking XREADGROUP plus a 30s XAUTOCLAIM against a stream nobody writes
  to, forever. `self._loops` never shrinks.
- **Blast radius**: idle Redis polling and one asyncio task per ever-created agent per
  replica. No correctness impact, and the loop is arguably needed for restore
  (`agent_service.py:684`).
- **Intent source**: `docs/implement/G-orchestration.md:28` ("one consumer per **live**
  agent runtime").

## F-21: `autostop_rounds = 0` silently disables the silence trigger while the worker reads it as 100

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `app/workers/tasks/orchestration.py:111-113` applies
  `effective_limit = autostop_limit if autostop_limit > 0 else sm.autostop_max_default`;
  `wakeup_service.py:224-226` uses the raw `autostop_limit_for(...)`, so `count >= 0` is
  always true and the trigger is permanently suppressed. `domain/models.py:117-121` claims
  the helper is "the single source of truth so the worker gate and the domain evaluator
  can't diverge" — which is exactly what happens. `0` is reachable: `from_dict`
  (`models.py:159-163`) applies only `min(..., 100)` with no lower bound, and the API
  accepts free-form `BoundedConfig`.
- **Failure scenario**: a designer writes `autostop_rounds: 0` via the API believing it
  means "no autostop". Silence sweeps then always return `False` — the agent never wakes on
  silence — while its `every_n_messages` wake-ups run with an effective cap of 100.
- **Blast radius**: reachable only by bypassing the UI, which clamps to
  `[1, autostop_max_default]` (`SWakeupEditor.vue:106-108`). The divergence fails closed.
- **Intent source**: R15.03/R15.04; the single-source-of-truth invariant asserted in
  `models.py:118-120`.

## F-22: A swallowed mark-write failure makes a turn report `skipped/locked` and drop the trigger

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:286-292` — `_mark_trigger_queued` swallows every exception
  and returns `None`, so the caller cannot tell. At `:597-604`, attempt 1 pops and `break`s
  when `parked is None`, on the strength of the comment's unverified assertion that the
  previous holder must already have enqueued a follow-up. The `break` exits the `async with`
  with `result` still `None`, and `:623-625` returns
  `TurnResult(status="skipped", reason="locked")`.
- **Failure scenario**: message M arrives while agent A is busy. Attempt 0 fails to acquire
  and the `redis.set(..., nx=True)` raises transiently — logged and swallowed. The holder
  releases; its own pop finds nothing. Attempt 1 acquires the lock, pops `None`, breaks,
  and reports `skipped/locked`. M is never answered, and the audit records a reason that is
  factually false. The same `break` also fires whenever the previous holder was cancelled
  per F-18, so nothing was actually enqueued.
- **Blast radius**: needs a Redis blip narrow enough that the surrounding `acquire_lock`
  calls still succeed — a raise there propagates out of `run_turn` instead.
- **Intent source**: `docs/implement/K-agent-runtime.md:73`.

## F-23: Silent lock-heartbeat loss can let two turns run concurrently for the same agent and room

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `shared_kernel/realtime/distributed_lock.py:73-75` logs and `continue`s on
  exception, so the next refresh attempt is a full `interval_s` later; `:76-77` returns
  silently when the compare-and-pexpire returns 0. Nothing signals the turn body — `:109`
  yields a bool captured once at entry, with no cancellation token, and
  `contexts/agents/infrastructure/turn_lock.py:23,45-51` passes the defaults through:
  TTL 300 (`:25`), interval 300/3 = 100 (`:104`).
- **Failure scenario**: an 8-round tool turn runs past 300s. Three consecutive refresh
  attempts raise (Redis failover, connection resets), each costing a full 100s. The key
  expires; a queued `wakeup_agent` acquires it and starts a second concurrent turn — two
  provider streams, two `agent.token` sequences into the same client-side draft, two
  persisted replies. The first holder's `release_lock` is correctly token-guarded (Lua at
  `:27-29`) so it will not delete the second holder's key, but it *will* pop the second
  holder's coalesced trigger mark.
- **Blast radius**: requires a >200s Redis degradation that recovers in time for a
  competing worker to acquire, overlapping a turn still running past 300s
  (`job_timeout = 600`). Narrow, but the outcome when it fires is the same duplicate-turn
  damage as F-7.
- **Intent source**: `docs/implement/K-agent-runtime.md:85`.

## F-24: The A2A call chain never survives a process hop, so depth is always 1 and the cycle guard is unreachable

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: `orchestration/application/a2a_call_chain.py:28-31` is a process-local
  `ContextVar` defaulting to `(0, ())`; `a2a_service.py:143` reads it via `next_hop` inside
  `call()`. The sole production caller of `a2a_call` is
  `workflow/application/executors/agent_invocation.py:41`, which runs in the workflow Arq
  worker, while `a2a_handler.py:85` binds the chain in the A2A consumer process. Neither
  `RunContext` nor `trigger_payload` carries `call_depth`/`call_path`
  (`workflow_signals.py:139`, `run_engine.py:178-185`).
- **Failure scenario**: workflow W1 calls agent B (depth 1, path `["B"]`). B's turn triggers
  W2, which calls agent A — but `next_hop` in the workflow worker reads an empty chain and
  returns `(1, ("A",))`. A's turn triggers W1 again, and `A2ACallLoop` never raises because
  the path resets on every hop.
- **Blast radius**: latent. Grep for `a2a_call_chain|A2AService|next_hop` in
  `turn_engine.py` returns no matches and `tool_registry.py` exposes no A2A tool, so no
  agent turn can issue a nested call today — there is currently no in-process nesting to
  detect. Its only live consequence is that it cannot break the F-4 loop. The module
  docstring at `a2a_call_chain.py:8-14` nonetheless claims the chain is threaded
  "regardless of whether the turn issues it via a tool or any other in-task path", which
  holds only in-process.
- **Intent source**: R9.15; the module's own stated contract;
  `docs/implement/G-orchestration.md` exit criterion "A→B→A rejected".

## F-25: Instruct chain identity is never propagated, so three of R15.16's four guards are unreachable

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: `instruct_service.py:57-58,65-66` accepts `chain_id`/`parent_path` and
  `:146-151` stamps `chain_id`/`path`/`depth` into the envelope, but
  `a2a_handler.py:116-142` reads only `payload["instruction_id"]` — the chain fields are
  dropped and no chain context is bound around `_run_turn_with_db` at `:130`. The sole
  production caller, `executors/instruct.py:39-43`, passes neither, so every `issue()`
  mints a fresh `chain_id` with `parent_path = ()`. `app/api/v1/orchestration.py:265,287`
  only reads instructions; no agent tool issues them.
- **Failure scenario**: rule 1 (`target in new_path`) can only ever catch the degenerate
  self-instruct A→A — A→B→A is structurally undetectable. Rule 2 (`depth >= 5`) can never
  fire because `depth` is always 1. Rule 4's wall-clock budget calls
  `get_chain_start_time(chain_id)` with a chain minted three lines earlier
  (`instruct_service.py:118`), so elapsed time is always ~0.
- **Blast radius**: latent. `linter.py:407-449` (`rule_10_instruct_cycle`) statically
  rejects cycles in a workflow's issuer→target graph at save time, and since no agent tool
  issues instructs, multi-hop chains never form at runtime. The guards have nothing to fire
  on rather than failing to fire on something dangerous. Note the unit tests at
  `tests/unit/test_orchestration_services.py:461,472` call `issue()` with a hand-constructed
  `parent_path`, which masks the wiring gap.
- **Intent source**: R15.16 rules 1/2/4;
  `docs/implement/G-orchestration.md:166-168,176`.

## F-26: `max_instructions_per_wakeup` (R15.16 rule 3) can never fire

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: the rule is gated on `if wakeup_started_at:`
  (`instruct_service.py:107-115`); the default is `None` at `:59` and
  `interfaces/facade.py:249`; `executors/instruct.py:39-43` never passes it and no other
  production caller exists. `count_issued_by_agent_since` has no other call site, and grep
  for `max_instructions_per_wakeup` outside `instruct_service.py`, `models.py`, and
  `facade.py` finds no alternative counter.
- **Failure scenario**: a workflow with a loop body containing an `instruct` node iterating
  500 times issues 500 instructs from one agent against a documented cap of 5, with no
  error and no audit of the breach.
- **Blast radius**: latent for the same reason as F-25 — no wake-up path issues instructs
  today. `tests/unit/test_orchestration_services.py:487` passes `wakeup_started_at`
  explicitly, so the rule is unit-covered but never reached.
- **Intent source**: R15.16 rule 3.

## F-27: Sub-agent inheritance restrictions (R15.22) are written to `run_context` and read by nothing

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: `subagent_service.py:257-283` writes `a2a_enabled: False`,
  `can_instruct: False`, `can_create_subagent: False`, `can_approve: False`,
  `rag_config_id: None`, `wakeup_config: None`, and the child row is inserted with
  `agent_id = parent_agent_id` (`:150-157`). Grep for `run_context` across non-test backend
  code returns reads of only two keys — `synthetic_root` / `workflow_run_id` in
  `retention.py:508-511` and `infrastructure/repositories.py:465,494` — plus a pass-through
  to the API response at `app/api/v1/orchestration.py:194`. Every runtime gate reads the
  parent `Agent` row instead: `a2a_scope.py:93-96`, `builtin_tools.py:416,427`.
  `graphrag_config_id`, which `REQUIREMENTS.md:806` requires to be forced null, is missing
  from the dict entirely.
- **Failure scenario**: none today — grep for `instance_id|agent_instance` across
  `backend/contexts/agents/` returns zero matches, so the runtime has no concept of an
  instance and no sub-agent turn is executable (see F-1). The containment is not
  "bypassable", it is unbuilt on both sides.
- **Blast radius**: a spec-conformance gap to close as part of wiring sub-agent execution.
  `tests/unit/test_orchestration_services.py:742` already records the condition
  ("SUBAGENT_INHERITANCE is read by nothing at runtime — it documents R15.22").
- **Intent source**: R15.22.

## F-28: `workflow_subagent_complete` discards the resume result while its claim key is already deleted

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: `run_engine.py:336-341` states the contract verbatim — "Callers that
  claimed a single-shot resume token … MUST restore the claim and retry later on a `False`
  + non-terminal run, or the wait is lost." `workflow_approvals.py:182-196` (instruct) and
  `:87-103` (approval) comply; `app/workers/tasks/workflow_steps.py:121` discards the
  `bool` entirely, and `subagent_service.py:250-251` enqueues then unconditionally deletes
  `wf:subagent_callback:{id}`. `_emit_resumed` is also never called here, so the
  `workflow.resumed` audit every other resume path emits is missing.
- **Failure scenario**: a run fans out to two parked branches. Branch A resumes first,
  flipping the run to RUNNING. Branch B's subagent completes 200ms later;
  `resume_at_port` returns `False` because the run is no longer WAITING; the callback key
  is already gone and no retry is scheduled. Branch B is permanently parked until its 1h
  timeout force-fails an otherwise-successful run.
- **Blast radius**: latent — `destroy_subagent` has no production caller (F-1), so
  `_fire_workflow_callback` and therefore this task never run today. Fix as a precondition
  of wiring the destroy path.
- **Intent source**: `run_engine.resume_at_port`'s documented claim-before-verify contract;
  `docs/implement/H-workflow.md` W10.

## F-29: The sub-agent park timeout is not scoped to the node it was armed for

- **Severity**: minor (latent)
- **Verdict**: confirmed
- **Evidence**: `app/workers/tasks/workflow_steps.py:91-94` guards only on
  `run.state != RunState.WAITING` and then calls `force_fail(run_id, ...)`;
  `run_engine.force_fail` (`:402-416`) takes only `run_id` and re-checks only
  `state in (RUNNING, WAITING)`. No park bookkeeping exists to check against:
  `workflow/infrastructure/tables.py:82-100` has no parked-node column, and the docstring at
  `run_engine.py:638-642` concedes the engine "can only observe *this run is WAITING*, not
  which parked node it is waiting on". The deferred job is enqueued with a trailing `None`
  job_id (`:649-652`), so it cannot be cancelled. Contrast `workflow_event_timeout`
  (`workflow_signals.py:27-39`), which claims the node-scoped key
  `wf:wait:{run_id}:{node_id}` with GETDEL and therefore self-cancels when stale.
- **Failure scenario**: node A parks at t=0 arming a 3600s timeout; the park resolves at
  t=100; the run advances and later parks on an unrelated `wait_for_event` node. At t=3600
  the stale job sees WAITING and force-fails a healthy run with reason
  `subagent_timeout (node A)`.
- **Blast radius**: latent and coupled to F-28 — a subagent park never resolves early
  today, so force-failing at t=3600 is currently the correct outcome. Becomes live the
  moment the destroy path is wired.
- **Intent source**: `docs/implement/H-workflow.md` W10.

## F-30: `SubagentService.destroy` is not idempotent

- **Severity**: minor (latent)
- **Verdict**: plausible
- **Evidence**: `subagent_service.py:188-215` has no check on `instance.destroyed_at`, and
  `infrastructure/repositories.py:518-523` unconditionally re-stamps it.
  `_fire_workflow_callback` uses a non-atomic `redis.get` (`:235`) then `redis.delete`
  (`:251`) rather than the `getdel` the instruct and event paths use
  (`workflow_approvals.py:78,168`, `workflow_signals.py:59,265`).
- **Failure scenario**: two concurrent `destroy` calls both read the callback key before
  either deletes it, both enqueue `workflow_subagent_complete`, the gauge
  `SUBAGENT_CONCURRENCY.dec()` runs twice and goes negative, and the second call resets
  `destroyed_at`, pushing the row 30 days further out of the purge window.
- **Blast radius**: the claimed double-advance of the workflow is **refuted** —
  `run_engine.py:345-347,365` makes the second `resume_at_port` a no-op on a non-WAITING
  run. Residual damage is a negative gauge and a delayed purge. Marked plausible rather
  than confirmed because the path has no production caller today (F-1), so the concurrent
  window could not be traced end to end.
- **Intent source**: R15.21 (single, final teardown).

## F-31: The approval-gate timeout is armed before the gate row commits and never retries

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `approval_service.py:162-167` enqueues `approval_timeout` with
  `_defer_by=timedelta(seconds=config.timeout_seconds)` from inside the caller's
  uncommitted transaction; `:246-248` returns `None` when the row is not found and
  `app/workers/tasks/orchestration.py:200-204` maps that to `"noop:gone"` with no retry and
  no re-arm. The sibling job `app/workers/tasks/approvals.py:59-76` retries the identical
  not-yet-committed condition 5×2s — the inconsistency is real.
  `domain/models.py:290-292` allows `timeout_seconds` from 1, and `linter.py:756-762` only
  warns above 3600, so there is no floor.
- **Failure scenario**: a gate declares `timeout_seconds: 2` (lint-clean, R15.10-legal).
  The timeout job fires before the executor's transaction commits, finds no row, and exits
  permanently. If no approver votes, the gate stays `pending` with no timeout backstop.
- **Blast radius**: two corrections cut this down. The executor's commit lands within
  milliseconds (`approval_service.py:176-178`), so the race needs a 1-2s timeout *and* an
  anomalously slow commit. And "permanently wedged" is refuted:
  `app/workers/tasks/workflow_watchdog.py:14-85` force-fails any active run past
  `idle_max_seconds` (default 1800). Worst case is a force-failed run.
- **Intent source**: `docs/implement/G-orchestration.md:261` (`timeout_leader` guarantees
  forward progress); R15.10.

## F-32: The approval resume claim key can expire while its own retry budget is still running

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `executors/approval_gate.py:87-91` sets `ex = int(timeout_seconds) + 300`
  at gate creation, so the 300s grace is measured from creation, not resolution.
  `app/workers/tasks/workflow_approvals.py:28-29` gives a retry budget of 3s × 210 ≈ 630s.
  `workflow_common.py:43-45` `_restore_claim` writes `ex=ttl if ttl and ttl > 0 else 60` —
  the decayed TTL read at `workflow_approvals.py:77`, never refreshed, with
  `_CLAIM_RESTORE_TTL_S = 60` as a worse fallback if the read missed. Once the key expires,
  `:51-52` returns `"noop:no_claim"` and the chain ends.
- **Failure scenario**: a gate resolves at t≈`timeout_seconds`, leaving 300s of key life
  against a 630s budget. If `resume_at_port` keeps returning falsy — a sibling branch
  holding the run in RUNNING — the key expires mid-retry and nothing ever resumes the
  approval node.
- **Blast radius**: thin. The 630s budget was sized for a vote sitting uncommitted inside a
  long turn, but `approval_service.py:226` commits the vote immediately, so the retry
  normally resolves on attempt 0-1. Burning 300s of retries is pathological, and the
  watchdog force-fails the run rather than leaving it parked forever.
- **Intent source**: the code's own comment at `approval_gate.py:88-91` ("TTL outlives the
  gate timeout plus a grace window so a late resolution can still claim it").

## F-33: `dispatch_enqueues` clears its pending list before enqueuing, so a mid-loop failure drops branches

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `run_engine.py:497-500` snapshots `_pending_enqueues` and clears it; the
  loop at `:509-524` has no per-item try and no re-queue on failure. Callers commit first
  by contract (`app/workers/tasks/workflow_steps.py:39-41`).
- **Failure scenario**: a `parallel` node appends three `run_workflow_step` entries; the
  caller commits, making the run and step rows durable; `enqueue_job` raises for branch 2.
  Branches 2 and 3 are gone from the cleared list. On the arq-driven path the exception
  propagates and the retry re-executes the parallel node, re-appending all branch enqueues
  — but that re-executes already-enqueued branches, duplicating agent invocations and
  instructs. On the API entry path (`dispatch_pending` with no arq wrapper) there is no
  retry at all and the branches are lost outright.
- **Blast radius**: the claimed join-accounting corruption is **refuted** — the join's
  `SADD` (`join.py:47`) is keyed on the same edge ids in the same epoch (which cannot have
  advanced, since draining needs all branches), so re-arrival is a no-op and a `join(all)`
  still completes. The residual defects are duplicated side effects on the retry path and
  outright branch loss on the API path.
- **Intent source**: the DB-1 contract at `run_engine.py:484-495`.

## F-34: `workflow_cron_scheduler` writes its debounce marker after committing the run

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `app/workers/tasks/workflow_cron.py:88` `trigger_run`, `:92` `commit`,
  `:93` `dispatch_pending`, `:94` `redis.set(last_fire)`. The `except` at `:96-103` calls
  `db.rollback()`, which cannot undo the commit at `:92`. The eligibility gate reads only
  `wf:cron:{id}:last_fire` (`:64-70`) and falls back to `now - 1min` when it is absent
  (`:80`) — no DB column, no query against the runs table. Amplified by `:110-113`: if
  every eligible workflow fails, the task raises and arq retries the whole pass
  immediately, still with no `last_fire`.
- **Failure scenario**: the cron is due; `trigger_run` commits; `dispatch_pending` raises on
  a Redis hiccup; `last_fire` is never set. The committed run has no dispatched entry node
  and sits RUNNING until the watchdog, and 60 seconds later the next pass fires a second
  run of the same workflow. Repeats while Redis writes keep failing.
- **Blast radius**: narrow — `redis.get` at `:65` is outside the try, so a hard Redis outage
  aborts the pass before firing anything. The bug needs a transient failure landing exactly
  in the `dispatch_pending`/`redis.set` window.
- **Intent source**: the module's own docstring at `:52-63` ("each trigger fires at most
  ONCE per pass … never one run per missed tick"); `docs/implement/H-workflow.md` H.4.

## F-35: `run_triggered_workflow` swallows every start failure with no retry and no audit

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `app/workers/tasks/workflow_signals.py:319-323` —
  `except Exception: logger.exception(...); return "error"`. A normal return is a successful
  arq job, so there is no retry and no DLQ path. `dispatch_pending` at `:325` sits outside
  the try, so only *start* failures are swallowed. No audit row and no metric is emitted
  before the swallow, and no comment states the intent.
- **Failure scenario**: a message lands in a watched room and matches a `message_received`
  trigger. `trigger_run` raises — a run-start linter failure, or a transient asyncpg
  disconnect. No run row, no audit event, no retry. To the user the message simply did not
  trigger the workflow.
- **Blast radius**: smaller than first reported — the failure *is* observable, via a bound
  stack-traced error log (`logger.bind(workflow_id=...).exception(...)`). What is missing is
  the retry and the audit row, not all visibility.
- **Intent source**: R14.07; `docs/implement/H-workflow.md` K.4.

## F-36: `join`'s documented `timeout` port is unreachable

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `executors/join.py` has exactly two return paths — `:129-133`
  (`skip_edges`) and `:136-140` (`port="default"`). `park` is never set, so
  `run_engine.py:647-653` — the only site that arms a timeout task — never arms one for a
  join. Repo-wide grep finds no other join handling: no cron, no watchdog branch, nothing
  in the `workflow_*.py` tasks. Yet `linter.py:42` allows the port and
  `docs/workflow.schema.md:29,45` documents it.
- **Failure scenario**: an author follows the documented port table and wires `join`'s
  `timeout` port to a compensation node. The definition validates. The edge is dead — if a
  branch never arrives, the run stalls until `workflow_watchdog` force-fails it on
  `idle_max_seconds`, and the compensation branch never runs.
- **Blast radius**: a never-taken branch and a misleading linter pass.
- **Intent source**: `docs/workflow.schema.md` §2/§2.1 port tables.

## F-37: `find_matching_waits` prunes the by-event index during another task's claim window

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `event_dispatch.py:158-161` SREMs the index member whenever
  `wf:wait:{run}:{node}` reads `None` — which is exactly the state during the gap between
  `workflow_signals.py:265` (`getdel`) and `:277` (`_restore_claim`), and the identical gap
  at `:59`→`:75` in `workflow_event_timeout`. `workflow_common.py:43-45` `_restore_claim` is
  a single `redis.set` that never re-SADDs `wf:wait:by_event:{event_type}`; the member is
  added only at `executors/wait_for_event.py:79`, nowhere else.
- **Failure scenario**: a branch parks on `message_in_room` while a sibling holds the run in
  RUNNING. Message M1 arrives; `workflow_event_resume` GETDELs the claim and begins its
  terminal check. Message M2 arrives concurrently, reads `None`, and SREMs the index member.
  The first task restores the claim key but not the index entry. Message M3 — the one the
  author wanted to match — is never seen, and the branch can only exit via its `timeout`
  port.
- **Blast radius**: the window spans a DB session open, `resume_at_port`, a commit, and
  `_run_is_terminal` — tens of milliseconds under load. Impact is narrower than it sounds:
  the retry loop keeps chasing the *original* event, so orphaning bites only for a
  subsequent event after the retries give up.
- **Intent source**: the ASYNC-10 dispatcher contract at `executors/wait_for_event.py:7-15`;
  `event_dispatch.py:145-148` explicitly claims stale members are "pruned by the resume
  job's miss" — but the restore path does not re-index.

## F-38: Wake-up counters can lose their TTL

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `orchestration/infrastructure/wakeup_state.py:88-89` and `:167-168` issue
  `INCR` then `EXPIRE` as two separate awaits — no pipeline, no `MULTI`, no `SET ... EX`
  equivalent — against the TTL promise at `:23`. `reset_message_count` (`:93-97`) is dead
  code: repo-wide grep returns only the definition and its `__all__` entry.
- **Failure scenario**: a connection drops after `INCR` returns but before `EXPIRE` is sent.
  `wakeup:msg_count:{A}:{R}` becomes persistent and survives room deletion indefinitely.
- **Blast radius**: partially self-healing — any subsequent increment on the same key
  re-issues `EXPIRE`, so active rooms recover. The immortal key is real only for keys never
  touched again, which is exactly the abandoned-room case. The counter arithmetic itself is
  safe: `INCR` is atomic and the modulo test consumes its return value, so concurrent
  inserts cannot both observe the same multiple of N.
- **Intent source**: `wakeup_state.py:23`. No requirement-level source.

## F-39: `_pop_queued_trigger` uses two unpipelined GETDELs, so a race can split the trigger from its message id

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:304-305` — two separate `getdel` round-trips with no
  MULTI/Lua. This does **not** depend on the lock failing: the two-popper race is a
  designed-for condition, since turn A's post-release pop (`:628`) can interleave with turn
  B's attempt-1 pop (`:597`), exactly as the comment at `:598-604` anticipates.
- **Failure scenario**: A pops the trigger, B pops the trigger key as `None`, B pops the
  message id, A pops the message id as `None`. A enqueues its follow-up with
  `message_id=None`, and `_resolve_trigger_attachments` (`:995-997`) falls back to
  `latest_user_attachments(chatroom_id)` — attributing whatever attachment is newest in the
  room to the reply.
- **Blast radius**: mild. The fallback is the same degraded mode `silence_minutes` uses
  deliberately (`app/workers/tasks/orchestration.py:41-43`), so it misattributes only when
  a newer attachment-bearing message exists.
- **Intent source**: internal inconsistency.

## F-40: Multi-round tool turns stream text that is then discarded

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `turn_engine.py:2673-2679` emits `agent.token` for every `TokenDelta`,
  inside the `for rounds in range(1, MAX_TOOL_ROUNDS + 1)` loop at `:2651` — unconditional,
  not gated to the final round. `:2683` `last_text = str(body.get("text", ""))` is
  overwritten each round; the loop returns it at `:2686` only when there are no tool calls,
  and the post-loop path returns `final_body["text"]` at `:2758`. That single value is what
  `MessageService.send_agent` persists at `:2181`. The frontend renders every token into a
  per-agent draft with no per-round reset
  (`slices/conversation/composables/useChatroomSocket.ts:237-241`,
  `useAgentStreams.ts:14-38`) and clears the draft on `agent.finished` (`:243`), replacing
  it with the refetched row — pinned by `__tests__/useChatroomSocket.test.ts:138-148`.
- **Failure scenario**: an agent does a 3-round tool turn. The user watches the round-1 and
  round-2 preambles appear, then the round-3 answer; on `agent.finished` everything but the
  round-3 answer silently vanishes.
- **Blast radius**: cosmetic. The intermediate text is not lost from the model's reasoning —
  it is folded into the final call's context (`:2688`, `:2720-2728`). This reads as thinking
  text deliberately not persisted, but the streaming behavior does not match that intent.
  Belongs to the agent-to-user dossier's territory; recorded here because it surfaced during
  the concurrency sweep.
- **Intent source**: internal inconsistency between the streaming and persistence paths.

## F-41: `workflow_event_timeout` omits the `workflow.resumed` audit every other resume path emits

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `workflow_event_resume` calls `_emit_resumed(..., reason="event")` at
  `app/workers/tasks/workflow_signals.py:290`; `workflow_resume_approval` at
  `workflow_approvals.py:104`; `workflow_resume_instruct` at `:197`.
  `workflow_event_timeout` (`workflow_signals.py:27-107`) has none on its success path —
  the only record is `logger.bind(event="workflow_event_timed_out")` at `:104-106`, a log,
  not an audit row.
- **Failure scenario**: a `wait_for_event` branch resumes on its `timeout` port and advances
  downstream. An operator reconstructing why the compensation branch ran finds no audit row
  for the resume.
- **Blast radius**: audit completeness only. Arguably deliberate (a timeout is a lapse, not
  a resume), but nothing says so, and instruct timeouts *are* audited because they route
  through `workflow_resume_instruct` — which makes the omission look accidental.
- **Intent source**: `workflow_common.py:48-49` (cross-cutting checklist item 2); R14.10.

## F-42: `SubagentService.cleanup_expired` is dead code whose retention window disagrees with the live sweep

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `subagent_service.py:320-321` ← `interfaces/facade.py:347-348`
  `cleanup_expired_instances` ← nothing. Repo-wide grep yields only those definitions plus
  `tests/unit/test_orchestration_services.py:801-806`. The divergence is real:
  `infrastructure/repositories.py:569` truncates to midnight before subtracting the days,
  so its cutoff is up to 24h earlier than `retention.py:472`'s
  `now() - timedelta(days=30)`; it is unbatched (no LIMIT) unlike
  `_purge_agent_instances`; and `:577` returns bare `result.rowcount` with no `or 0`, unlike
  `retention.py:483`.
- **Failure scenario**: none today. If a future caller wires it into a cron, it silently
  applies a different retention window, and `retention_sweep`'s
  `total = sum(v for v in report.values() if v > 0)` would treat a `-1` rowcount as a policy
  failure marker.
- **Blast radius**: none while uncalled. The right disposition is deletion (together with
  the facade wrapper and its test) rather than a fix — `_purge_agent_instances` already owns
  this policy.
- **Intent source**: R15.21 (one 30-day rule).

## 4. Refuted Candidates

Kept because each refutation is itself informative — these are the false positives most
likely to be re-reported by the next sweep of this area.

- **Instruct budget exhaustion does not abort the root run.** Refuted. The claim assumed
  `StepOutcome(state=FAILED, port="failure")` follows the failure port, but
  `run_engine.py:602-610` routes any FAILED outcome through `_apply_on_error`, and the
  default strategy is `fail` (`:68`), which falls through to `_fail_run` at `:689-692`.
  `InstructBudgetExceeded` *does* abort the run; the failure port is taken only when the
  author explicitly opts out with `on_error.strategy: continue`.
- **`find_triggered_workflows` has no tenant filter, so a workflow can be triggered by
  another tenant's A2A traffic.** Refuted — two finders disagreed and the verifier settled
  it. The query at `event_dispatch.py:210` really does scan every live workflow, but
  isolation is enforced at *save* time: `linter.py:322-338` (rule 6) iterates **every**
  node with no type filter, and `_collect_agent_ids` (`:111-126`) reads exactly the
  `config["agent_id"]` key that `matches_a2a_trigger` compares. Both write paths
  (`app/api/v1/workflows.py:383-389,420-427`) supply project-scoped valid-id sets, and
  `valid_agent_ids` defaults to `frozenset()` (`workflow_service.py:130,173`), which fails
  *closed*. There is no import, duplicate, or definition-bearing restore endpoint. Worth
  defence-in-depth, not a defect.
- **`broadcast:workspace` fans out to the project rather than the workspace.** Refuted.
  `REQUIREMENTS.md:439` only defines the literal as a JSON value; grep finds no requirement
  scoping the fan-out at all. The one scoping rule that exists, R9.17.1, says "both agents
  live in the same **Project**" — which is precisely what
  `a2a_service.py:303` implements. Also unreachable: no production caller constructs such
  an envelope.
- **The instruct depth check is off by one against R15.16 rule 2.** Refuted.
  `REQUIREMENTS.md:781-784` never states whether `path` is evaluated before or after
  appending the current issuer and gives no worked example. Including the issuer in "agents
  traversed so far" is a defensible reading, and the persisted `path` is the same value the
  next hop receives as `parent_path`, so the recursion is self-consistent. Ambiguity, not a
  defect.
- **A2A re-delivery resurrects a terminal instruction back to `delivered`.** Refuted as
  stated. The processed-marker check at `a2a_consumer.py:299-302` short-circuits to a bare
  `xack` *before* calling the handler, and the marker is written **before** the ACK at
  `:326-327`, not after — so the window the claim posits is closed. (Note this is the same
  marker that does *not* help F-5, where the entry is stolen while the first handler is
  still running.) The residual is a crash-only window between the DB commit at
  `a2a_handler.py:142` and the marker write.
- **A vote committed after resolution flips the workflow port.** Refuted. `_approval_port`
  (`workflow_approvals.py:114-126`) branches on `approval.state` **first** — `APPROVED →
  "approved"`, `REJECTED → "rejected"` — without consulting votes at all. Only
  `TIMEOUT_LEADER` consults live votes, which is the documented, test-pinned design
  (`tests/unit/test_workflow_k4.py:374-382`) and is exactly what R15.13 says should happen.
- **MAJORITY fails to resolve when approval is already arithmetically unreachable.**
  Refuted — the arithmetic in the claim was wrong. With 4 approvers and 2 rejections, if
  both silent approvers approve, control reaches the leader tie-break at
  `approval_service.py:406-408`, which can still return APPROVED. R15.12 ("ties are broken
  by the leader") mandates no early resolution.
- **`cast_vote` commits the turn's session, defeating the turn's rollback.** Refuted. The
  layering smell is real (`approval_service.py:226` commits a session it does not own), but
  the harm does not exist on either path: the room path explicitly commits all pre-stream
  writes at `turn_engine.py:2099-2104` *before* streaming, and the headless path
  (`run_input_turn`) persists no reply and builds no compaction summary. The only work made
  durable is a `turn_started` audit row and real usage/billing events.
- **Compaction can never fold prior summaries, so long-lived rooms reach permanent
  overflow.** Refuted. `load_model_history` reads a newest-500-**rows** window
  (`transcript.py:43,142,146`), and compacted originals still occupy window slots ahead of
  their summary — they are filtered out only afterwards at `:161-165`. Old summaries
  therefore age out of the window rather than accumulating. (The real issue in that code is
  the inverse: summaries silently falling out of the window is *context loss*, recorded as
  FU-2.)
- **A cancelled turn leaves the room stuck in `agent.thinking`.** Refuted. The backend facts
  hold, but the frontend watchdog is purpose-built for exactly this case —
  `slices/conversation/composables/useChatroomSocket.ts:21-24` documents it,
  `AGENT_THINKING_TIMEOUT_MS = 120_000` is armed on `agent.thinking` and re-armed on every
  `agent.token`, and it surfaces a timeout error. Pinned by tests at
  `__tests__/useChatroomSocket.test.ts:215-235`.
- **A coalesced `@mention` can be dropped by autostop.** Refuted. The SETNX mechanism is
  real (`turn_engine.py:274-279`), but posting the mention calls
  `evaluate_message_wakeups(..., sender_is_user=True)` (`app/api/v1/messages.py:270`) which
  hits `wakeup_service.py:99-100` `reset_autostop`, so the counter is 0 when the follow-up
  runs. The one path that skips the reset (`wakeup_service.py:94`) can only ever park
  `mention`/`release`, both already exempt at `orchestration.py:113`. Residual impact is a
  wrong `trigger` value in the audit row.
- **The sub-agent concurrency cap should be per agent, not per workflow run.** Refuted.
  `REQUIREMENTS.md:793` says the cap is "configurable per parent agent", which states where
  the *setting* lives, not the counting domain; per-instance counting is the natural reading
  of R15.19/R15.21 and is documented as deliberate at `subagent_service.py:60-72`. (A real
  but different gap surfaced here and is recorded as FU-1.)
- **The sub-agent callback Redis key is written before the spawning transaction commits.**
  Refuted as a defect. The ordering is as described, but the orphan is inert: the key is
  named for a `uuid4` instance id that, after rollback, exists nowhere, and its only reader
  is `_fire_workflow_callback` on an existing row. Unreadable garbage with a TTL.
- **`run_input_turn` passes `None` as the room when draining notifications.** Refuted. The
  `None` at `turn_engine.py:716` is real, but a `released_observation` can only target
  NORMAL-role bindings of that same room (`observation_service.py:127-137`), so the
  scenario's headless non-member target cannot exist. The claimed decay loop is also wrong:
  `pending_notify.requeue` re-`expire`s to 86400 on every requeue, so the note survives
  intact for the agent's next room turn. The `None` is the documented fail-closed design.
- **CONSENSUS resolves on first divergence with no debate round.** Reclassified, not a
  bugfix. The state-labelling half is refuted —
  `docs/implement/G-orchestration.md:140,147` names `timeout_leader` as the spec's own state
  for the non-converged outcome, and `tests/unit/test_orchestration_services.py:253-256`
  pins it. The genuine gap is that no propose/debate/converge loop exists at all
  (R15.13 verbatim), with no re-notification path — an unrecorded architectural limitation
  rather than a defect in the resolution logic. Recorded as FU-3.

## 5. Hand-off

Per the dossier contract, this section links the task slugs this audit spawned. A finding
with no dossier and no explicit decision to skip it is an unfinished triage.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | pending triage | |
| F-2 | pending triage | |
| F-3 | pending triage | |
| F-4 | pending triage | |
| F-5 | pending triage | |
| F-6 | pending triage | |
| F-7 | pending triage | |
| F-8 | pending triage | |
| F-9 | pending triage | |
| F-10 | pending triage | |
| F-11 | pending triage | |
| F-12 | pending triage | |
| F-13 | pending triage | |
| F-14 | pending triage | |
| F-15 | pending triage | |
| F-16 | pending triage | |
| F-17 | pending triage | |
| F-18 | pending triage | |
| F-19 | pending triage | |
| F-20 | pending triage | |
| F-21 | pending triage | |
| F-22 | pending triage | |
| F-23 | pending triage | |
| F-24 | pending triage | |
| F-25 | pending triage | |
| F-26 | pending triage | |
| F-27 | pending triage | |
| F-28 | pending triage | |
| F-29 | pending triage | |
| F-30 | pending triage | |
| F-31 | pending triage | |
| F-32 | pending triage | |
| F-33 | pending triage | |
| F-34 | pending triage | |
| F-35 | pending triage | |
| F-36 | pending triage | |
| F-37 | pending triage | |
| F-38 | pending triage | |
| F-39 | pending triage | |
| F-40 | pending triage | |
| F-41 | pending triage | |
| F-42 | pending triage | |

## 6. Out-of-scope Observations

- **FU-1** — `max_alive_simultaneously` is sourced from the workflow node config
  (`executors/subagent_spawn.py:45,72`), never from an agent column. R15.20's "configurable
  per parent agent" therefore has no implementation at all — distinct from the refuted
  per-run-scoping claim. Route to `/spec` alongside F-1 when sub-agent execution is wired.
- **FU-2** — compaction summaries silently fall out of the newest-500-row history window
  (`transcript.py:43,142,146`), so a long-lived room progressively loses its oldest
  compacted context with no signal. This is context *loss*, not overflow; it belongs to a
  knowledge/context audit rather than this one.
- **FU-3** — no propose/debate/converge round exists for CONSENSUS approval gates, and
  approver notifications are one-shot (`approval_service.py:179-185,420-422`). R15.13
  describes behavior the architecture does not currently support. This needs a design
  decision (amend R15.13, or build a re-notification path), not a bugfix — route to `/spec`.
- **FU-4** — `WorkflowService.dry_run` calls `self.validate(defn)` with no
  `valid_agent_ids`/`valid_chatroom_ids` (`workflow_service.py:349`), and those default to
  a fail-closed `frozenset()`, so rule 6 flags every referenced agent. Dry-run appears to
  fail for any non-trivial workflow. A correctness bug, but in the authoring surface rather
  than agent runtime — route to `/spec` as its own bugfix.
- **FU-5** — `turn_engine.py` is 2772 lines and `docker_runsc.py` 1659. Both exceeded what
  this audit could read with uniform attention (the sandbox file was not read at all).
  Route to `check-quality` for a structural assessment, and to `check-security` for the
  sandbox.
- **FU-6** — the A2A CALL path (`run_input_turn`) takes no turn lock at all, unlike the
  room path. F-5 is one consequence, but the broader question — whether a headless turn
  should serialize per agent — is a design question rather than a defect.
