---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R9.17, R15.15, R15.16, R15.23]
depends_on: []
---

# Every context and chain identity in the A2A subsystem is declared and never populated

## 1. Summary

Four findings, **one structural root cause**: every authorization- and budget-relevant piece of
context or chain identity in the A2A subsystem is declared on a data carrier — an envelope
field, a dataclass field, a keyword argument, a `ContextVar` — and **never populated by any
production caller**. The checkers that read those fields are correct. They are starved.

| Finding | Starved carrier | Reader rendered inert |
|---|---|---|
| **F-9** (+ config-audit F-4) | `caller_invocation_context_id`, `callee_attached_context_ids` (`a2a_service.py:63-65,132-133,199-200`) | `a2a_scope.evaluate` rule 3a (`a2a_scope.py:98-101`) |
| **F-24** | `A2AEnvelope.call_depth` / `call_path` across a process hop (`orchestration/domain/models.py:58-59`) | `a2a_call_chain.next_hop` cycle and depth guard (`a2a_call_chain.py:46-54`) |
| **F-25** | `chain_id` / `parent_path` (`instruct_service.py:57-58`) | `[R15.16]` rules 1, 2, 4 (`instruct_service.py:73,103,118-124`) |
| **F-26** | `wakeup_started_at` (`instruct_service.py:59`) | `[R15.16]` rule 3 (`:107-115`) |

The mechanism is identical in all four: an optional parameter with a permissive-looking default,
a correct consumer, and zero production writers. `a2a_service.py:319` is the same defect written
as a hardcoded literal rather than an omitted argument.

**F-9 is live and fails closed** — the G.7 instruct-via-workflow feature is non-functional for
every ordinary agent pair. **F-24/F-25/F-26 are latent**: no agent tool issues A2A or instructs
(`tool_registry.py` has no such tool), so the guards have nothing to fire on. Their consequence
is a missing safety net — most concretely, F-24's cycle guard cannot break the a2a-audit F-4
self-amplification loop.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-9 (major), F-24,
F-25, F-26 (minor); and `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-4 (major),
whose hand-off note carries the design pass this dossier builds on.

## 2. Observed vs Expected

**F-9.** `instruct_service.py:156` calls `await self._a2a.send(envelope=envelope)` with **no
keyword arguments**. `a2a_service.py:88-97` takes the agent-to-agent branch (because
`envelope.from_agent = issuer_agent_id`, `:142`) and passes `callee_attached_context_ids or
frozenset()` plus `caller_invocation_context_id=None` into `_enforce_scope`. In
`a2a_scope.evaluate:98-101`, `shared_context` requires a non-`None` caller context, so it is
unconditionally `False`; the only remaining grant is `is_call_only_enabled` (`:105`); verdict
`allowed=False` (`:108-111`). `A2AForbidden` propagates to
`backend/contexts/workflow/application/executors/instruct.py:94-100`, routing the node to
`failure`.

Three secondary defects on the same statement:
- `instruct_service.py:144` hardcodes `workflow_run_id=None`, which is false and degrades
  attribution at `a2a_handler.py:187`.
- `:128-136` INSERTs the `instructions` row and emits `instruct.issued` **before** the send at
  `:156`, so a denied send leaves an orphan `issued` row that `retention.py:625-644` never reaps
  (it collects only chains whose rows are all terminal).
- `a2a_service.py:316-321` hardcodes `callee_attached_context_ids=frozenset()` inside the
  per-recipient broadcast loop while threading `caller_invocation_context_id` through, so
  broadcast structurally reaches only `call_only` agents — contradicting
  `contexts/orchestration/domain/models.py:33-36`.

**F-24.** `a2a_call_chain._chain` (`a2a_call_chain.py:28-31`) is a `ContextVar` defaulting to
`(0, ())`, bound only at `a2a_handler.py:85` inside the **A2A consumer process**. Its sole
production reader is `a2a_service.py:143`, reached from `executors/agent_invocation.py:41-47` in
the **workflow Arq worker** — a different process, so the var is always at its default and
`next_hop` returns `(1, (callee,))` on every hop. The break has a nameable link:
`a2a_handler._dispatch_a2a_workflow_signal` (`:211-219`) enqueues only
`{"target_agent_id", "msg_type"}`. The envelope itself already serializes both fields correctly
(`models.py:71-72,86-87`), **so the wire format is not the bug and needs no migration.**

**F-25.** `instruct_service.py:65-67` computes `chain_id` and `new_path` and stamps them into the
envelope payload at `:146-151`, but `a2a_handler._handle_instruct` (`:116-142`) reads only
`payload["instruction_id"]` (`:122`) and binds no chain context around `_run_turn_with_db`
(`:130`). The sole production caller, `executors/instruct.py:39-43`, passes neither, so every
issue mints a fresh chain with `parent_path=()` and `depth=1`. Rule 1 catches only self-instruct
A→A; rule 2 compares `1 >= 5`; rule 4 calls `get_chain_start_time` on a chain id created three
lines earlier, so elapsed is ~0.

**F-26.** Rule 3 is gated `if wakeup_started_at:` (`:107`), default `None` at `:59` and mirrored
at `facade.py:249`. The only production caller does not pass it, and
`count_issued_by_agent_since` has no other call site.

**Expected.** Rule 3a grants when the caller and callee genuinely share a context, and the
`[R15.16]` chain guards are reachable. `[R9.17]` (`REQUIREMENTS.md:450-454`) names "ChatRoom
**or Workflow run**" as valid invocation contexts, so rule 3a is not absent for the workflow
path — it is unfed.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier or two? | **One.** | Fixing F-9 alone leaves the same class alive one call away, and the moment an A2A or instruct agent tool is added — which `a2a_call_chain.py:8-14` explicitly claims is covered — F-24/25/26 flip from latent to live simultaneously. The unifying acceptance criterion is: *no authorization- or budget-relevant field may be defaulted at a production call site; if the caller cannot supply it, the service must derive it.* |
| Q-2 | How should rule 3a be fed for a workflow instruct? | **Read the workflow run as the context**: `caller_invocation_context_id = ctx.run_id`. | `[R9.17]` names it explicitly. Rule 3a is unfed, not absent. |
| Q-3 | Who derives the callee's attached-context set? | **`A2AService`, from the DB. Never the executor.** | `a2a_scope.py:98-101` trusts `callee_attached_context_ids` **unconditionally**, so an executor-supplied set is self-authorization — a one-argument authorization bypass. This is the single most important constraint in the dossier. `_enforce_workflow_tenant` (`a2a_service.py:419-450`) is the service-side pattern to copy, lazy `WorkflowFacade` import included. |
| Q-4 | Why not mirror `agent_invocation` with `from_agent_id=None`? | **Rejected.** | It discards the issuer, breaking `parent_agent_id` usage attribution (`[R15.23]`) — `a2a_handler.py:186` forwards it into `turn_engine.py:873,2669,2744` and thence to `key_usage_events` — and it silently overrides the `a2a_enabled` opt-out on both sides. The two nodes take different rules because they are different relationships: `agent_invocation_config` has no issuer, `instruct_config` requires one (`docs/workflow.schema.json:317`). |
| Q-5 | Why not add a workflow-origin branch to `a2a_scope.evaluate`? | **Rejected.** | `a2a_scope.py:14-19` documents it as a pure function with no DB access; a branch that means anything needs run membership, which is a DB fact. A bare "came from a workflow" flag is a one-assertion bypass, and the moment an agent-authored surface can set it the check is defeated. |
| Q-6 | **What does "attached to a workflow run" mean?** | **OPEN — user decision required. Do not guess.** | Three options in §7. No requirement, doc or table defines it; `REQUIREMENTS.md:453` says only "a context … that the callee is also attached to". The security context for the decision: `[R15.15]` (`:779`) says an instructed agent **cannot refuse**, so every widening hands workflow authors unrefusable turn execution — the target's BYO key spend, the target's tools, the target's MCP servers — on more agents. |
| Q-7 | `wakeup_started_at` has no meaning for a workflow-originated instruct. What replaces rule 3? | **OPEN — user decision.** | Either (i) pass the *run* start as the budget window and rename the concept to an issuing window, or (ii) declare rule 3 out of scope for workflow instructs and add a run-scoped loop guard instead. Note F-26's failure scenario — a loop body issuing 500 instructs — is a **run**-scoped abuse, not a wake-up-scoped one, so (i) is the substantive fix and (ii) is a documented non-fix. |
| Q-8 | Does this depend on any open dossier? | No. `depends_on: []`. | Checked against `BOARD.md`. `2026-07-22-subagent-spawn-fail-fast` touches a different executor; nothing else touches `a2a_service.py` or `instruct_service.py`. |

## 4. Reproduction

**F-9 (live, deterministic).** Seed a project with agents A and B, both `a2a_enabled=True`, both
bound to the same chatroom, **neither** with `call_only`. Save a workflow with an `instruct`
node (`issuer_agent_id=A`, `target_agent_id=B`) and trigger a run.

Observed: the node takes `failure` with `error="a2a denied: no shared context and callee
call_only disabled (caller=A, callee=B)"` (`a2a_scope.py:110`, raised at `a2a_service.py:413`);
an `a2a.forbidden` audit row (`:401-412`); and an orphan `instructions` row in `issued` plus an
`instruct.issued` audit row already committed (`instruct_service.py:128-136`). Setting
`call_only.enabled=true` on B makes it pass — **that is the discriminator proving rule 3b, not
3a, is doing the work.**

Broadcast sibling: same seed, have A broadcast to `broadcast:workspace`; B is skipped at
`a2a_service.py:322-323` despite sharing the room, because of the hardcoded `frozenset()` at
`:319`.

**F-24 (latent — reproduce by assertion, not by loop).** In the workflow worker process, observe
that `a2a_call_chain.current()` is `(0, ())` at `a2a_service.py:143` for any workflow-originated
call, regardless of the inbound envelope's `call_depth`. The full cross-workflow cycle overlaps
the a2a audit's F-4 amplification; prefer the assertion.

**F-25.** Call `issue_instruct` twice in sequence, A→B then B→A, exactly as two workflow nodes
would. Both succeed; the second row has `depth=1`, `path=[B]` and a fresh `chain_id` — no
`InstructLoopDetected`. Then verify via `a2a_handler._handle_instruct` that the delivered
envelope's `chain_id`/`path` (present in `payload`, `instruct_service.py:148-149`) are read
nowhere at `:116-142`.

**F-26.** A workflow whose loop body contains one `instruct` node, iterating N > 5 (the loop
guard permits up to `max_visits_per_node`, clamped 1..1000 at
`contexts/workflow/domain/models.py:202-206`). N instructions issue,
`count_issued_by_agent_since` is never called, no `InstructBudgetExceeded`, no audit of the
breach.

## 5. Root Cause Analysis

**Root cause: authorization- and budget-relevant identity is carried on optional parameters with
permissive defaults, and no production caller populates any of them.** Per-finding traces are in
§2. The four differ only in which carrier is starved and whether a live caller exists to expose
it.

**Why F-9 survived**, which the dossier should state because it dictates the test plan: every
test on this path mocks the seam under test. `tests/unit/test_orchestration_services.py:137-151`
(`_make_instruct_service`) replaces `svc._a2a` with an `AsyncMock`, and `:447` asserts only
`a2a.send.assert_awaited_once()` — that it was awaited, never with what.
`tests/unit/test_workflow_executors.py:440,470,501` mock `issue_instruct` wholesale and
`:537,561,585` mock `a2a_call`. `tests/unit/test_orchestration_services.py:461,472,487`
hand-construct `parent_path` and `wakeup_started_at`, masking the wiring gap for F-25/F-26. And
`tests/wiring/test_wiring.py:328-332` — the only real-stack A2A test — **works around the bug**
by giving the callee `call_only`, with a comment explaining why. A major, feature-killing defect
shipped through roughly 4700 unit tests.

## 6. Blast Radius and Sibling Suspects

**Every production caller of `A2AService.send` / `call` / `notify`:**

| Call site | Context args | Status |
|---|---|---|
| `instruct_service.py:156` → `send` | **none supplied** | **Confirmed — primary defect** |
| `a2a_service.py:316-321` (broadcast loop) | `frozenset()` hardcoded | **Confirmed — same class, distinct fix** |
| `a2a_service.py:163-167` (`call` → `send`), `:213-217` (`notify` → `send`) | forwards its own params | **Cleared** — pass-throughs; they inherit whatever the caller supplies |
| `executors/agent_invocation.py:41-47` → `facade.a2a_call` | `from_agent_id=None`, `workflow_run_id=ctx.run_id` | **Cleared** — takes the `_enforce_workflow_tenant` branch (`a2a_service.py:98-100`), a real service-side check. **Do not "fix" it into the agent branch.** |
| `OrchestrationFacade.send_a2a` / `a2a_notify` (`facade.py:47-58`, `:81-98`) | both declare the params, defaulting `None` | **Suspect — unused public surface.** No production caller of either exists anywhere in `backend/`. Either wire or narrow them; a permissive default on an unused authorization API is a trap for the first caller. |
| `A2AService.reply` (`:219-265`) | skips scope by design (`:243`), after `_require_agent` anti-spoof at `:232` | **Cleared** — documented, and the rendezvous binds the expected responder at `:161` |
| `app/api/v1/orchestration.py:265,287,313` | read-only | **Cleared** |

**Every site passing or defaulting chain parameters:** `executors/instruct.py:39-43`
(**confirmed** — F-25, F-26); `facade.py:241-264` (**confirmed as amplifier** — mirrors the
permissive defaults at the public boundary, `:247-252`); `a2a_handler.py:116-142`
(**confirmed** — drops `chain_id`/`path`/`depth`); `a2a_handler.py:85` (correct in-process, and
the **only** binder, hence F-24); `:211-219` (**confirmed** — the precise link where depth and
path leave the system); `a2a_service.py:143` (**confirmed** — reads a var no workflow-worker path
ever set); `run_engine.py:178-185` and `workflow_signals.py:139` (**confirmed as carriers** that
would relay depth and path); `tool_registry.py` and `turn_engine.py` (**cleared** — no A2A or
chain reference, which is exactly why F-24/25/26 are latent, and the moment that changes they
are live).

## 7. Fix Design

**Part 1 — feed rule 3a (F-9).** `A2AService` derives the run's participant set and grants when
both issuer and target are members, setting the context to `run_id`. Three options for
"participant", and **Q-6 is the user's to answer**:

- **Option A — design-time participation (recommended shape).** New `WorkflowFacade` method
  resolving run → `workflow_id` → `definition`, then reusing **`linter._collect_agent_ids`**
  (`contexts/workflow/application/linter.py:111-126`), which already extracts `agent_id`,
  `target_agent_id`, `issuer_agent_id`, `leader_agent_id`, `parent_agent_id` and `approvers` —
  exactly the reference sites that matter, already written and already tested. Deterministic,
  race-free, node-order-independent.
  **Caveat the implementer must handle**: the run does **not** snapshot its definition —
  `run_engine.py:155` persists `context={"trigger_payload": ...}` only, holding `definition` in
  memory as `RunContext.workflow_def` (`:181`). A long-running run whose workflow was edited
  mid-flight is authorized against the *current* definition. Sub-choices: **A1** accept and
  document the drift; **A2** snapshot the participant set into `workflow_runs.context` at
  `:155` — JSONB, so no migration, but it only helps runs started after deploy and old runs must
  fall back to A1.
- **Option B — execution-time participation**, derived from `workflow_steps`
  (`WorkflowFacade.list_steps`, `:111-112`). Narrowest grant, but the same instruct is allowed or
  denied depending on node execution order, and an instruct node firing *before* the target's own
  node is denied — the common authoring shape. Leaning reject.
- **Option C — a `workflow_run_participants` table**, materialized at run start from the same
  extraction. This is A2 normalized: explicit, queryable, snapshot-correct, no drift. Costs a
  migration.

Also, regardless of option: fix `instruct_service.py:144`'s hardcoded `workflow_run_id=None`,
and move the `instructions` INSERT and `instruct.issued` audit (`:128-136`) after the send, or
compensate them, so a denied instruct leaves no orphan `issued` row.

**Part 2 — propagate the chain (F-24/25/26).** Use the existing envelope and trigger payload; do
**not** introduce a Redis side-channel, which would duplicate state the envelope already
serializes correctly and could desync from it.

- **F-24**: add `call_depth`/`call_path` to the signal payload at `a2a_handler.py:218`; they flow
  through `workflow_signals.py:139` into `RunContext.trigger_payload`;
  `agent_invocation.py:41-47` reads them back and passes them to `a2a_call`, which threads them
  into `next_hop` instead of reading the empty `ContextVar`. `A2AService.call` accepts an explicit
  inbound chain, defaulting to the `ContextVar` so in-process behaviour is preserved.
- **F-25**: bind chain context in `a2a_handler._handle_instruct` around `:130` from the payload
  fields `instruct_service.py:146-151` already writes, and have `executors/instruct.py:39-43`
  forward `chain_id`/`parent_path` from `ctx` when the run was itself instruct-originated.
- **F-26**: per Q-7.

**Sequencing.** F-9 is the only live defect and is independently shippable. F-24/25/26 are latent
and touch three processes; they can land second without leaving anything worse. **But do not
close the dossier on F-9 alone** — the shared root cause is the point.

## 8. Regression Test Plan

**Anti-requirement, load-bearing and non-negotiable:** no new or modified test on these paths may
mock `svc._a2a`, `A2AService._enforce_scope`, `a2a_scope.evaluate`, or
`OrchestrationFacade.issue_instruct`. Mocking every one of those seams is exactly why this
survived (§5). The existing offenders listed there are to be **supplemented, not extended**.

**The workaround to remove.** `tests/wiring/test_wiring.py:328-332` gives the callee `call_only`
with a comment stating why. That comment documents the bug as if it were the design. After the
fix, add a sibling wiring test where the two agents share a context and the callee has **no**
`call_only` — and it must pass. Keep the `call_only` test as the rule-3b case.

**Wiring tier** (`-m wiring`, real Postgres/Redis/Vault, real scope checker; reuse
`_seed_agent_and_room` at `:154`, `_serving` at `:208`, `consume_once`/`handle_envelope` at
`:63-64`; stub only the LLM router as `:308` already does):

**The failing test comes first** — **W-1**: two distinct agents, both `a2a_enabled`, neither
`call_only`, both referenced by a saved workflow definition containing an `instruct` node; start
a real run through `RunEngine`. Assert the node takes `success`, the `instructions` row reaches
`delivered`, **no `a2a.forbidden` audit row exists**, and the delivered envelope's
`workflow_run_id == run_id` (which also covers the `:144` hardcode). **Fails today** at the
scope check.

Then: **W-2** (negative, the security floor) — the target is **not** a participant; assert
`A2AForbidden`, an `a2a.forbidden` row, and the `failure` port. *This is the test that fails if a
future change loosens the grant to "any workflow".* **W-3** — cross-project target stays denied
(`a2a_scope.py:79-80`). **W-4** — `a2a_enabled=false` on either side stays denied (`:93-96`);
guards against the rejected Q-4 shortcut sneaking back. **W-5** — broadcast reaches a room-mate
with no `call_only`; impossible today (`:319`). **W-6** — a denied instruct leaves **no** `issued`
row.

**Integration tier**: **I-1** (F-25) two sequential real `issue_instruct` calls A→B→A with the
chain relayed as production will relay it; assert `InstructLoopDetected` and a persisted
`rejected_loop` row. **I-2** (F-26) a real run whose loop body issues past the cap; assert
`InstructBudgetExceeded` and a breach audit.

**Unit tier, for genuinely pure logic only**: **U-1** extend
`tests/unit/test_a2a_call_chain.py` (which already covers the pure module correctly, `:24-50`)
with an explicit inbound chain — targeting `B` raises `A2ACallLoop`, targeting `C` produces
`call_depth=2, call_path=("B","C")`. **U-2** two wiring assertions on either side of the process
boundary: `_dispatch_a2a_workflow_signal` includes the fields, and `agent_invocation` reads them
back out of `ctx.trigger_payload` — the seam that actually broke. **U-3** extend
`tests/unit/test_a2a_scope.py:51-111` with the workflow-run-id-as-context case, keeping
`evaluate` pure and DB-free. **Do not add a workflow-origin branch to it.**

## 9. Risks and Rollback

| Risk | Severity | Mitigation |
|---|---|---|
| **Over-broad grant** — the dominant risk | **high** | Negative wiring tests W-2/W-3/W-4 as merge gates; service-side derivation only (Q-3); Q-6 resolved by the user, not the implementer. **`check-security` should be a required gate for this dossier, not a conditional one.** |
| Definition drift under A1 — a workflow edited mid-run authorizes against the current definition | medium | Choose A2/C, or accept and document |
| Extra DB reads per instruct (run → workflow → definition) | low | Instructs are low-volume by `[R15.16]`. Note the reads happen inside the caller's ambient transaction — `instruct_service.py:97-99` documents that the engine never commits (the DB-1 contract at `run_engine.py:188-192`). **Do not add a commit.** |
| Import cycle `A2AService` → `WorkflowFacade` while `workflow/executors` → `OrchestrationFacade` → `A2AService` | low | Already solved by the lazy import at `a2a_service.py:431` with a comment saying exactly this. Copy it; do not hoist to module level. |
| Behaviour change for projects that worked around F-9 with project-wide `call_only` | low | They keep working — rule 3b is unchanged and evaluated after 3a. Call it out in release notes so operators can **narrow** their now-unnecessary `call_only` grants; leaving them is a wider grant than needed. |
| F-24 propagation spans three processes; a partial deploy | medium | `A2AEnvelope.from_dict:86-87` already defaults `call_depth=0`/`call_path=()`, so envelopes are forward and backward safe. Ensure the workflow-signal payload read uses the same defaulting, so a partial deploy degrades to today's behaviour rather than crashing. |

**Security — what must not weaken**, each with a negative test: cross-project denial
(`a2a_scope.py:79-80`); soft-deleted callee project denial (`:82-83`, fed by the live lookup at
`a2a_service.py:390-391` — note the comment there that `project.soft_delete` does not cascade to
`agents.deleted_at`, so this check cannot be dropped as redundant); the `a2a_enabled` opt-out on
both sides (`:93-96`); issuer identity preserved for `[R15.23]` attribution; fail-closed on
missing context, matching `_enforce_workflow_tenant`'s `workflow_run_id is None` denial
(`:426-427`); and the `a2a.forbidden` audit before every raise (`:401-412`).

**What an over-broad fix exposes.** `[R15.15]`: an instructed agent **cannot refuse**. An instruct
is therefore unrefusable turn execution on the target. A fix granting on "the caller came from
some workflow" hands **any project member who can author a workflow unrefusable execution on
every agent in the project**, permanently, with no opt-out — the target's `call_only=false` would
no longer protect it. That is a privilege-escalation-by-authoring primitive. Note also that
**shipping nothing is safe**: current behaviour fails closed, so there is no pressure to accept a
loose fix. If Q-6 cannot be resolved, keeping the feature broken is the correct action.

**Rollback.** Pure application-layer under A1 and the Part-2 design — revert and behaviour
returns to fail-closed denial (annoying, not dangerous). A2 writes into the existing
`workflow_runs.context` JSONB, revert-safe since old code ignores the extra key. C adds a table
and needs a down-migration. Prefer A1 or A2 if rollback speed is weighted heavily. No cache or
Redis key shapes change under any option.

## 10. Acceptance Criteria

- [ ] AC-1: W-1 (§8) fails against current code and passes after the fix.
- [ ] AC-2: a workflow instruct between two ordinary `a2a_enabled` agents that share the chosen
      context succeeds, with neither agent configured `call_only`.
- [ ] AC-3: W-2 — an instruct targeting a non-participant is denied, with an `a2a.forbidden`
      audit row.
- [ ] AC-4: cross-project and `a2a_enabled=false` denials are unchanged, each pinned by a test.
- [ ] AC-5: the callee's attached-context set is derived inside `A2AService` from the DB; no
      executor-supplied value influences it.
- [ ] AC-6: the delivered envelope carries the real `workflow_run_id`, and a denied instruct
      leaves no orphan `issued` row.
- [ ] AC-7: A2A broadcast reaches a room-mate with `a2a_enabled` and no `call_only`.
- [ ] AC-8: an A2A call chain crossing a process hop preserves `call_depth`/`call_path`, and the
      cycle guard fires on a genuine cycle.
- [ ] AC-9: `[R15.16]` rules 1, 2 and 4 are reachable — an A→B→A instruct chain raises
      `InstructLoopDetected`.
- [ ] AC-10: no test on this path mocks `_enforce_scope`, `a2a_scope.evaluate`, `svc._a2a` or
      `issue_instruct`.
- [ ] AC-11: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.

## 11. SRS Delta

None for the F-9 half — this makes `[R9.17]` rule 3a reachable as written. **Possibly required
for Q-7**: if rule 3's wake-up window is redefined as a run-scoped issuing window, `[R15.16]`
rule 3 must be amended to say so. Draft the delta at approval once Q-7 is answered; do not apply
it before.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `OrchestrationFacade.send_a2a` and `a2a_notify` have no production callers and
  default their authorization parameters permissively. Wire or narrow them before the first
  caller inherits the trap.
- **FU-2** — Permissive defaults on authorization and budget inputs are systemic across
  `a2a_service.py:63-65,132-133,199-200` and `facade.py:51-52,68-69,88-89,247-252`. A default that
  silently means "deny" (F-9) or "no limit" (F-26) on an authorization boundary is a design smell
  independent of these bugs; prefer service-side derivation or a required parameter.
- **FU-3** — Two authorization rules govern two workflow-originated agent invocations
  (`a2a_service.py:88-100`). Correct — they are different relationships — but undocumented at the
  branch itself. Add the WHY comment.
- **FU-4** — The broad `except Exception` in `executors/instruct.py:94-100` and
  `agent_invocation.py:69-75` converts an authorization denial into a generic node failure,
  erasing the distinction between "forbidden" and "the target crashed". This is why F-9 read as a
  flaky feature rather than a bug for as long as it did.
- **FU-5** — `a2a_scope.py:85-91` allows self-invocation unconditionally, **independent of
  `a2a_enabled`**, with a comment explaining it is the `agent_invocation` path. If this fix
  changes how `agent_invocation` routes, re-examine whether that grant is still needed; if it
  becomes dead, removing it narrows the boundary.
- **FU-6** — The mocked-seam testing pattern is the primary debt on this surface. Consider a
  standing rule: any change to `a2a_scope`, `_enforce_scope` or `_enforce_workflow_tenant`
  requires a wiring-tier test.
</content>
