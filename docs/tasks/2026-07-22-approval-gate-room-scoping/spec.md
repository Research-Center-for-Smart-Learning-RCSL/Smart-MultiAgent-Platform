---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R15.10]
depends_on: []
---

# A workflow's approval gate publishes into any chatroom UUID the caller supplies at trigger time, including another project's

## 1. Summary

The `approval_gate` executor resolves the room it publishes into from
`ctx.trigger_payload["chatroom_id"]` — a free-form, caller-authored bag that is size-bounded and
nothing else — and never checks that room against the run's own project
(`backend/contexts/workflow/application/executors/approval_gate.py:67-77`,
`backend/app/api/v1/workflows.py:180-181`, `backend/shared_kernel/validation.py:98`). A member of
project A who can trigger a workflow can therefore make the platform publish a fabricated
`approval.requested` frame — carrying an attacker-authored free-text `question` — into a chatroom
belonging to project B, followed later by `approval.resolved`. This is a cross-tenant authorization
defect on the publish path, not a cosmetic routing bug: the value is used as an authorization-bearing
identifier without ever being derived from a trusted source. Nothing downstream can repair it,
because the room is never persisted (`backend/contexts/orchestration/infrastructure/tables.py:45-78`
has no `chatroom_id` column) — it exists only as an in-flight argument on publishes, notify notes and
Arq jobs.

## 2. Observed vs Expected

**Observed.** `approval_gate.py:67` reads
`config.get("chatroom_id") or ctx.trigger_payload.get("chatroom_id")`, UUID-parses it at `:68-71`,
and hands it to `facade.create_approval_gate(..., chatroom_id=room_id)` at `:73-77` with no scope
check. `ApprovalService.create_gate` publishes to `room_channel(chatroom_id)`
(`backend/contexts/orchestration/application/approval_service.py:97-110`), copies the room into the
approver notify note (`:151`), into the `approval_timeout` job argument (`:162-167`) and into the
`drive_approver_turn` job argument (`:179-185`). From there it reaches a headless agent turn
(`backend/app/workers/tasks/approvals.py:90-94`), the `cast_approval_vote` tool
(`backend/contexts/agents/application/runtime/tool_registry.py:287`) and the `approval.resolved`
publish (`approval_service.py:439-443`). Every one of those consumers treats the value as trusted;
`approvals.py:46-48` says so in as many words — *"`chatroom_id` is the authoritative (server-side)
room the vote is threaded back to"*. It is not server-side.

Three structural facts make the hole the *only* live path rather than one of two:

1. The linter's chatroom-scope rule inspects `node["config"]["chatroom_id"]` exclusively
   (`backend/contexts/workflow/application/linter.py:354-372`), so the trigger-payload branch bypasses
   it entirely.
2. The config branch at `approval_gate.py:67` is **unreachable for any workflow saved through the
   API**. `approval_gate_config` in `docs/workflow.schema.json:264-288` declares
   `"additionalProperties": false` and does not list `chatroom_id`; the schema is enforced on both
   write paths (`backend/contexts/workflow/application/workflow_service.py:134` for create, `:178`
   for patch, via `_validate_schema` at `:426-433`). The editor agrees — the seeded approval-gate
   config carries no room (`frontend/src/slices/workflow/constants.ts:6`) and
   `frontend/src/slices/workflow/components/config/ApprovalGateConfigForm.vue` renders no room field.
   So rule 8 is vacuous for this node type, and the untrusted branch is the sole supplier.
3. `RunContext` carries no project identity (`backend/contexts/workflow/domain/models.py:177-192`),
   so the executor had nothing to compare the room against even if it had wanted to.

**Expected.** A gate's room is an *in-project* room. The intent is stated three times, none of them
honoured on this path:

- `backend/app/api/v1/workflows.py:138-139` — the linter's guarantee that *"a reference to another
  tenant's agent/chatroom is still rejected"*.
- `backend/contexts/agents/application/runtime/turn_engine.py:742-749` — *"the gate's `chatroom_id`
  can be an arbitrary **in-project** room set by the workflow author"*, the comment that also
  documents why room-scoped Concept Maps are nulled for non-member agents at `:750-754`.
- CLAUDE.md's multi-tenant AuthZ rule: every endpoint must verify org/project membership before
  acting on tenant data.

`[R15.10]` (`REQUIREMENTS.md:767-771`) enumerates what an approval-gate node declares — `mode`,
`approvers`, `leader_agent_id`, `timeout_seconds` — and does **not** include a room, which is itself
evidence that the room was intended as author configuration, not as a trigger-time input.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | **Does this fix belong inside `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md`, which rewrites the gate create path?** | **No — independent, `depends_on: []`.** Ship whichever is ready first; the second rebases. | See the evidence block below. |
| Q-2 | Where is the room validated: trigger time, gate creation, or publish time? | **Gate creation, in the executor.** | See the evidence block below. |
| Q-3 | Should the fix also add `chatroom_id` to `approval_gate_config` so the (currently unreachable) config branch becomes usable and lint-covered? | **No — follow-up FU-1.** | That is a feature (author-selectable gate room) plus a schema change plus a UI field plus i18n. The security fix does not need it: after this change the trigger-payload path is validated, so the gate keeps working exactly as it does today, only scoped. Bundling a schema widening into an AuthZ fix enlarges the blast radius of a change whose whole point is to shrink it. |
| Q-4 | What happens when the room is out of scope, missing, or deleted — fail the node, or silently degrade to a headless (room-less) gate? | **Fail the node, loudly, with an audit row.** | Silent degradation is masking: the gate would still be created but no human would ever see the card, and the run would fall to its timeout port with no stated reason (`workflow_approvals.py` maps `TIMEOUT` onward). Failing routes through the existing path — `approval_gate.py:112-118` returns `FAILED`, `run_engine.py:602-610` logs it and `_apply_on_error` (`:733-748`) fails the run under the default `fail` strategy — so no new machinery is needed and the operator sees the real reason in the step's `error`. |
| Q-5 | Add a second, defence-in-depth check inside `create_gate` / the announce job? | **No. Document the trust contract instead.** | `approvals` has no `chatroom_id` column (`tables.py:45-78`), so `contexts/orchestration` cannot re-derive the room from anything it owns; a "check" there would have to re-query the workflow run and the conversation context to re-do what the caller already did, duplicating an authorization rule in two contexts. Instead, state the contract in `create_gate`'s docstring (`approval_service.py:64-70`): *the room argument is validated by the caller against the run's project; it is not persisted and cannot be re-derived here.* One check, one owner. |
| Q-6 | Does this warrant a `check-security` referral in parallel with the bugfix? | **Yes — parallel, non-blocking.** | See §9's Security Considerations. |
| Q-7 | Data repair for definitions or runs already carrying a foreign room? | **Detect and report; never rewrite.** | See §7's data-repair position. |

**Q-1 evidence — why this is independent of the approval-resume-claim-reliability dossier.**

That dossier (`docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md`, draft) does change
the create path substantially, and the coordination question is fair. Reading it against this defect:

- Its Q-2 (`:96`) replaces the inline publishes, notify pushes, timeout arm and approver dispatches
  with a single `enqueue("approval_gate_announce", approval_id, chatroom_id)`; the worker *"opens its
  own session, **re-reads the row**, and only then performs the effects"*.
- **The re-read gives no validation leverage over the room.** The room is a *job argument* in that
  design (`:96`, `chatroom_id` passed alongside `approval_id`), not a column that is re-read — because
  `approvals` has no such column (`tables.py:45-78`). The announce job therefore re-reads the row for
  *existence*, which is what it was designed for, and would still receive an unvalidated room from
  the same untrusted source. Putting the scope check there would mean the announce job re-deriving
  the run's project (`workflow_runs.project_id`, reachable via
  `backend/contexts/workflow/infrastructure/repositories.py:235-246`) and calling into the
  conversation context — i.e. re-implementing in `contexts/orchestration` a rule the workflow context
  can enforce with data it already has.
- **The change surfaces are adjacent, not overlapping.** That dossier's executor edits are Q-4
  (`:98`, the claim key at `approval_gate.py:87-91` stays put) and Q-5 (`:99`, delete the executor's
  publish at `approval_gate.py:93-101`); its service edits are `approval_service.py:97-118,162-185`
  (its §6 Part 1, `:195-204`). This dossier's edit is `approval_gate.py:67-77` — above both, and in
  neither file the other dossier restructures. Its §7 test plan (`:244-266`) extends
  `test_approval_gate_fixes.py` and `test_orchestration_services.py`; this dossier's primary home is
  `test_workflow_k4.py` (§8).
- **Sequencing is free in both directions.** If this lands first, the announce refactor inherits an
  already-validated room and needs no change. If the refactor lands first, this fix still applies to
  `approval_gate.py:67-77` unchanged, because the executor still computes the room and still passes
  it to `create_gate`.
- **And an AuthZ fix should not queue behind a reliability refactor.** The other dossier is a
  three-commit change touching backend and frontend with an open frontend-barrel decision (`:214-219`).
  A cross-tenant publish should not wait on it.

`depends_on: []`. Recorded in §9 as a textual adjacency for whichever build runs second.

**Q-2 evidence — trigger time vs gate creation vs publish time.**

| Point | What it would look like | Verdict |
|---|---|---|
| **Trigger time** — validate or reject `trigger_payload["chatroom_id"]` in `trigger_run` (`workflow_service.py:299-335`) or at the API model (`workflows.py:180-181`) | Fail fast with a 400 the caller can see | **Rejected as the primary check.** It elevates `chatroom_id` into a reserved key of a bag that is documented as a free-form initial-variable map (`shared_kernel/validation.py:96-98`) and is interpolated as `__trigger__` by five executors (`approval_gate.py:29`, `agent_invocation.py:31`, `instruct.py:30`, `condition.py:32`, `set_variable.py:29`, `subagent_spawn.py:51`) with no key semantics at all. It also covers only the API entry: signal-driven runs build their own payloads (`workflow_signals.py:139`) and would need the identical guard duplicated. Useful as an optional early 400 — recorded as FU-7, not as the fix. |
| **Gate creation** — validate in `approval_gate.py` before `create_gate` | The value becomes an authorization decision exactly where it is first used as one | **Chosen.** It is the single choke point every trigger source funnels through (manual, dry-run, cron, message/a2a/activity signals, resume and retry), it is in the context that owns both the run and the payload, and it is upstream of every consumer — publishes, notify note, both job arguments, the tool binding and the resolved publish all receive an already-validated value. |
| **Publish time** — validate in `approval_service`/the announce job | Check immediately before `Publisher(room_channel(...))` | **Rejected.** Too late and structurally weaker: the note (`approval_service.py:151`) and the two job arguments (`:165`, `:183`) already carry the room by then, and under the announce design all of those are dispatched from the same job, so "publish time" and "creation time" collapse into one point anyway — one that cannot re-derive the room (Q-5). |

**Implication for existing saved workflows carrying a foreign or stale room id.** Because the schema
forbids `chatroom_id` on an approval-gate node (`workflow.schema.json:264-288`, enforced at
`workflow_service.py:134,178`), no definition saved through the current API can carry one at all —
so for gate rooms there is no definition-level legacy population to repair, only whatever predates
that constraint. Trigger payloads, however, **are** persisted: `run_engine.py:155` stores them in
`workflow_runs.context` and `_prepare_continuation` reads them back at `:272` (and `retry_node` at
`:313`), so a run created before this fix can still re-execute a gate from a stored foreign room.
The chosen check runs on every execution path that builds a `RunContext`, so those fail closed at
the next attempt rather than needing a data migration. See §7 for the full repair position.

## 4. Reproduction

Preconditions: two projects A and B in the same deployment; user U is a member of project A with
`CHAT_CREATE` (`workflows.py:117-125`); project B contains chatroom `R_B` with at least one member
who has it open; project A contains workflow W whose definition includes an `approval_gate` node
(leader and approvers are project-A agents, so rule 6 passes) with a `question_template` such as
`"Approve payout to {{ __trigger__.who }}?"`.

1. As U, `POST /api/v1/workflows/{W}/runs` with
   `{"trigger_payload": {"chatroom_id": "<R_B uuid>", "who": "attacker-controlled text"}}`
   (`workflows.py:455-480`).
2. `_resolve_workflow` (`workflows.py:71-85`) authorizes U for project A only; `_require_chat_create`
   (`:467`) passes. `trigger_run` stores the payload verbatim (`workflow_service.py:334` →
   `run_engine.py:155`).
3. The run reaches the gate. `approval_gate.py:32` interpolates the question with the caller's text;
   `:67-71` parses `R_B`; `:73-77` creates the gate against it.
4. Observed: every client subscribed to `room_channel(R_B)` receives `approval.requested`
   (`approval_service.py:97-110`) with `question` set to the attacker's string, and later
   `approval.resolved` (`:439-443`). Project-B members see an approval card for a workflow in a
   project they cannot access; `GET /api/orchestration/approvals/{id}` correctly 403s for them
   (`approval_service.py:456-458` is the authz helper), so the card is unresolvable from their side.
5. Control: repeat with `chatroom_id` set to a project-A room and the behaviour is identical, which
   is the point — nothing distinguishes the two today.

Deterministic; no timing dependency. The unit-level equivalent, with no stack, is in §8.

## 5. Root Cause Analysis

Causal chain, earliest link first:

1. `workflows.py:180-181` accepts `trigger_payload: BoundedPayload`. `shared_kernel/validation.py:98`
   bounds bytes, depth and node count — there is no key allowlist and no per-key typing. **This is
   correct and stays correct**: the payload is a variable bag by design.
2. `workflow_service.py:334` → `run_engine.py:155,183` stores and threads the bag unmodified into
   `RunContext.trigger_payload` (`models.py:185`).
3. **Root cause — `approval_gate.py:67`.** The executor promotes one key of that bag to an
   authorization-bearing identifier: `config.get("chatroom_id") or ctx.trigger_payload.get("chatroom_id")`.
   It is parsed as a UUID at `:68-71` (which validates *syntax*, and reads as validation) and used at
   `:73-77`. Correcting this line — re-deriving the trust decision from the run's own project instead
   of accepting the caller's word — prevents the symptom on every path. Every link below it is a
   faithful consumer of a value it was told to trust.
4. Aggravating factor A — `linter.py:354-372` reads only `node["config"]["chatroom_id"]`, so save-time
   scoping never sees this value. The guarantee advertised at `workflows.py:138-139` is therefore
   false for this node.
5. Aggravating factor B — `workflow.schema.json:264-288` (`additionalProperties: false`, no
   `chatroom_id`) makes the *lint-covered* branch of `:67` unreachable, leaving the un-covered branch
   as the only supplier. The two aggravators compose: the covered path cannot be used and the usable
   path is not covered.
6. Aggravating factor C — `models.py:177-192`: `RunContext` has no `project_id`, and
   `_row_to_run` (`repositories.py:44-55`) deliberately omits it from the domain `WorkflowRun`
   (`:239` states the omission is intentional). The executor had no cheap trusted comparand.
7. Aggravating factor D — `tables.py:45-78`: the room is never persisted, so no later stage can
   re-derive or re-check it. This is why the fix must be upstream and cannot be a downstream filter.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Cross-project **event injection**, not disclosure. Every field in the published
payload originates in the caller's own project (`approval_service.py:100-109`: approval id, run id,
mode, the caller's own agent ids, timeout, and the caller-interpolated question), so the caller learns
nothing about project B — the disclosure direction is independently closed at
`turn_engine.py:750-754`. The harm is on the victim's side: fabricated, attacker-worded approval
cards rendered in another tenant's room, plus a matching `approval.resolved`, i.e. a phishing and
trust-erosion surface inside a UI the victim has every reason to trust. Approver agents are the
caller's own (rule 6 scoping, `linter.py:341-346` deferring to rule 6), so provider spend stays on the
caller's key. Persisted damage: none in `approvals` (no room column); the foreign room id **is**
persisted inside `workflow_runs.context` (`run_engine.py:155`) for any run already triggered this way.

**Sibling suspects — every other place a client-supplied chatroom id or project id reaches a publish
or a write without being re-derived from a trusted source.**

| Site | Source of the id | Verdict |
|---|---|---|
| `executors/approval_gate.py:67-77` | `ctx.trigger_payload` (client) | **Confirmed — this defect.** |
| `approval_service.py:97-110,112-118,151,162-167,179-185`; `approvals.py:31-94`; `tool_registry.py:265-289`; `approval_service.py:439-443` | The value produced above | **Confirmed as carriers, no independent input.** Each is a faithful pass-through of one upstream value; all are corrected by the single upstream fix, and none needs its own patch (§7). |
| Other executors reading `ctx.trigger_payload` — `agent_invocation.py:31`, `instruct.py:30`, `condition.py:32`, `set_variable.py:29`, `subagent_spawn.py:51` | Client | **Cleared.** In all five the payload enters only as `__trigger__` inside `interpolate(...)`, producing text or variable values. The id fields they act on are read straight from `node.config` (e.g. `instruct.py:39-43` uses `issuer_agent_id`/`target_agent_id` from config), which rule 6 scopes to the project (`linter.py:111-124,341-346`). No id sink takes a payload value. |
| `agent_invocation` `target_chatroom_id` | Config, lint-covered (`linter.py:361`) | **Cleared as unreachable** — repo-wide grep finds `target_chatroom_id` only in `linter.py:361` and `frontend/src/slices/workflow/components/config/AgentInvocationConfigForm.vue:89-95`; no executor reads it. Dead config, recorded as FU-2. |
| `workflows.py:469-474` passing `project_id=scope.project_id` | Derived from the workflow via `_resolve_workflow` (`:71-85` → `workflow_service.py:65-87`) | **Cleared** — the project is never client-supplied on any workflow route (`_resolve_workspace` `:59-68`, `_resolve_run` `:88-98` follow the same pattern), and the cron path derives it too (`workflow_service.py:319-325` → `repositories.py:173-189`). |
| Signal-driven trigger payloads carrying `chatroom_id` — message (`workflow_signals.py:143-163`) and activity (`:192-210`) | Server-derived: the message path's room is a path parameter gated by `resolve_room_access`/`ensure_can_send` (`app/api/v1/messages.py:184-199`); the activity path's likewise (`app/api/v1/activities.py:267,290,324,343,367`) | **Cleared, with a caveat.** Both are matched by exact equality against the workflow's own *config* room (`event_dispatch.py:61-67`, `:104`), which rule 8 does scope. Caveat: `find_triggered_workflows` (`event_dispatch.py:197-224`) scans **every live workflow in the deployment** with no project filter, so isolation rests entirely on that config value having been lint-scoped at save. Correct today, fragile — FU-3. |
| A2A and wake-up signal payloads | `workflow_signals.py:165-190`; `app/workers/tasks/orchestration.py:170` | **Cleared** — neither carries a `chatroom_id` key at all (`{"agent_id": ...}` and `{target_agent_id, msg_type}`), so they cannot reach `approval_gate.py:67`'s fallback. |
| Cron-triggered runs | `app/workers/tasks/workflow_cron.py:90` | **Cleared** — payload is exactly `{"trigger_type": "cron"}`. |
| Remaining `room_channel(...)` publishers — `app/api/ws/chatroom.py:70,148`; `app/api/v1/observations.py:197`; `app/api/v1/messages.py:205`; `app/api/v1/activities.py:421,450,464`; `app/workers/tasks/activities.py:86-95` | Path parameters behind `resolve_room_access`, or re-derived from a committed row (`messages.py:440,484` use `msg.chatroom_id`; `activities.py:450` uses `activation.chatroom_id`; `tasks/activities.py:86` receives the submission's room) | **Cleared** — every one either re-derives the room from a persisted row or gates the path parameter on room access before publishing. |

The pattern is therefore **not** systemic: `approval_gate.py:67` is the only site in the audited
surface where a client-authored payload value reaches a room-scoped sink unvalidated. That is why
this dossier fixes one line's worth of trust rather than adding a filter at every sink.

## 7. Fix Design

**Part 1 — give the run context its project identity.** Add `project_id: uuid.UUID | None` to
`RunContext` (`contexts/workflow/domain/models.py:177-192`) and populate it on all three construction
sites: `start_run` already receives it as a parameter (`run_engine.py:136`, set at `:178-185`);
`_prepare_continuation` (`:267-274`) and `retry_node` (`:308-315`) resolve it with the existing
`WorkflowRunRepository.get_project_id` (`repositories.py:235-246`) — one indexed single-column read
per continuation. Rejected alternative: adding `project_id` to the domain `WorkflowRun`
(`repositories.py:44-55`), which `:236-240` documents as a deliberate omission; keeping the field on
the mutable execution context rather than the domain entity respects that decision.

**Part 2 — validate at the point of use.** Replace `approval_gate.py:67-71` with: resolve the raw
room (config first, trigger payload second — the precedence stays, so behaviour is unchanged for
in-scope rooms), and when one is present, require it to resolve to the run's project. Room → project
resolution mirrors the existing pattern: `Chatroom.workspace_id`
(`contexts/conversation/domain/models.py:56-58`) → `Workspace.project_id` (`:47`), both already
reachable through `ConversationFacade.get_chatroom` (`interfaces/facade.py:78-84`) and
`get_workspace`. Prefer adding one facade helper, `resolve_chatroom_scope(chatroom_id) -> uuid.UUID | None`,
shaped exactly like `WorkflowService.resolve_workflow_scope` (`workflow_service.py:65-87`), over
`list_chatroom_ids_for_project` (`facade.py:86-91`) — the latter would list a whole project's rooms
on every gate execution. The cross-context call is sanctioned by precedent: the workflow context
already calls `ConversationFacade` at `workflow_service.py:78`.

Mismatch, unknown, or soft-deleted room ⇒ raise. The existing `except Exception` at
`approval_gate.py:112-118` turns that into `StepState.FAILED`, `run_engine.py:602-610` logs it, and
`_apply_on_error` (`:733-748`) fails the run under the default strategy — no new engine machinery.
Also emit an audit row (`approval.gate_room_rejected`, carrying run id, node id, the requested room
and the run's project) via the same `audit.emit` the context already uses
(`approval_service.py:81-95`), so an attempt is visible to the operator rather than only to the log.

**Part 3 — record the trust contract where it is consumed.** Per Q-5, no second check. Amend the
`create_gate` docstring (`approval_service.py:64-70`) to state that `chatroom_id` arrives validated
against the run's project by its caller, and that it is intentionally not persisted
(`tables.py:45-78`) and therefore not re-derivable in this context. This is the comment that stops
the next reader from concluding the announce job (or any future consumer) can re-check it.

**Why this corrects rather than masks.** The defect is not "the wrong room got published to" — it is
"a caller-supplied value was used as an authorization decision". The fix changes the value's
provenance: after it, the room is accepted only if it is independently derivable as belonging to the
run's own project, which is a fact the caller cannot influence (`workflow_runs.project_id` is set from
`scope.project_id` on the API path and from the workflow's workspace otherwise,
`workflow_service.py:314-325`). Every downstream consumer then receives a value that is trustworthy
for the reason `approvals.py:46-48` already claims it is. The masking alternatives are explicitly
rejected:

- *Delete the trigger-payload branch.* This would leave every approval gate permanently headless,
  because the config branch is schema-unreachable (§2 fact 2) — removing the feature, not securing it.
- *Filter or reject the key at the API.* Leaves the executor still trusting whatever reaches it, and
  covers only one of six trigger sources (Q-2).
- *Filter at each publish site.* Six sinks, one rule, and two of them (`approval_service.py:151`, the
  notify note, and `:183`, the approver job) are not publishes at all — the value would already have
  escaped into a note and a job argument before the "filter" ran.

**Data-repair position.** Three populations, three answers, and no blind rewrite of user data:

1. **Workflow definitions.** No definition saved through the current API can carry
   `config.chatroom_id` on an approval-gate node at all (`workflow.schema.json:264-288` enforced at
   `workflow_service.py:134,178`), so there is nothing to repair for rows written under the present
   constraint. For anything predating it, ship a **read-only detection query** over
   `workflows.definition` that reports approval-gate nodes carrying a `chatroom_id`, and separately
   those whose value is outside the owning project (via `workflows → workspaces → projects`, the join
   at `repositories.py:182-189`). Report to the operator; do not auto-edit a customer's workflow —
   the runtime check already fails those closed, so the only cost of leaving one in place is a run
   that fails with a precise reason.
2. **Persisted trigger payloads.** `workflow_runs.context->'trigger_payload'->>'chatroom_id'`
   (`run_engine.py:155`) can hold a foreign room from any run already triggered this way, and
   `_prepare_continuation:272` / `retry_node:313` will re-read it. **No migration**: the Part 2 check
   runs on every path that builds a `RunContext`, so a stale run fails closed on its next gate
   execution. Include these rows in the same detection report so an operator can see whether the
   defect was ever exercised in production; the report is also the evidence base for any incident
   notification a cross-tenant event injection would require.
3. **In-flight gates parked right now.** Their room exists only in a Redis claim key
   (`approval_gate.py:87-91`) and in Arq job arguments (`approval_service.py:162-185`). It is neither
   queryable nor repairable, and both carriers expire. Nothing to do; stated so the absence of a
   repair step is a decision rather than an oversight.

No Alembic migration. No API contract change, so no `pnpm run gen:api`. No frontend change.

## 8. Regression Test Plan

For an AuthZ fix the negative tests are the load-bearing ones — a fix that rejects *everything* also
passes the positive test — so the positive controls below are not optional garnish, they are what
proves the check is not over-broad.

**Primary home: `backend/tests/unit/test_workflow_k4.py`.** It already owns the approval-gate
executor tests — `test_approval_gate_builds_config_and_registers_claim` (`:98-147`) and
`test_approval_gate_failure_returns_timeout_port` (`:150-174`) — with the `_FakeFacade` /
`_FakeRedis` / patched-`Publisher` harness (`:106-118`) these tests reuse. Note that **neither
existing test passes a room at all** (`:132`, `:169` construct a `RunContext` with no
`trigger_payload`), which is precisely why the room-resolution path has never been exercised.

*The failing test comes first:*

- **`test_approval_gate_rejects_trigger_payload_room_outside_run_project`** — build a `RunContext`
  with `project_id = A` and `trigger_payload = {"chatroom_id": str(room_in_B)}`; stub the room→project
  resolver to return `B`. Assert: `outcome.state == StepState.FAILED`; `_FakeFacade.create_approval_gate`
  was **never** called; `fake_redis.kv` contains no `wf:approval:*` key; the patched `Publisher`
  emitted nothing. **Fails today** on all four assertions — `approval_gate.py:67-77` parses the
  foreign room and calls `create_approval_gate` with it, `:87-91` writes the claim key, `:93-101`
  publishes, and the outcome is `RUNNING` with `park=True`.

*Negative tests, equal weight:*

- **`test_approval_gate_rejects_config_room_outside_project`** — same assertions via the
  `config["chatroom_id"]` branch. Unreachable through the API today (§2 fact 2), which is exactly why
  it is worth pinning: it guarantees FU-1's schema widening cannot silently reopen the hole. **Fails
  today** — `:67` prefers config and passes it through unchecked.
- **`test_approval_gate_rejects_unknown_or_deleted_room`** — resolver returns `None`; assert `FAILED`
  and no gate created. **Fails today** — a syntactically valid UUID for a non-existent room is
  accepted at `:68-71` and reaches `create_gate`.
- **`test_approval_gate_rejects_malformed_room_instead_of_ignoring_it`** — `trigger_payload =
  {"chatroom_id": "not-a-uuid"}`. **Fails today** — `:70-71` swallows the parse error and sets
  `room_id = None`, silently producing a headless gate where the author asked for a room. Decide and
  pin the post-fix behaviour here (fail closed, consistent with Q-4) rather than leaving a second
  silent-degradation path behind.
- **`test_approval_gate_room_rejection_is_audited`** — assert one `approval.gate_room_rejected` audit
  event carrying the requested room and the run's project. **Fails today** — no such event exists.

*Positive controls — these are what stop the fix being over-broad:*

- **`test_approval_gate_accepts_trigger_payload_room_in_run_project`** — resolver returns the run's
  own project; assert the gate is created **with that room**, the claim key is written, and
  `outcome.park is True`. Passes today for the wrong reason (no check at all); after the fix it is the
  only thing proving in-project trigger-payload rooms still work.
- **`test_approval_gate_without_room_stays_headless`** — no room anywhere (the existing `:132`
  context); assert `create_approval_gate` is called with `chatroom_id=None`, no resolver call is made,
  and the gate parks normally. Guards against the fix accidentally making a room mandatory and
  breaking every headless gate.
- The two existing tests at `:98-147` and `:150-174` must continue to pass unmodified; if
  `RunContext` gains a required field they will fail to construct, which is the signal that
  `project_id` must be optional-with-default (`models.py:177-192`).

**`backend/tests/unit/test_workflow_reference_scoping.py`** — pin the constraint the whole analysis
rests on: **`test_approval_gate_config_rejects_chatroom_id`**, validating a definition with an
approval-gate node carrying `chatroom_id` against `workflow_service._get_schema()` and asserting a
validation error. This is a **pin, not a regression test** — it passes today
(`workflow.schema.json:266,268-281`) and exists so that a future schema edit that adds the field
without adding lint coverage fails here. This file already holds the sibling rule-6/rule-8 scoping
tests (`:36-59`).

**`backend/tests/unit/test_approval_gate_fixes.py`** — one characterization test on the propagation
surface, in the file the coordinating dossier also edits (its §7, `:244-266`), so the two builds
collide visibly rather than silently:
**`test_create_gate_propagates_exactly_one_room_to_every_sink`** — assert the room reaching the room
publish (`approval_service.py:97-110`), the notify note (`:151`), the `approval_timeout` argument
(`:165`) and the `drive_approver_turn` argument (`:183`) is identical to the one `create_gate` was
called with, and that no sink derives a room from anywhere else. Passes today; its job is to fail
loudly if any refactor (including the announce job) introduces a second room source that would bypass
the executor's check.

**Not added.** No integration or e2e test: the reproduction spans an async run and a WS subscriber,
and the unit assertions above pin the decision point precisely. No frontend test — no frontend change.

**Gates:** `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` in `backend/`.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| A legitimate gate that relied on a room that is in-project but **soft-deleted** now fails the run instead of parking | Decide the deleted-room case explicitly in the resolver: `get_chatroom` takes `include_deleted` (`facade.py:78-84`). Recommend treating a soft-deleted in-project room as *out of scope* (fail) for consistency with the linter, whose valid set is live-only (`facade.py:86-91`) — and covering it with `test_approval_gate_rejects_unknown_or_deleted_room` so the behaviour is a decision, not an accident |
| Two extra queries per gate execution (project resolve + room resolve) | Both single-row indexed lookups on a path that already performs an insert, an audit write, two publishes and two enqueues. The project resolve is skipped entirely when no room is supplied (headless gates pay nothing) |
| `RunContext` gains a field that three constructors must set; missing one yields `project_id=None` and, if written carelessly, a check that silently passes | Make the absent-project case **fail closed**: a room supplied with no resolvable run project is rejected. Assert it directly rather than leaving it implied |
| `on_error: continue` turns a rejected room into a silent branch death (`run_engine.py:743-748` returns port `default`, which is not in `_ALLOWED_PORTS["approval_gate"]`, `linter.py:36`) | Pre-existing for every approval-gate failure, not introduced here; recorded as FU-5. The default strategy is `fail`, which surfaces the reason |
| Textual adjacency with `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md` | Per Q-1 the edits do not overlap (`approval_gate.py:67-77` here vs `:87-101` there, per its `:98-99`); whichever lands second rebases `approval_gate.py` and re-runs both test files |

**Rollback.** One commit, `fix(backend): scope approval-gate rooms to the run's project`. No
migration, no persisted state, no API contract change, no frontend change. Reverting restores the
unvalidated resolution — acceptable only as an emergency measure, and it must be paired with a note
that the cross-tenant path is live again.

### 9.1 Security Considerations

**Classification.** Cross-tenant authorization failure on a publish path — a client-authored value
used as a tenancy-bearing identifier. It is *injection*, not disclosure (§6), which lowers its
severity but does not change its class: the platform performs an action in tenant B on tenant A's
instruction, and the attacker controls a free-text field rendered in B's UI
(`approval_gate.py:32` interpolates `question_template` with caller-supplied `__trigger__` values, and
`approval_service.py:108` publishes it). Treat it as an AuthZ defect for triage, review depth and
disclosure purposes, and use §7's detection query to determine whether it was ever exercised in
production.

**What must not weaken.**

- *The disclosure direction stays closed.* `turn_engine.py:750-754` nulls `knowledge_chatroom_id`
  unless the agent is a member of the room, with the comment at `:742-749` naming this exact case.
  That guard is what keeps a foreign-room headless turn (`approvals.py:90-94`) from pulling B's
  room-scoped Concept Maps. The fix must not be taken as licence to relax it — after this change the
  room is in-project, but "in-project" is still not "the agent is a member", and the two guards
  protect different things.
- *Rules 6 and 8 stay authoritative.* `linter.py:341-346` and `:354-372` remain the save-time scoping
  for agent and chatroom references, and `_linter_valid_ids` (`workflows.py:128-151`) remains the only
  source of the valid sets. This fix adds a runtime check; it does not license removing a save-time
  one.
- *The API keeps deriving `project_id` from the resource, never from the request body*
  (`workflows.py:59-98`). Nothing here should introduce a client-supplied project.
- *Fail-closed direction.* Every new branch rejects on absence or ambiguity (unknown room,
  unresolvable project, malformed UUID). A "when in doubt, publish headless" default would convert an
  injection bug into a silent-loss bug.

**What an over-broad fix would expose or break.** Each of these looks tighter and is worse:

- *Requiring the room to be one the approver agents belong to.* Directly contradicts the documented
  contract at `turn_engine.py:742-749` ("an arbitrary **in-project** room set by the workflow
  author") and would silently kill legitimate gates — a gate is frequently posted to a human-facing
  room that none of the approver agents is bound to. It would also make the check depend on
  `is_agent_in_chatroom` (`facade.py:117-123`) — mutable state that can change between gate creation
  and resolution, turning a stable authorization rule into a race.
- *Validating against "rooms the triggering user can access" rather than the run's project.*
  Introduces a second, weaker authority. It breaks every run with no user —
  `started_by_user_id` is nullable and is `None` for cron (`workflow_cron.py:90`) and for every
  signal-driven run (`workflow_signals.py:308-320`), per `run_engine.py:140` — so those would either
  fail or need an exemption, and an exemption is exactly the hole being closed.
- *Rejecting any `trigger_payload` key named `chatroom_id` at the API boundary.* Beyond the Q-2
  objections, if that guard were later generalised to the signal paths it would break the
  message and activity payloads that legitimately carry the key (`workflow_signals.py:139,144,193`).
- *Adding a filter at each of the six sinks instead of at the source.* Two of the six are not
  publishes (`approval_service.py:151` note, `:183` job argument), so the value would already have
  escaped before any filter ran — and a rule implemented six times is a rule that will be implemented
  five times after the next refactor.

**`check-security` referral: yes, in parallel, non-blocking.** Justification rather than reflex:

- This dossier fixes *one* sink with high confidence and clears the rest of the audited surface with
  evidence (§6). But §6's clearance rests on a static read of one area — the a2a-orchestration audit
  explicitly did not cover `contexts/knowledge`, most of `contexts/conversation`, or the sandbox
  (`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md:50-60`). The *class* — a
  client-authored payload key reaching a tenancy-scoped sink — is broader than this dossier's remit.
- `check-security`'s AuthZ and cross-room-leakage dimensions are the right instrument for that class,
  and its scope should be stated as: client-supplied identifiers (`chatroom_id`, `agent_id`,
  `project_id`, `workspace_id`) reaching a publish, an enqueue, or a write without re-derivation,
  across `contexts/workflow`, `contexts/orchestration` and `contexts/conversation`.
- **Parallel, not blocking.** The fix is small, evidence-backed and self-contained; holding a
  cross-tenant publish fix pending a broader audit trades a certain harm for a speculative one. Run
  `check-security` on the resulting diff as `/build`'s conditional gate (which an AuthZ change
  triggers regardless), and open the broader sweep as its own referral.

## 10. Acceptance Criteria

- [ ] **AC-1**: `test_approval_gate_rejects_trigger_payload_room_outside_run_project` (§8) fails
      against current code and passes after the fix.
- [ ] **AC-2**: an approval gate is never created, no claim key is written, and no event is published
      when the resolved room does not belong to the run's project — whether the room came from
      `config` or from `trigger_payload`.
- [ ] **AC-3**: an out-of-scope, unknown, deleted, or malformed room fails the node with a stated
      reason and an `approval.gate_room_rejected` audit row; it never degrades silently to a headless
      gate.
- [ ] **AC-4**: an **in-project** room supplied via `trigger_payload` still produces exactly the
      behaviour it does today — gate created with that room, claim key written, node parked.
- [ ] **AC-5**: a gate with no room anywhere still parks as a headless gate, and performs no project
      or room lookup.
- [ ] **AC-6**: the room reaching the room publish, the approver notify note, the `approval_timeout`
      argument and the `drive_approver_turn` argument is in every case the single validated value
      (`test_create_gate_propagates_exactly_one_room_to_every_sink`).
- [ ] **AC-7**: `create_gate`'s docstring states the trust contract and the reason it cannot re-check
      (the room is not persisted).
- [ ] **AC-8**: the detection report from §7 exists as a runnable read-only query for both
      populations (definitions, persisted trigger payloads) and rewrites nothing.
- [ ] **AC-9**: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.

## 11. SRS Delta

None. `[R15.10]` (`REQUIREMENTS.md:767-771`) enumerates the fields an approval-gate node declares and
does not include a room; the in-project constraint this fix enforces is stated in
`turn_engine.py:742-749` and in the linter guarantee at `workflows.py:138-139`, both of which the fix
restores rather than changes.

Recorded for a future decision, not drafted here: if FU-1 proceeds and the gate's room becomes an
author-declared config field, `[R15.10]`'s field list should gain `chatroom_id` (optional, in-project)
at that time. That is a feature decision, not a correction of the SRS.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — Add `chatroom_id` to `approval_gate_config` (`docs/workflow.schema.json:264-288`), make
  `rule_08_chatroom_scope` (`linter.py:354-372`) non-vacuous for this node type, and surface the field
  in `ApprovalGateConfigForm.vue` with i18n. Today the author-facing, lint-covered way to set a gate's
  room does not exist; only the trigger-payload path does. §8's
  `test_approval_gate_rejects_config_room_outside_project` is the guard that keeps this from
  reopening the hole.
- **FU-2** — `target_chatroom_id` is linted (`linter.py:361`) and offered by the editor
  (`AgentInvocationConfigForm.vue:89-95`) but read by no executor. Dead config that reads as a live
  feature: either wire it or remove it from both the linter and the form.
- **FU-3** — `find_triggered_workflows` (`event_dispatch.py:197-224`) scans every live workflow in the
  deployment with no project filter; cross-tenant isolation for signal-driven triggers rests entirely
  on the matched config value having been lint-scoped at save time. Correct today, one bad save away
  from not being. Add the project join (the pattern exists at `repositories.py:182-189`).
- **FU-4** — `_KNOWN_CTX_KEYS` (`linter.py:56-66`) advertises `ctx.chatroom_id`, `ctx.workspace_id`
  and `ctx.project_id` to expression authors, but `__ctx__` is built with only `run_id` and
  `workflow_id` at every executor (`approval_gate.py:30`, `instruct.py:31`, `condition.py:32`,
  `set_variable.py:29`, `agent_invocation.py:31`, `subagent_spawn.py:51`). Referencing an advertised
  key yields a silent empty. Note the overlap with FU-1: adding `ctx.chatroom_id` would create a
  *third* room source and must not bypass the check this dossier installs.
- **FU-5** — `approval_gate.py:116` returns port `"failure"`, which is not in
  `_ALLOWED_PORTS["approval_gate"]` (`linter.py:36`), and `on_error: continue` rewrites a failure to
  port `default` (`run_engine.py:743-748`), also not allowed. Under either, a failed gate advances to
  a port no edge may declare and the branch dies silently (`run_engine.py:716-717`).
- **FU-6** — `approvals` does not persist `chatroom_id` (`tables.py:45-78`), so the gate's room lives
  only in Redis and Arq job arguments. A lost job loses the routing permanently and nothing can
  reconstruct it. Persisting the room would also let a future consumer re-derive it rather than trust
  an argument (see Q-5).
- **FU-7** — Optional early rejection at the API: a 400 when `trigger_payload["chatroom_id"]` is not a
  room in the workflow's project (`workflows.py:455-480`). Rejected as the primary fix (Q-2) because
  it covers one of six trigger sources, but it turns a run-time failure into an immediate, legible
  error for the common interactive case.
</content>
