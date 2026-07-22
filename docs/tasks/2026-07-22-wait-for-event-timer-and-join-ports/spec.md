---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R14.02, R14.03, R14.05]
depends_on: []
---

# `wait_for_event` timer waits never fire, and `join`'s documented `timeout` port has no producer

## 1. Summary

Two node contracts in the workflow editor promise behavior that no backend code produces.
(a) A `wait_for_event` node configured `event_type: "timer"` — the editor's seeded default
for every new wait node (`frontend/src/slices/workflow/constants.ts:10`) — parks forever.
The executor reads `timeout_seconds` and never `delay_seconds`
(`backend/contexts/workflow/application/executors/wait_for_event.py:44-45`), arms only the
timeout task (`:94-101`), and no dispatcher produces a timer event
(`app/workers/tasks/workflow_signals.py:143-210` handles message / a2a / wakeup / activity
only). The author's intended delay elapses invisibly; at `timeout_seconds` the branch
resumes at the `timeout` port, which seals the step `failed`
(`run_engine.py:381`), and if that port is unwired the branch dies with no successor
(`run_engine.py:716-717`) until the watchdog force-fails the run
(`workflow_watchdog.py:68-72`). (b) A `join` node's `timeout` port is documented
(`docs/workflow.schema.md:29,45`), permitted by the linter (`linter.py:42`), rendered as a
canvas handle (`frontend/.../WorkflowNodeComponent.vue:71-73`), and fed by a config field
the editor seeds and collects (`constants.ts:12`, `JoinConfigForm.vue:63-76`,
`docs/workflow.schema.json:411`) — but `join.py` has two return paths
(`:129-133`, `:136-140`), neither sets `park`, and `run_engine.py:647-653` is the only site
that arms a timeout task. The port is unreachable and the config field is read by nobody
(`join.py:74-76` reads `mode` and `count` only).

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-2 (major) and
F-36 (minor); hand-off row at `findings.md:1175`.

## 2. Observed vs Expected

### F-2 — timer waits

**Observed.**

- The executor's only branch on `event_type` is the default value it reads
  (`wait_for_event.py:44`); every event type takes the identical path. `delay_seconds` is
  never read — repo-wide grep finds it in `docs/workflow.schema.json:371`,
  `docs/UI/08-workflow.md:497`, `frontend/src/slices/workflow/constants.ts:10`, and
  `frontend/src/slices/workflow/components/config/WaitForEventConfigForm.vue:175,179`, and
  in no backend file.
- The single armed task is `workflow_event_timeout` (`wait_for_event.py:36,98-101`),
  dispatched through the park branch at `run_engine.py:647-653`. It resumes at the
  `timeout` port (`workflow_signals.py:70`).
- Nothing enqueues `workflow_event_resume` for a timer. `workflow_signal` branches on
  `source` ∈ {`message`, `a2a`, `wakeup`, `activity`} (`workflow_signals.py:143,165,182,192`)
  and `workflow_variable_signal` covers `variable_matches` (`:218-242`). There is no timer
  producer and no cron sweep over `wf:wait:by_event:timer` (grep for `timer` in `backend/`
  returns only the enum member `domain/models.py:78`, the executor default
  `wait_for_event.py:44`, unrelated silence-timer code, and three test fixtures at
  `tests/unit/test_workflow_signals.py:319,347,387`).

**Expected.** `docs/workflow.schema.json:369-373` makes `delay_seconds` a *required*
property of a timer wait, so every saved timer wait carries one — the JSON Schema is
enforced on create and patch (`workflow_service.py:134,178` → `:426-433` →
`jsonschema.validate` at `:429`). `docs/UI/08-workflow.md:497` specifies "1-86400, default
60". `docs/implement/H-workflow.md:80` lists timer among the implemented wait events.
R14.03 requires the schema validator to guarantee integrity, and R14.02 names
`docs/workflow.schema.json` normative. A timer wait must therefore resume at `default`
after `delay_seconds`.

### F-36 — join `timeout` port

**Observed.**

- `join.py` returns `skip_edges=True` (`:129-133`) or `port="default"` (`:136-140`).
  `park` is never set, so `run_engine.py:647` is never entered for a join and no timeout
  task is armed. Grep for `park=True` across `backend/contexts/workflow/` returns
  `run_engine.py:778` (retry), `approval_gate.py:109`, `instruct.py:83`,
  `wait_for_event.py:98`, `subagent_spawn.py:104` — no join.
- No join task exists in the worker registry (`app/workers/main.py:253-279`) and grep for
  `join` across `backend/app/workers/` returns only SQL joins and `str.join`.
- `join_config.timeout_seconds` (`docs/workflow.schema.json:411`, default 600) is read by
  no backend code; `join.py:74-76` reads only `mode` and `count`.

**Expected.** The port tables at `docs/workflow.schema.md:29` and `:45`, and
`docs/implement/H-workflow.md:82`, list `default`, `timeout` for `join`.
`docs/workflow.schema.md:35` states "The visual editor renders a distinct handle per
documented port" — which the editor does (`WorkflowNodeComponent.vue:71-73`). R14.05
requires each node type's config panel to expose its settings including timeout; the panel
exists (`JoinConfigForm.vue:63-76`) for a setting with no consumer. Either the port fires
or the documents, linter, editor and schema must stop promising it. Which of those two is
"expected" is **Q-2** — it is a product decision, not a code-reading conclusion.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | For a timer wait, is the deadline `delay_seconds` or `timeout_seconds`? | `delay_seconds` is the wait; `timeout_seconds` becomes inert for timer waits. | A timer's "event" is the elapse of `delay_seconds` — an event guaranteed to arrive, so a timeout on it is meaningless. The schema makes `delay_seconds` required specifically and only for timer (`workflow.schema.json:369-373`), and `timeout_seconds` required for all waits (`:346`). Treating `timeout_seconds` as the timer's deadline would make `delay_seconds` permanently dead and resume the branch at a port that seals the step `failed` (`run_engine.py:381`). |
| Q-2 | **OPEN — needs user.** F-36: build a real join timeout, or record its absence as a non-capability and strip the promise? | **Proposed: record the absence.** See §7 C-3 for the package and the argument. | Building it is not a small fix. It requires the join to park, and parking flips the *whole run* to WAITING (`run_engine.py:648`) while sibling fan-in branches must still execute — `resume_at_port` refuses any run not in WAITING (`run_engine.py:346-347`), and the one-wait-per-run constraint is documented at `run_engine.py:636-642`. Arming a delayed task *without* parking needs a new `StepOutcome` channel, since `timeout_ms` / `timeout_task` are consumed only inside the `if outcome.park:` block (`:647-653`). And the arming condition itself is not currently definable — see the F-36-specific constraint in §6. Against that, the cost of the absence is one dead edge and a misleading linter pass (`findings.md:932`). This is a capability decision with an architectural bill attached, so the user decides. |
| Q-3 | If Q-2 resolves to "build it", does this dossier still carry `depends_on: []`? | No — it must become `depends_on: [2026-07-22-join-epoch-loop-reentry]`. | That dossier's §6 coordination note states the constraint directly: a join timeout "must arm only while the current fan-in is genuinely open. Today 'open' is defined by the same broken `total_branches` this dossier rewrites (`join.py:79-81`), and a join in a loop is *never* open by that definition. **Recommended ordering: this dossier lands first**." Under the recommended Q-2 answer no code arms anything, so the constraint does not bind and `[]` stands (§6). |
| Q-4 | F-2: add a new Arq task for the timer resume, or reuse an existing one? | Reuse `workflow_event_resume`. | It already performs exactly the required sequence: GETDEL claim on `wf:wait:{run}:{node}` (`workflow_signals.py:265`), `resume_at_port(..., "default")` (`:273`), claim-restore-and-retry when the run is not yet WAITING (`:274-287`), audit + `dispatch_enqueues` (`:290-292`), by-event index cleanup (`:294-300`). It is already registered (`app/workers/main.py:272`) and its signature `(ctx, run_id, node_id, attempt=0)` matches the two-positional-argument enqueue shape the engine emits (`run_engine.py:510-524`). A new task would duplicate 60 lines of claim protocol. |
| Q-5 | F-2: should the linter reject or warn on a timer wait whose `timeout` port is wired? | Warn (advisory, rule 0). | After the fix no timer wait can reach `timeout`, so the edge is dead — but making it an error would reject definitions that save cleanly today (`linter.py:200-218` via `workflow_service.py:178`), turning a documentation defect into a save failure for existing users. The existing advisory block at `linter.py:739-762` is the right home. Symmetrically, W3 ("wait_for_event has no timeout edge", `:749-754`) must stop firing for timer waits — it would now be advice to wire an unreachable port. |
| Q-6 | F-36: remove `timeout` from `_ALLOWED_PORTS["join"]` (`linter.py:42`) and `join_config.timeout_seconds` from the schema (`workflow.schema.json:411`)? | No to both. Deprecate in place. | `join_config` is `additionalProperties: false` (`workflow.schema.json:406`), so deleting the property makes every stored definition that carries it fail `_validate_schema` on the next patch (`workflow_service.py:178,429`). Deleting the port from `_ALLOWED_PORTS` makes stored `timeout` edges fail rule 3 (`linter.py:200-218`). Both would convert a harmless dead field into a hard save failure on definitions the user authored in good faith. Runs are unaffected either way — `trigger_run` does not re-validate (`workflow_service.py:299-309`). |
| Q-7 | F-36: remove the `timeout` handle from the join node on the canvas? | No. Keep the handle; remove the config field and add a notice. | `WorkflowNodeComponent.vue:71-73` supplies the `sourceHandle` that stored edges bind to (`useWorkflowEditor.ts:137`); dropping it would orphan the rendering of edges that already exist in saved definitions. Removing the *config field* (`JoinConfigForm.vue:63-76`) and the seeded default (`constants.ts:12`) stops new definitions acquiring the dead setting without touching how old ones draw. |
| Q-8 | Are F-2 and F-36 one dossier or two? | One. | Same defect class (§6), same three artifact families (executor + linter advisory + editor/doc surface), and the same reviewer question — "does anything actually produce this?" — answered twice. The audit hand-off already routes both here (`findings.md:1175`). |

## 4. Reproduction

Both are deterministic; neither depends on timing or concurrency.

### F-2

Preconditions: a project with a workflow the caller may trigger; Redis and an Arq worker
running (the park claim and the delayed task both live there).

1. In the editor, add a `wait_for_event` node. Its seeded config is
   `{event_type: 'timer', timeout_seconds: 300, delay_seconds: 60}` (`constants.ts:10`);
   the delay field renders at `WaitForEventConfigForm.vue:168-182`.
2. Wire `trigger -> wait1`, `wait1 --default--> end`. Leave `timeout` unwired — permitted:
   rule 13 port coverage does not list `wait_for_event`
   (`linter.py:48-53`), and rule 5's termination exception applies only to a wait with no
   outgoing edges (`linter.py:302-310`), which is not the case here.
3. Save. It validates: the JSON Schema's timer branch is satisfied
   (`workflow.schema.json:369-373`) and only advisory W3 fires
   (`linter.py:749-754`).
4. Trigger the run. `wait1` parks; the run goes WAITING (`run_engine.py:648`); exactly one
   delayed job is enqueued — `workflow_event_timeout` at 300 s (`wait_for_event.py:98-101`
   → `run_engine.py:649-652`).
5. At t=60 s, nothing happens. Observe Redis: `wf:wait:{run}:wait1` still holds its payload
   with TTL ≈ 360 (`wait_for_event.py:55-66`).
6. At t=300 s, `workflow_event_timeout` claims and resumes at `timeout`
   (`workflow_signals.py:59,70`). The step is sealed `failed`
   (`run_engine.py:381`). `_advance_from` finds no edge on that port
   (`run_engine.py:708,716-717`) and returns.
7. The run remains RUNNING with no pending work until `workflow_watchdog` force-fails it on
   `idle_max_seconds` (`workflow_watchdog.py:68-72`).

Variant: wire `timeout` to a node. The workflow then "works", 240 s late, down the failure
branch, with the wait step recorded as `failed` — which is why this defect is easy to
misread as a timeout-tuning problem rather than a missing feature.

### F-36

1. Author `parallel -> {a, b}`, both `-> join1`, `join1 --default--> end`, and
   `join1 --timeout--> compensate`. Two incoming edges satisfies rule 14
   (`linter.py:563-626`); `timeout` is an allowed join port (`linter.py:42`), so rule 3
   passes (`linter.py:200-218`). Set `mode: all`.
2. Save — clean, no warnings about the timeout edge.
3. Arrange for branch `b` never to arrive (e.g. `b` fails and the run's on-error strategy
   halts that branch).
4. Observe: `compensate` never executes at any time, including past the configured
   `timeout_seconds: 600`. No delayed job exists for it — `join.py` never sets `park`, so
   `run_engine.py:647-653` never runs.
5. The run is force-failed by the watchdog on `idle_max_seconds`
   (`workflow_watchdog.py:68-72`), default 1800 s, with a reason naming idleness.

## 5. Root Cause Analysis

### F-2

Causal chain:

1. The executor reads `event_type` (`wait_for_event.py:44`) and then never branches on it —
   every event type follows the same code path through `:47-101`.
2. `delay_seconds` is never read anywhere in `backend/` (grep, §2).
3. Exactly one delayed task is armed, and it is always the timeout
   (`wait_for_event.py:36,98-101`), consumed by the single-task park branch at
   `run_engine.py:647-653`.
4. For `message_in_room` / `a2a_message` / `activity_in_room` / `variable_matches`, an
   *external* producer supplies the resume (`workflow_signals.py:153-154,172-173,205-206`
   and `:218-242`). For `timer` there is no external event — the deadline is knowable only
   from the node's own config, so the executor is the only possible producer.
5. Consequence: the wait resumes at `timeout`, sealing the step `failed`
   (`run_engine.py:381`); with the port unwired the branch terminates silently
   (`run_engine.py:716-717`) and the run idles into the watchdog
   (`workflow_watchdog.py:68-72`).

**Root cause: link 3, localized to `wait_for_event.py:94-101`.** The executor arms a
deadline for the wrong quantity and omits the only deadline that a timer wait has. It is
the earliest link whose correction prevents the symptom: correct it and links 4-5 do not
occur, with no change required in the dispatcher, the engine, or the schema.

**Aggravating factors, not causes.**
- `constants.ts:10` seeds `timer` as the default for every new wait node, so the broken
  event type is the one an author gets without choosing it. This maximizes exposure; it
  does not cause the miss.
- The linter is silent: `event_type: timer` triggers no rule, and W3
  (`linter.py:749-754`) actively advises wiring the port that will silently swallow the
  branch.
- `run_engine.py:716-717` returns without diagnosis when no edge matches a port, so the
  branch dies quietly instead of failing loudly. Same aggravator recorded as FU-4 in
  `docs/tasks/2026-07-22-join-epoch-loop-reentry/spec.md`.

**Explicitly not the root cause:** the absence of a `timer` branch in
`workflow_signals.workflow_signal` (`:143-210`). That function fans *real-world signals*
out to parked waits; a timer has no real-world signal. Adding a branch there would require
a polling sweep over `wf:wait:by_event:timer` with sweep-interval granularity, to rediscover
a deadline the executor already held exactly. The dispatcher's silence is correct.

### F-36

Causal chain:

1. `join.py` has two exits, `:129-133` and `:136-140`; neither constructs a `StepOutcome`
   with `park=True`.
2. `run_engine.py:647` gates all timeout arming on `outcome.park`; `:649-652` is the only
   `_pending_enqueues` append that carries a `timeout_task`.
3. Therefore no join ever arms a timeout job, and no other component fills the gap —
   there is no join task in the registry (`app/workers/main.py:253-279`) and no join branch
   in any `workflow_*.py` worker.
4. `join_config.timeout_seconds` (`workflow.schema.json:411`) reaches `node.config` and is
   never read (`join.py:74-76`).
5. The `timeout` port stays permitted (`linter.py:42`), rendered
   (`WorkflowNodeComponent.vue:71-73`) and documented (`workflow.schema.md:29,45`;
   `H-workflow.md:82`; `docs/UI/08-workflow.md:250,518`), so an author can wire an edge that
   can never be taken.

**Root cause: link 1 — the join executor never parks, so it never enters the only
code path that can arm a timeout.** Every other symptom follows. Note the framing: the root
cause of the *observed defect* (a documented port with no producer) is the missing producer,
not the documentation. Whether the correct repair is to add the producer or to retract the
promise is Q-2, and the answer changes the fix but not the diagnosis.

**Aggravating factors.** Five independent surfaces assert the capability — schema property,
schema port table, implementation doc, linter allow-list, canvas handle and config form —
so an author receives six confirmations and zero contradictions before discovering the edge
is dead at runtime.

**Explicitly not the root cause:** `workflow_watchdog.py:64-72`. It reports accurately on
the state it can see. The uninformative failure reason is a symptom of the stall, not its
origin.

## 6. Blast Radius and Sibling Suspects

**Blast radius, F-2.** Every workflow containing a `wait_for_event` node left at its seeded
config. The failure is silent in two distinct ways depending on whether `timeout` is wired
(§4 step 6 vs. the variant), and in both the run's recorded history is
self-consistently wrong: the wait step reads `failed` with `resume_port: "timeout"`
(`run_engine.py:381,395`), which is exactly what a genuine event timeout looks like. No
cross-tenant reach — every key is `run_id`-scoped (`wait_for_event.py:54,79`). No incorrect
data is persisted: definitions containing timer waits are valid definitions that describe a
capability the runtime lacks.

**Blast radius, F-36.** One never-taken edge per affected definition, plus a config field
that consumes editor space and author attention. Runs are not corrupted; a fan-in that
never completes stalls to the watchdog exactly as it would with no timeout edge at all.
Latent-adjacent: the damage is the false promise, not a wrong computation.

**Sibling suspects.** The class: *a node contract surface — a documented port or a schema
config property — that reaches `node.config` or the linter's allow-list but has no runtime
consumer or producer.* Enumerated exhaustively from `docs/workflow.schema.json` `$defs`
against every `config.get(` / `.config[` read in `backend/contexts/workflow/` (57 reads,
listed at `linter.py:203-766`, `run_engine.py:60,661`, and the ten executor modules).

- **S-1 — `join_config.timeout_seconds` + join `timeout` port. CONFIRMED, in scope (F-36).**
  `workflow.schema.json:411`, `linter.py:42`, `constants.ts:12`,
  `JoinConfigForm.vue:63-76`, `WorkflowNodeComponent.vue:71-73`; no reader in `join.py:74-76`.

- **S-2 — `wait_for_event_config.delay_seconds`. CONFIRMED, in scope (F-2).**
  `workflow.schema.json:371-372` (required), no backend reader.

- **S-3 — `agent_invocation_config.target_chatroom_id`. CONFIRMED dead, owned elsewhere.**
  `workflow.schema.json:256-257` defines it; `linter.py:361` validates its scope;
  `AgentInvocationConfigForm.vue:93-95` collects it; `agent_invocation.py:25-46` reads
  `agent_id`, `input_template`, `output_variable`, `timeout_seconds` and never it. Already
  recorded as FU-2 of `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md`.
  Out of scope here to avoid two dossiers editing the same executor's config contract.

- **S-4 — `agent_invocation_config.stream_to_chatroom`. CONFIRMED dead, unowned.**
  `workflow.schema.json:258` (default `true`); `AgentInvocationConfigForm.vue:25-26,105-106`
  seeds and collects it; grep returns no backend occurrence. Same defect class as F-36, not
  covered by any finding in the source audit and not by the dossier that owns S-3. Recorded
  as FU-1 rather than fixed here because the correct behavior — suppress the streamed turn
  or not — is a product question about R14.09 invocation semantics, not a restoration of a
  documented mechanism.

- **S-5 — `wait_for_event` `timeout` port. CLEARED.** Produced: armed at
  `wait_for_event.py:98-101`, dispatched at `run_engine.py:649-652`, consumed by
  `workflow_event_timeout` which resumes at `"timeout"` (`workflow_signals.py:70`).

- **S-6 — `approval_gate` `approved` / `rejected` / `timeout` ports. CLEARED.**
  `approval_gate.py:109` parks; the gate's own timeout defect is F-31 and concerns arming
  order, not the port's existence.

- **S-7 — `instruct` `success` / `failure` ports. CLEARED.** `instruct.py:47-83` reads
  `wait_for_completion` (`:47`) and `completion_timeout_seconds` (`:62`) and parks (`:83`);
  `workflow_instruct_timeout` is registered (`app/workers/main.py:276`).

- **S-8 — `subagent_spawn` `success` / `failure` ports. CLEARED as a port question.**
  `subagent_spawn.py:104-106` parks with a timeout task; every config key in
  `subagent_spawn_config` has a reader (`:42-47,79`). The node is nonetheless broken — F-1 —
  but for a different reason (no executor ever runs the spawned agent's task), so it is not
  a sibling of this defect class.

- **S-9 — `condition` user-declared ports. CLEARED.** `condition.py:27-28` reads `branches`
  and `default_port`; `linter.py:201-205` derives the allow-list from the same config, so
  the port set cannot drift from its producer.

- **S-10 — `end_config.return_variables`. CLEARED.** `end.py:20-21,29` reads and emits it;
  covered by `tests/unit/test_workflow_executors.py:228-280`.

- **S-11 — `trigger_config.timezone` / `cron_expression`. CLEARED.** `trigger.py:29-49`
  reads all three; `linter.py:765-766` lints the expression.

- **S-12 — remaining `wait_for_event` event types. CLEARED individually.**
  `message_in_room` → `workflow_signals.py:148-154`; `a2a_message` → `:169-173`;
  `activity_in_room` → `:197-206`; `variable_matches` → `workflow_variable_signal`
  (`:218-242`), enqueued by the engine at `run_engine.py:633-634`. Each has a live producer;
  `timer` is the only member of the enum (`domain/models.py:75-80`,
  `workflow.schema.json:348`) without one.

- **S-13 — `on_error` strategies. CLEARED.** `run_engine.py:60` parses the block;
  `linter.py:539` and rule 16 (`:671`) validate `fallback_node_id`.

- **S-14 — `loop_guard.max_visits_per_node`. CLEARED here, owned by F-11's dossier.**
  It *is* read (`run_engine.py:553-559`), so it is not a member of this defect class; its
  separate ineffectiveness across Arq hops is FU-2 of
  `docs/tasks/2026-07-22-join-epoch-loop-reentry/spec.md`.

Summary: of the node-contract surfaces in the workflow schema, four have no runtime
consumer — the two in scope here (S-1, S-2), one owned by another dossier (S-3), and one
newly discovered and deferred with a stated reason (S-4). The other ten enumerated surfaces
are cleared with a named producer or reader each.

**Coordination note — `docs/tasks/2026-07-22-join-epoch-loop-reentry/` (F-11).**

That dossier's §6 records the relationship from its side and reaches the same verdict:
"**Verdict: no `depends_on`.**" It identifies its own edit surface in `join.py` as the
module docstring (`:8-12`), the script header (`:33-40`), the Lua body (`:41-63`), the edge
counting and thresholds (`:78-91`) and the `eval` argument list (`:100-109`), and its AC-9
confines its diff to `executors/join.py` plus tests. Two couplings were named there:

1. *Textual* — both dossiers might touch the `StepOutcome` returns at `join.py:129-133` and
   `:136-140`. Under the recommended Q-2 answer, **this dossier touches no line of
   `join.py`**, so the textual coupling disappears entirely.
2. *Semantic* — a join timeout "must arm only while the current fan-in is genuinely open.
   Today 'open' is defined by the same broken `total_branches` this dossier rewrites
   (`join.py:79-81`), and a join in a loop is *never* open by that definition. **Recommended
   ordering: this dossier lands first**, so any later timeout work is built against a
   correct definition of an open fan-in."

The reasoning behind that constraint, verified against current code: `total_branches` counts
every incoming edge including loop back-edges (`join.py:79-81`), and a back-edge cannot
arrive until the join has already fired, so `arrivals >= total_branches` (`join.py:56`) is
unreachable in a single pass for a join inside a cycle. A hypothetical timeout armed on
"fan-in still open" would therefore treat a correctly looping join as permanently open and
fire against it.

**`depends_on: []` is a positive claim here, on two grounds.** Logically: under the
recommended Q-2 answer this dossier arms nothing, so it never needs a definition of an open
fan-in and the semantic constraint does not bind. By overlap: its file set —
`executors/wait_for_event.py`, `linter.py`, four docs, three frontend files — is disjoint
from that dossier's `executors/join.py`. If Q-2 resolves the other way, Q-3 applies and
`depends_on` becomes `[2026-07-22-join-epoch-loop-reentry]`.

## 7. Fix Design

**C-1 — arm the timer's own deadline (`wait_for_event.py:43-101`).**
Compute the wait's effective deadline once, before any Redis write:

- `delay_seconds = int(config.get("delay_seconds", 60))` for `event_type == "timer"`, the
  default matching the schema and editor (`workflow.schema.json:371`, `constants.ts:10`,
  `docs/UI/08-workflow.md:497`). The schema makes it required
  (`workflow.schema.json:372`) and is enforced on save (`workflow_service.py:429`), so the
  default is defensive only.
- `park_seconds = delay_seconds if timer else timeout_seconds`.
- Use `park_seconds + 60` for the claim key TTL (currently `timeout_seconds + 60`,
  `wait_for_event.py:65`) and for the by-event index TTL (`:78`). **This is load-bearing,
  not cosmetic:** with `delay_seconds > timeout_seconds + 60` — e.g. a one-hour timer left
  at the default `timeout_seconds: 300` — the claim key would expire before the delayed
  job runs, its `GETDEL` would return `None`, and the resume would be dropped as
  "already_claimed" (`workflow_signals.py:266-267`). Arming the delay without moving the TTL
  would replace a 100%-reproducible stall with an interval-dependent one.
- For timer, return `timeout_ms = delay_seconds * 1_000` and
  `timeout_task = "workflow_event_resume"`; for every other type, return exactly what the
  code returns today. The engine's park branch (`run_engine.py:647-653`) needs no change:
  it enqueues `(task, run_id, node_id)` deferred by `timeout_ms`
  (`run_engine.py:510-524`), which matches `workflow_event_resume(ctx, run_id, node_id,
  attempt=0)` (`workflow_signals.py:245`), already registered (`app/workers/main.py:272`).

Result: the timer wait resumes at `default` (`workflow_signals.py:273`) after
`delay_seconds`, under the same GETDEL claim, the same claim-restore-and-retry on a
not-yet-WAITING run (`:274-287`), and the same index cleanup (`:294-300`) as every other
wait. No new task, no worker-registry change, no schema change, no engine change.

**C-2 — stop the linter advising an unreachable port (`linter.py:749-754`).**
Skip W3 for `event_type == "timer"`, and add one advisory (rule 0, warning, in the same
block) when a timer wait has an outgoing `timeout` edge: that edge is now provably dead.
Keep both at warning level per Q-5.

**C-3 — record the join timeout as a non-capability (Q-2, recommended).**
- `linter.py:739-762`: advisory warning when a `join` node has an outgoing `timeout` edge —
  "join timeout is not implemented; this edge is never taken". The port stays in
  `_ALLOWED_PORTS` (Q-6) so stored definitions keep saving.
- `frontend/src/slices/workflow/constants.ts:12`: drop `timeout_seconds` from the join
  default.
- `JoinConfigForm.vue:63-76`: remove the timeout field; add a short notice via `$t()` in
  `locales/en.json` and `locales/zh-TW.json` (siblings of `workflow.config.timeoutSeconds`
  at `en.json:189` / `zh-TW.json:187`) stating that a join has no timeout and a stalled
  fan-in is bounded by the run's `idle_max_seconds`.
- `WorkflowNodeComponent.vue:71-73` unchanged (Q-7).
- `docs/workflow.schema.json:411`: keep the property, mark it deprecated in its
  `description` (Q-6).
- `docs/workflow.schema.md:29,45`, `docs/implement/H-workflow.md:82`,
  `docs/UI/08-workflow.md:250,518,1301`: state that the join `timeout` port is not
  implemented and that fan-in stalls are bounded by `idle_max_seconds`
  (`workflow_watchdog.py:71-72`).

**Why this corrects rather than masks.**

*C-1.* The rejected alternatives each preserve the root cause and relocate the breakage.
(a) A cron sweep over `wf:wait:by_event:timer` would add a second producer, at sweep
granularity, for a deadline the executor already knows exactly — and would keep
`wait_for_event.py` unable to express its own deadline, so the next event type with an
internal deadline repeats the bug. (b) Redefining the timer's continuation as the `timeout`
port would make `delay_seconds` permanently dead, seal every successful timer wait as
`failed` (`run_engine.py:381`), and require every author to run their success path out of a
failure port. (c) Removing `timer` from the enum (`workflow.schema.json:348`,
`domain/models.py:78`) would reject every stored definition that uses it and delete a
documented capability rather than deliver it. C-1 instead puts the deadline in the only
component that holds it, and reuses the resume protocol that already carries the claim
guarantees (ASYNC-10, `wait_for_event.py:7-15`) — so the timer inherits the
exactly-once property rather than getting a parallel one.

*C-3.* This is deliberately not a repair of the runtime, and it should be read as the
honest form of the alternative rather than a shortcut past it. The masking version would be
to leave all six surfaces asserting the capability and hope authors do not wire the port —
which is the status quo. C-3 removes the assertion at each point where an author encounters
it (config form, seeded default, save-time lint, three documents), so the system stops
claiming something it does not do. If Q-2 goes the other way, the real fix is a `StepOutcome`
channel for unparked delayed tasks plus a correct "open fan-in" predicate, and it sequences
behind F-11 (Q-3).

**Data repair.**

*F-2: none, and here is why.* Nothing wrong was persisted. Stored definitions containing
timer waits are schema-valid and become correct the moment C-1 deploys — no rewrite is
needed or wanted. Redis state self-clears: claim keys and index members carry TTLs
(`wait_for_event.py:65,78-82`). Runs already force-failed by the watchdog are terminal, were
never partially committed to a wrong state, and cannot be resumed — their step rows are
accurate and only the recorded resume port is misleading; they must be re-triggered by the
user. In-flight at deploy time: a wait parked by the old code has its old (timeout-only) job
already enqueued in Redis and will behave as it does today; the worst case is one more run
following the pre-fix path, bounded by its own `timeout_seconds`. No migration, no backfill.

*F-36: none, and deliberately so.* Definitions do carry residue — `join.timeout_seconds`
values and edges from the join `timeout` port. A backfill stripping them was considered and
rejected: it would rewrite user-authored definitions for zero behavioral gain, and every
workflow write goes through optimistic concurrency on `version`
(`workflow_service.py:169,177-189`), so a bulk rewrite would either bump every version out
from under open editor sessions or bypass the guard. Keeping the schema property and the
allowed port (Q-6) means the residue stays valid indefinitely, and the new save-time lint
warning (C-3) surfaces the dead edge to the one person who can decide whether to remove it.
No migration.

## 8. Regression Test Plan

Every tier named below already exists in this repo. `backend/tests/unit/` uses the
`AsyncMock` + `patch("shared_kernel.auth.clients.get_redis", ...)` pattern established at
`tests/unit/test_workflow_executors.py:377-381`; the linter is unit-testable by direct
import (`tests/unit/test_workflow_reference_scoping.py:12,38`); the frontend has a component
vitest tier (`frontend/src/slices/workflow/components/__tests__/`). **No Redis-backed
integration tier is proposed and none is needed** — `backend/tests/integration/conftest.py`
contains no Redis fixture and `fakeredis` is not a dependency of `backend/pyproject.toml`;
every assertion below is on arguments and return values, not on Redis behavior.

Failing tests first.

**T-1 (unit) — new `TestWaitForEventExecutor` in
`backend/tests/unit/test_workflow_executors.py`.** There is no existing test for this
executor anywhere: grep for `wait_for_event` across `backend/tests/` returns nothing, and
the class list in that file runs Condition, SetVariable, End, Trigger, Join, Instruct,
AgentInvocation (`:52-575`) with no wait. So **no existing test can fail on this bug**, and
this class must be created. Model the helper on `TestJoinExecutor._run_join`
(`:359-381`): build a `RunContext` via `_make_ctx`, a node via `_make_node`, patch
`get_redis` with an `AsyncMock`, and return both the `StepOutcome` and the captured
`mock_redis.set` / `mock_redis.expire` calls.

- `test_timer_wait_arms_delay_not_timeout`: config
  `{event_type: "timer", timeout_seconds: 300, delay_seconds: 60}`. Assert
  `outcome.timeout_ms == 60_000` and `outcome.timeout_task == "workflow_event_resume"`.
  **Fails today**: `wait_for_event.py:99-100` returns `300_000` and
  `"workflow_event_timeout"` (`:36`).
- `test_timer_claim_ttl_covers_the_delay`: config
  `{event_type: "timer", timeout_seconds: 300, delay_seconds: 3600}`. Assert the `ex`
  kwarg of the `redis.set` call is `3660`. **Fails today**: `wait_for_event.py:65` passes
  `timeout_seconds + 60 = 360`, i.e. the claim key would die 54 minutes before the resume —
  this is the C-1 TTL argument, asserted directly.
- `test_timer_index_ttl_covers_the_delay`: same config. Assert `redis.expire` is called on
  `wf:wait:by_event:timer` with `3660`. **Fails today**: `:78` computes the index TTL from
  `timeout_seconds`.
- `test_message_wait_unchanged`: config
  `{event_type: "message_in_room", timeout_seconds: 300, chatroom_id: <uuid>}`. Assert
  `timeout_ms == 300_000`, `timeout_task == "workflow_event_timeout"`, `set` `ex == 360`.
  **Passes both before and after** — the guard that C-1 does not disturb externally
  produced waits. Write it alongside the failing ones.
- `test_timer_defaults_delay_when_absent`: config `{event_type: "timer",
  timeout_seconds: 300}`. Assert `timeout_ms == 60_000`. **Fails today** for the same reason
  as the first case; pins the defensive default against the schema default
  (`workflow.schema.json:371`).

**T-2 (unit) — new `backend/tests/unit/test_workflow_lint_advisories.py`,** importing
`validate_definition` directly as `test_workflow_reference_scoping.py:12` does.

- `test_timer_wait_does_not_warn_about_missing_timeout_edge`: a timer wait with only a
  `default` edge. Assert no warning whose message contains "no timeout edge".
  **Fails today**: `linter.py:749-754` emits W3 for every `wait_for_event` regardless of
  event type.
- `test_timer_wait_with_timeout_edge_warns_unreachable`: a timer wait with a `timeout`
  edge. Assert exactly one new advisory and `result.valid is True`.
  **Fails today**: no such rule exists — the block at `linter.py:739-762` has no timer case.
- `test_join_timeout_edge_warns_not_implemented`: a `parallel`/`join` pair with a
  `join --timeout--> compensate` edge. Assert one advisory and `result.valid is True`.
  **Fails today**: `linter.py:42` permits the port and no rule comments on it, so the
  definition lints completely clean — which is the defect (`findings.md:932`).
- `test_join_timeout_edge_still_saves`: the same definition. Assert `result.errors == []`.
  **Passes both before and after** — the Q-6 guard that the advisory did not become a
  blocking error and break stored definitions.
- `test_message_wait_without_timeout_edge_still_warns`: a `message_in_room` wait with no
  `timeout` edge. Assert W3 still fires. **Passes both before and after** — pins that C-2
  narrowed W3 rather than removed it.

**T-3 (unit, frontend) — new
`frontend/src/slices/workflow/components/config/__tests__/JoinConfigForm.test.ts`.**
Mount the form and assert no input is bound to `timeout_seconds`, and that the notice string
renders through `$t()`. **Fails today**: `JoinConfigForm.vue:63-76` renders the field.
Also assert `NODE_DEFAULTS.join` has no `timeout_seconds` key — **fails today**
(`constants.ts:12`).

**T-4 — explicitly no test.** `run_engine.py:647-653` and `workflow_signals.py:245-305` are
untouched by this fix; C-1 reuses them as-is. Listed so `/build` does not add engine-level or
task-level coverage for a defect that lives entirely in one executor's return value.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| A timer wait now resumes at `default` where an author had (unknowingly) built their flow around the `timeout` port firing at `timeout_seconds` | Only possible for definitions authored against broken behavior, and today that path seals the step `failed` (`run_engine.py:381`). The new lint advisory (C-2) names the dead edge at save time. Release-note it. |
| `delay_seconds > timeout_seconds + 60` previously could not occur meaningfully; after C-1 the claim key must outlive the delay | Asserted directly by T-1 `test_timer_claim_ttl_covers_the_delay` and `test_timer_index_ttl_covers_the_delay`. |
| `workflow_event_resume` emits its audit with `reason="event"` (`workflow_signals.py:290`), which now covers timer resumes too | Accurate enough — a timer elapse is the wait's event. The enqueue shape (`run_engine.py:510-524`) carries no room for a reason argument, so a distinct reason would require a new task; rejected under Q-4. Noted in FU-3. |
| Deploy straddles the change: a wait parked by the old code already has its timeout-only job in Redis | Bounded and self-limiting — that run behaves as it does today and is bounded by its own `timeout_seconds`. Key shapes are unchanged (`wait_for_event.py:54,77`), so no cross-version key confusion. |
| Removing the join timeout field orphans `timeout_seconds` values in stored definitions | Intended and safe: the schema property is retained (Q-6), so those definitions keep validating (`workflow_service.py:429`); `join.py` ignores the key exactly as it does today. |
| C-3 removes a UI control users may have configured, which can read as a regression | The control never did anything. The `$t()` notice replaces it in place, so the panel explains the change where the field used to be. |
| The new advisories add noise to existing clean lints | Both are scoped to a specific edge that is provably dead; neither can fire on a definition that has no such edge. |

**Rollback.** Revert `backend/contexts/workflow/application/executors/wait_for_event.py`,
`backend/contexts/workflow/application/linter.py`, and the three frontend files. No
migration, no schema change, no Redis key-shape change, no worker-registry change — a
revert needs no cleanup, and a wait parked by the fixed code carries its resume job in
Redis independently of which version of the executor is deployed when it fires.

## 10. Acceptance Criteria

- [ ] AC-1: every T-1 test marked "fails today" fails against current code and passes after
      the fix; `test_message_wait_unchanged` passes both before and after.
- [ ] AC-2: every T-2 test marked "fails today" fails before and passes after;
      `test_join_timeout_edge_still_saves` and
      `test_message_wait_without_timeout_edge_still_warns` pass both before and after.
- [ ] AC-3: T-3 fails before and passes after.
- [ ] AC-4: the §4 F-2 reproduction resumes at the `default` port at `delay_seconds` and
      reaches `end`, with the wait step sealed `succeeded` (not `failed` via
      `run_engine.py:381`) and no `idle_max_seconds` force-fail
      (`workflow_watchdog.py:71-72`).
- [ ] AC-5: a timer wait with `delay_seconds: 3600, timeout_seconds: 300` resumes correctly
      — the claim key outlives its delay (the C-1 TTL requirement, asserted by T-1).
- [ ] AC-6: no timer wait arms `workflow_event_timeout`; every non-timer wait still does.
- [ ] AC-7: saving a definition that carries `join.timeout_seconds` or a join `timeout` edge
      still succeeds, and produces exactly one advisory warning per dead edge.
- [ ] AC-8: `docs/workflow.schema.md:29,45`, `docs/implement/H-workflow.md:82`, and
      `docs/UI/08-workflow.md:250,518,1301` state that the join `timeout` port is not
      implemented; `docs/workflow.schema.json:411` marks `timeout_seconds` deprecated.
- [ ] AC-9: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
      `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build` pass in
      `frontend/`.
- [ ] AC-10: the diff touches only `executors/wait_for_event.py`, `linter.py`, the three
      named frontend files, the two locale files, the four documents, and the three test
      files. **No change to `executors/join.py`, `run_engine.py`, `workflow_signals.py`,
      `app/workers/main.py`, or any migration** — a diff touching `join.py` means Q-2 was
      resolved the other way, which requires re-approval and the `depends_on` change in Q-3.
- [ ] AC-11: Q-2 is answered by the user before `/build` starts. This dossier must not move
      to `in-progress` while it is open.

## 11. SRS Delta

None. R14.02 already makes `docs/workflow.schema.json` the normative node definition and
R14.03 already requires the schema validator to guarantee integrity; C-1 makes the runtime
honor a property the schema already requires (`workflow.schema.json:371-372`). C-3 removes
a promise from three implementation documents and the schema description, none of which are
the SRS — R14.05's requirement that each node type have a config panel is satisfied by a
join panel with `mode` and `count`.

If Q-2 resolves to "build the join timeout", that becomes a new capability and this section
must be redrafted with an `[R14.xx]` entry before approval.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 — `agent_invocation.stream_to_chatroom` is dead config (S-4).**
  `docs/workflow.schema.json:258` declares it with default `true`;
  `AgentInvocationConfigForm.vue:25-26,105-106` seeds and collects it; no backend file
  mentions it. Same defect class as F-36, not covered by the source audit, and not covered
  by the dossier that owns `target_chatroom_id` (S-3). Needs a product decision on whether a
  workflow-issued turn may be suppressed from the room before it can be specced — it
  interacts with R14.09 (workflow invocations respect all agent settings) and R14.10 (the
  trace is backstage-only).
- **FU-2 — a schema-to-reader conformance test.** Four of fourteen node-contract surfaces
  had no consumer (§6), and each was found only by hand-grepping `config.get(` against
  `docs/workflow.schema.json`. A unit test that walks the schema's `$defs` and asserts every
  declared config property is referenced somewhere in `backend/contexts/workflow/` would
  have caught S-1 through S-4 at once, and would catch the next one at commit time.
  Cleared-but-fragile class, worth hardening.
- **FU-3 — timer resumes audit as `reason="event"`.** `workflow_signals.py:290` hardcodes
  the reason, and the engine's enqueue shape (`run_engine.py:510-524`) carries only
  `(run_id, node_id)`, so a distinct `reason="timer"` requires either a new task or a wider
  enqueue tuple. Cosmetic for the audit trail; deferred rather than expanded into this fix.
- **FU-4 — join fan-in stalls have no bound but `idle_max_seconds`.** Recorded as the
  standing consequence of the Q-2 recommendation: a join whose fan-in never completes is
  caught only by `workflow_watchdog.py:68-72` at the run level, with a reason that names
  idleness rather than the incomplete fan-in. If the join timeout is ever built, this is the
  gap it closes, and per §6 it sequences behind
  `docs/tasks/2026-07-22-join-epoch-loop-reentry/`.
</content>
