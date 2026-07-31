---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: [R15.10a, R15.18, R15.20, R15.22]
depends_on: []
---

# The three `workflow_capabilities` flags are stored, displayed, inherited — and read by nothing

## 1. Summary

`agents.workflow_capabilities` holds three operator-facing switches — `can_instruct`,
`can_approve`, `can_create_subagent` — plus a `max_alive_subagents` number. All four are
persisted, echoed by the API, rendered as live controls in the agent editor, and explicitly
forced `false` when a sub-agent inherits from its parent. **No backend code ever reads any of
them to make a decision.** A workflow author can name any agent in the project as an instruct
issuer, an approval-gate approver, or a sub-agent parent, and the capability the operator set to
deny that behaviour has no effect. For `can_approve` this is not theoretical: the approval path
is fully live and spends the conscripted approver's own provider key.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-13 (major,
confirmed), and its sibling `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-21
(minor, confirmed) on `max_alive_subagents`.

**Scope, stated first — the three flags are not in the same state, and this dossier does not
pretend they are.**

| Flag | Live consumer path? | Enforceable now? |
|---|---|---|
| **`can_approve`** | **Yes, end to end** — gate → notify → headless approver turn → `cast_approval_vote` | **Yes. Highest value; real provider spend behind it.** |
| **`can_instruct`** | **Yes, the issuing half** — the executor and `InstructService.issue` run on every instruct node | **Yes, at the issuing boundary.** The *allowed* outcome is not observable end to end until `2026-07-22-a2a-scope-context-wiring` lands (see §3 Q-4) |
| **`can_create_subagent`** | **No.** `SubagentService.spawn` has exactly one production caller, and `2026-07-22-subagent-spawn-fail-fast` removes it | **No runtime gate. Save-time advisory only.** A runtime check would be unreachable code |

So the answer to "is this implementable today" is **(b) partially** — and the split is not the
one the finding's headline implies. The headline pairs `can_create_subagent` with `can_instruct`;
in fact `can_approve`, which the headline omits and the finding's own grep includes, is the only
flag whose enforcement is both live and complete today.

## 2. Observed vs Expected

### 2.1 The shared observation

`workflow_capabilities` is loaded onto the domain model
(`backend/contexts/agents/infrastructure/repositories.py:85`,
`backend/contexts/agents/domain/models.py:163`), accepted and echoed by the API
(`backend/app/api/v1/agents.py:90,124,148,174,236,334`), and written on create
(`backend/contexts/agents/application/agent_service.py:447`) and patch (`:616-617`). A repo-wide
grep for `can_create_subagent|can_instruct|can_approve` across `backend/` returns **only write
sites**: the R15.22 documentation table
(`backend/contexts/orchestration/domain/models.py:356-370`), the child's inherited `run_context`
(`backend/contexts/orchestration/application/subagent_service.py:278-280`), and tests. There is
no `.get(...)` against `workflow_capabilities` anywhere, so a variable-key lookup is ruled out
(config audit F-21).

The bootstrap default is the empty dict (`backend/app/bootstrap/seed.py:274`), which the frontend
reads as three `false` toggles (`AgentDetailView.vue:390-394`). **Every agent in every existing
deployment therefore currently denies all three capabilities on paper and permits all three in
fact.** That asymmetry is what makes §7's data-repair question load-bearing rather than cosmetic.

### 2.2 `can_approve` — a live, unguarded, key-spending path

`backend/contexts/workflow/application/executors/approval_gate.py:50-52` takes the node's
`approvers` list verbatim, folds in `leader_agent_id`, and calls
`facade.create_approval_gate` (`:73-77`). `ApprovalService.create_gate`
(`backend/contexts/orchestration/application/approval_service.py:64-79`) inserts the gate with no
agent lookup at all — the service holds no `AgentsFacade` (`:55-58`, contrast
`instruct_service.py:49`). It then calls `_notify_and_arm` (`:124-129`), which pushes a
pending-notify to every approver and enqueues one `drive_approver_turn` job per approver
(`:168-185`). That worker runs a **headless LLM turn** for the approver
(`backend/app/workers/tasks/approvals.py:31-49`), which drains the note and exposes the
`cast_approval_vote` tool (`backend/contexts/agents/application/runtime/turn_engine.py:1637`;
`backend/contexts/agents/application/runtime/tool_registry.py:255-292`). `cast_vote`
(`approval_service.py:198-216`) checks only gate state and approver membership — never the
capability.

Per `[R15.14]` (`REQUIREMENTS.md:775`) approvers spend **their own** Key Group. So naming an
agent as an approver conscripts that agent's provider key into a turn, and the switch that exists
to prevent exactly that is inert.

### 2.3 `can_instruct` — a live issuing path, unguarded

`backend/contexts/workflow/application/executors/instruct.py:39-43` calls
`facade.issue_instruct` with the config's `issuer_agent_id` unvalidated.
`InstructService.issue` (`backend/contexts/orchestration/application/instruct_service.py:51-124`)
runs four `[R15.16]` chain checks and no capability check, then INSERTs and dispatches
(`:126-156`). It already holds an `AgentsFacade` (`:49`) and already calls `get_agent` elsewhere
(`:233,246`), so the lookup the check needs is one line of an object that is already constructed.

### 2.4 `can_create_subagent` — no live path to gate

`backend/contexts/workflow/application/executors/subagent_spawn.py:64-73` is the sole production
caller of `ensure_subagent_root` / `spawn_subagent`. `SubagentService.spawn`
(`backend/contexts/orchestration/application/subagent_service.py:95-140`) enforces `[R15.19]`
depth (`:113-129`) and `[R15.20]` concurrency (`:131-140`) and never reads the parent's
capability. But `docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md` §7 replaces that executor
body with an immediate `failure` outcome **before** either facade call, on the explicit ground
that an unreachable branch is the debt that produced the defect. After it lands, `spawn` has zero
production callers. Adding a capability gate inside it would create exactly the unreachable
branch that dossier refuses.

### 2.5 `max_alive_subagents` — a live-looking control with three different names and no reader

`AgentDetailView.vue:1198-1207` renders the number box whenever `canCreateSubagent` is true and
`:428` sends it. Nothing reads it: the only backend hits for `max_alive|alive_subagent` are
`subagent_spawn.py:45,72`, which read `max_alive_simultaneously` from the **workflow node's**
config, a different field on a different entity (config audit F-21). The concept carries three
names — `max_subagents_alive_simultaneously` (`REQUIREMENTS.md:793`), `max_alive_subagents`
(`docs/UI/06-agents.md:429`), `max_alive_simultaneously` (`subagent_spawn.py:45`) — which is why
no one noticed they were never joined up.

Two concrete divergences from the spec, both persisted:
- **The out-of-range `0`.** `AgentDetailView.vue:1203-1206` binds a plain `ref` outside the
  vee-validate schema through `SInput`'s number coercion
  (`frontend/src/shared/ui/SInput.vue:81-85`, no empty-string guard — config audit F-22), and
  `agents.py:90` types the container as `BoundedConfig`, a size and depth bound with no key-level
  validation. Clearing the box to retype persists `0`, and `:394`'s `?? 5` keeps it on reload.
- **The wrong default.** `AgentDetailView.vue:347,394` default to `5`. `[R15.20]`
  (`REQUIREMENTS.md:793`) and `SUBAGENT_MAX_CONCURRENT_DEFAULT`
  (`contexts/orchestration/domain/models.py:352`) both say **3**.

### 2.6 Expected

- **`[R15.18]`** (`REQUIREMENTS.md:791`) — "An agent with `workflow_capabilities.can_create_subagent
  = true` **may** call `spawn_subagent`". The permission is conditional; today it is unconditional.
- **`docs/implement/G-orchestration.md:250`** — "AuthZ tap. A2A scope = R9.17 runtime check;
  **instruct + subagent require `workflow_capabilities`**." This is the only statement that names
  a *runtime* capability check, and it names the layer: an AuthZ tap, not a UI hint.
- **`[R15.22]`** (`REQUIREMENTS.md:809-810`; `G-orchestration.md:200-201`) forces all three flags
  `false` for a sub-agent, with the stated reason "Enforces depth = 1" and "Sub-agents do only the
  delegated task". A flag forced false to enforce something must be read by something.
- **`can_approve`** has **no `[Rxx.yy]` of its own.** `[R15.10]`–`[R15.14]` describe the gate and
  never mention the capability. Its intent source is `G-orchestration.md:250` (which names
  instruct and subagent, not approval), `docs/UI/06-agents.md:426-427`, and the `[R15.22]`
  inheritance row. This gap is stated rather than papered over, and is why §3 Q-1 exists.
- **`max_alive_subagents`**: `docs/UI/06-agents.md:429` — "int, 1-20, required when
  `can_create_subagent`"; `[R15.20]` — default 3, hard cap 20.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | `can_approve` has no requirement of its own. Is enforcing it in scope, or is that inventing behaviour? | **In scope.** | Three independent intent sources agree it is a permission and not a label: `[R15.22]` forces it `false` for sub-agents "so they do only the delegated task" (`REQUIREMENTS.md:810`), the UI documents it as a capability toggle (`docs/UI/06-agents.md:427`), and `G-orchestration.md:250` establishes the class ("AuthZ tap"). It is also the only one of the three with a fully live consumer and a direct cost consequence (`[R15.14]`, `REQUIREMENTS.md:775`). Enforcing it restores the documented model rather than inventing one. §11 proposes the missing SRS line. |
| Q-2 | Does `can_create_subagent` get a runtime gate? | **No.** | `2026-07-22-subagent-spawn-fail-fast` §7 removes the only production caller of `SubagentService.spawn`. A gate there would be code no test can reach for the right reason. Recorded as an acceptance criterion for the sub-agent feature dossier (§13 FU-1). |
| Q-3 | Then how is `can_create_subagent` honoured at all today? | **Save-time advisory warning only, and say plainly that is all it is.** | The linter is the one place that sees a `parent_agent_id` reference on a path that actually executes today (`workflows.py:337,382,419`). It cannot stop a run; it can stop an author from believing the flag works. |
| Q-4 | `2026-07-22-a2a-scope-context-wiring` establishes that workflow instruct is denied for **every** ordinary agent pair (its F-9). Does gating `can_instruct` sit behind a gate that already rejects everything? | **The gate fires *before* that denial, not behind it — and this dossier is therefore not blocked by it. `depends_on: []`.** | The order is: executor (`instruct.py:39-43`) → `InstructService.issue` → chain checks → INSERT (`instruct_service.py:128`) → `self._a2a.send` (`:156`) → scope denial. A capability check placed at the top of `issue` runs strictly earlier. Today both a permitted and a denied issuer end at the node's `failure` port, but for **different reasons, with different audit rows, and with different persisted side effects** — a denied issuer must leave no `instructions` row, whereas today the F-9 denial leaves an orphan `issued` row (a2a dossier §2). That difference is observable and testable now. What is **not** observable now is the end-to-end *success* of a permitted instruct; that assertion belongs to the a2a dossier's W-1 and is named as such in §8. Blocking this dossier on one that carries an unresolved user decision on an authorization boundary (a2a §3 Q-6) would stall an independently correct fix; the two touch different lines of the same file and are trivially rebasable. |
| Q-5 | Blocking lint error, or advisory warning? | **Advisory warning, for all three flags.** | Two reasons. (i) A blocking rule makes capability *revocation* lock authors out of unrelated edits to every workflow referencing the agent — `workflow_service.py:135-140` (create) and `:179-184` (patch) share one validator and `:185-189` raises on any error, so the edit that removes the offending node is blocked along with everything else. This is the same lockout argument as `subagent-spawn-fail-fast` §3 Q-5, and it applies with more force here because a capability can be withdrawn at any time. (ii) For instruct and approval the runtime gate is the real enforcement, so the linter is defence in depth, not the boundary. `validate_definition` computes `valid` from errors alone (`linter.py:824-829`), so a warning is non-blocking on both paths with no create/patch asymmetry. |
| Q-6 | A gate lists five approvers; two lack `can_approve`. Drop them, or reject the gate? | **Reject the whole gate.** | Dropping them silently changes the tally denominator: `majority` is "&gt;50 % of listed approvers" (`[R15.12]`, `REQUIREMENTS.md:773`) and `consensus` requires all of them (`[R15.13]`). Removing approvers would quietly convert a 3-of-5 gate into a 2-of-3 gate — a changed decision rule, not a changed participant list. Rejecting is deterministic and routes to the node's existing `failure` port via `approval_gate.py:112-118`. |
| Q-7 | Where does the check live — executor or service? | **Service** (`InstructService.issue`, `ApprovalService.create_gate`). | Same constraint the a2a dossier fixes in its Q-3: an executor-supplied authorization input is self-authorization. The service must derive the fact from the DB. It is also the only placement that covers a future agent-facing tool: `OrchestrationFacade.issue_instruct` (`interfaces/facade.py:241-264`) and `cast_approval_vote` (`:199-214`) are public surface. |
| Q-8 | **Every existing agent has `workflow_capabilities: {}`. Enforcing on deploy breaks every currently-working approval gate. What is the migration position?** | **Decided (2026-07-31): (i) derived, narrow backfill.** | Three options, detailed in §7.5. (i) grant `can_instruct` / `can_approve` only to agents actually named in that role by a saved, non-deleted workflow definition — preserves today's effective behaviour exactly, widens nothing an author had not already wired, and mirrors the derived insert-only backfill precedent in `2026-07-22-egress-allowlist-provisioning`. (ii) blanket-grant every agent — rejected, widest possible grant. (iii) no backfill: operators grant manually — fails **closed** and is the most honest, but breaks running approval workflows at deploy with no warning. User confirmed (i) at spec approval. |
| Q-9 | Does the persisted `0` get repaired, or only prevented? | **Both.** | See §7.6. Prevention without repair leaves rows that a newly added `1..20` validator rejects on the *next* unrelated PATCH, converting a dormant bad value into a user-visible 422 on an unrelated edit. |

## 4. Reproduction

**R-A — `can_approve` (live, deterministic, spends a key).**
1. Create agents A and B in one project. Leave `workflow_capabilities` at its default — the UI
   shows all three toggles off (`AgentDetailView.vue:390-394`). Explicitly toggle
   "Can approve actions" **off** and save, so the stored dict is `{"can_approve": false, ...}`
   rather than `{}`.
2. Save a workflow `trigger → approval_gate → end` with `approvers: [A, B]`,
   `leader_agent_id: A`, `mode: majority`, and both resolution ports wired.
3. Trigger a run.
4. Observed: the gate is created, an `approval.requested` audit row is written
   (`approval_service.py:81-95`), one `drive_approver_turn` job is enqueued per approver
   (`:179-185`), and each approver runs a headless LLM turn against **its own** key group. An
   agent whose operator explicitly denied approval authority just spent that operator's money
   voting.

**R-B — `can_instruct` (live issuing half).**
1. Agents A and B, `can_instruct` explicitly false on A.
2. Workflow `trigger → instruct → end` with `issuer_agent_id: A`, `target_agent_id: B`, both
   ports wired.
3. Trigger a run.
4. Observed: `InstructService.issue` runs every `[R15.16]` check and none of them concern A's
   authority; a row is INSERTed (`instruct_service.py:128-136`) and dispatch is attempted. The
   node then fails at the A2A scope check — **for the unrelated reason in the a2a dossier's F-9** —
   with `error="a2a denied: no shared context..."` and an orphan `issued` row left behind. The
   capability produced no part of that outcome; grant it and nothing changes.

**R-C — `max_alive_subagents` persists `0`.**
1. Open an agent, enable "Can create sub-agents", clear the number box to retype, save.
2. Observed: `0` is sent (`AgentDetailView.vue:428` via `SInput.vue:81-85`), accepted by
   `BoundedConfig` (`agents.py:90`), persisted, and redisplayed as `0` (`:394`'s `?? 5` does not
   replace a stored zero). `docs/UI/06-agents.md:429` requires 1-20.

**Not reproducible, and stated as such:** there is no runtime scenario for
`can_create_subagent`, because there is no sub-agent runtime. The closest observation is that
`spawn` reads the parent agent (`subagent_service.py:143-147`) for the inheritance dict only and
never consults its capabilities.

## 5. Root Cause Analysis

**Root cause: `workflow_capabilities` was specified as an AuthZ input
(`G-orchestration.md:250`) and implemented as a storage field.** Every layer that touches it
treats it as opaque JSON to be round-tripped, and the one document that says otherwise is an
implementation checklist, not a code path.

The causal chain, each link a place where the value could have become a decision and did not:

1. **Persistence is untyped.** `tables.py:63` is JSONB with a `'{}'` server default and
   `agents.py:90` validates it as `BoundedConfig` — a size and depth bound
   (`shared_kernel/validation.py`), no key-level schema. There is no place where the shape of the
   dict is declared, so there is nothing to derive a reader from.
2. **The domain model carries it as `dict[str, Any]`** (`agents/domain/models.py:163`), so no
   consumer is prompted by a type.
3. **The services that would decide do not look.** `ApprovalService` holds no `AgentsFacade` at
   all (`approval_service.py:55-58`). `InstructService` holds one (`:49`) and uses it only for
   project resolution (`:233,246`).
4. **The linter, which does scope agent references, was never given the input.**
   `validate_definition` (`linter.py:793-799`) already accepts `valid_agent_ids`,
   `valid_chatroom_ids` and `subagent_parent_ids`, and `_collect_agent_ids` (`:111-126`) already
   extracts exactly the five config keys that matter — `agent_id`, `target_agent_id`,
   `issuer_agent_id`, `leader_agent_id`, `parent_agent_id` — plus `approvers`. The extraction
   exists; only the capability predicate is missing.
5. **The frontend discards the data it was given.** `projectAgents.ts:21` maps the agents list to
   `{id, name}`, dropping `workflow_capabilities` even though `agents.py:148` returns it. Every
   config form's picker (`InstructConfigForm.vue:25-28`,
   `ApprovalGateConfigForm.vue:32`, `SubagentSpawnConfigForm.vue:24-26`) therefore offers every
   project agent for every role.

**The earliest link whose correction prevents the symptom is (3)** — a service-side capability
read. (4) and (5) are earlier in time but cannot prevent the symptom, only warn about it; a
workflow definition can be written by any path that reaches the service.

**Aggravating factor, and the reason this was invisible:** `SUBAGENT_INHERITANCE`
(`orchestration/domain/models.py:356-370`) documents the flags as meaningful, and
`test_orchestration_services.py:727-729` asserts they are forced false. The codebase already
knows this — `:741-743` carries the comment "SUBAGENT_INHERITANCE is read by nothing at runtime —
it documents R15.22 — so only a test can stop it from drifting". Tests assert the *shape* of a
value nothing consumes, which reads as coverage.

## 6. Blast Radius and Sibling Suspects

**Blast radius of the defect.** Project-scoped policy bypass, not a tenant-boundary bypass — the
linter's `valid_agent_ids` scoping (`rule_07_agent_scope`, fed from
`workflows.py:146`) still confines every reference to the workflow's own project, and
`a2a_scope.py:79-80` independently denies cross-project A2A. Within a project, the effect is that
any member who can author a workflow (`_require_chat_create`, `workflows.py:117-125`) can name any
agent in any orchestration role. For `can_approve` this converts into real spend on the named
agent's key group. Data already written: every agent row carries a `workflow_capabilities` that
has never gated anything, plus any `max_alive_subagents: 0` from R-C.

**Blast radius of the fix.** Every currently-working approval gate whose approvers lack an
explicit grant starts failing on deploy, unless Q-8 resolves to backfill. This is the dominant
risk and is why Q-8 is a user decision (§9 R1).

**Sibling suspects:**

| Site | Same pattern? | Evidence |
|---|---|---|
| **`can_approve`** — omitted from F-13's headline, present in its grep | **Confirmed.** The most live of the three. | `approval_service.py:64-79,198-216` — no agent lookup at any point; the service has no `AgentsFacade` (`:55-58`) |
| **`max_alive_subagents`** (config audit F-21) | **Confirmed.** Same field, same absence of a reader, plus a persisted out-of-range value. | `subagent_spawn.py:45,72` reads a differently-named field off a different entity; `AgentDetailView.vue:428`; `SInput.vue:81-85` |
| **Frontend role pickers offer every agent** | **Confirmed.** The capability data reaches the browser and is thrown away. | `projectAgents.ts:21` drops all but `{id, name}`; `InstructConfigForm.vue:25-28`, `ApprovalGateConfigForm.vue:32`, `SubagentSpawnConfigForm.vue:24-26` |
| **`SUBAGENT_INHERITANCE` table** (`models.py:356-370`) | **Confirmed as the same class**, but **owned elsewhere** — it is FU-1 of `2026-07-22-subagent-spawn-fail-fast`, deferred to the sub-agent feature dossier. Not fixed here; recorded so the two do not collide. | Read by nothing at runtime, per the codebase's own comment at `test_orchestration_services.py:741-743` |
| **`a2a_enabled`** — the adjacent agent-level flag | **Cleared.** It **is** read, on both sides. This is the proof the defect is not systemic across agent flags — it is specific to `workflow_capabilities`. | `a2a_scope.py:93-96`; `a2a_service.py:311` |
| **`subagent_parent_ids`, always `frozenset()`** | **Cleared — deliberate and documented.** | `workflows.py:139-142` states the reasoning; depth is enforced at spawn time (`subagent_service.py:113-129`) |
| **`WorkflowFacade.validate_definition`** (`workflow/interfaces/facade.py:46-57`) | **Cleared as inert, flagged as a trap.** It has **zero callers** anywhere in `backend/`, and already fails to forward the existing `subagent_parent_ids`. The API path constructs `WorkflowService` directly (`workflows.py:337,382,419`). | Same shape as the a2a dossier's FU-1; recorded as FU-4 |
| **`wakeup_config`** (a2a F-14, F-21) | **Cleared — different defect class.** It **is** read, just wrongly (`refresh_every_hours` ignored, bounds dropped). Owned by `2026-07-22-wakeup-trigger-state-and-bounds`. | a2a findings F-12, F-14 |
| **`agent_invocation` node** — the fourth agent-naming node type | **Cleared — correctly out of scope.** It names an `agent_id` with no capability declared for it in `[R15.xx]` or `docs/UI/06-agents.md:422-431`. Inventing one would be scope creep. | `AgentInvocationConfigForm.vue:32-34`; no corresponding flag exists |

## 7. Fix Design

### 7.1 A — gate `can_approve` in `ApprovalService.create_gate` (live, highest value)

Add `self._agents = AgentsFacade(db)` to `ApprovalService.__init__` (`approval_service.py:55-58`),
mirroring `instruct_service.py:49`. At the top of `create_gate` (before the insert at `:72`, and
therefore before `_notify_and_arm` at `:124` spends anything), resolve every id in
`config.approvers` — which already includes the leader, folded in by the executor at
`approval_gate.py:51-52` — and reject the gate if any agent is missing, soft-deleted, or lacks
`can_approve`. Reject the whole gate, never a subset (Q-6). Raise a new
`ApprovalCapabilityDenied(OrchestrationError)` alongside the existing family in
`contexts/orchestration/domain/errors.py:60-84`, and emit an `approval.forbidden` audit row before
raising, matching the `a2a.forbidden`-before-raise convention. The executor's existing
`except Exception` (`approval_gate.py:112-118`) routes it to `failure`.

Fail closed: a missing or soft-deleted approver denies. `get_agent` defaults to
`include_deleted=False` (`agents/interfaces/facade.py:81-82`), so a deleted approver returns
`None` and is denied without a special case.

### 7.2 B — gate `can_instruct` in `InstructService.issue`

Insert the check at the **top** of `issue` (`instruct_service.py:64`), before the `[R15.16]`
chain checks — authorization precedes budget, and more concretely the rule-1 path INSERTs a
`rejected_loop` row (`:74-83`) that a forbidden issuer must never reach. Resolve
`issuer_agent_id` via the already-held `self._agents` (`:49`), deny on missing/deleted/not
permitted, emit `instruct.forbidden`, raise `InstructCapabilityDenied`. `instruct.py:94-100`
routes it to `failure`.

This lands strictly before the F-9 scope denial (Q-4), so it changes the outcome in three
observable ways today: a different error string, a different audit action, and **no `instructions`
row written at all** — where the current F-9 path leaves an orphan `issued` row.

### 7.3 C — no runtime gate for `can_create_subagent`

Deliberately omitted (Q-2). `subagent-spawn-fail-fast` removes the only caller. Recorded as an
acceptance criterion of the sub-agent feature dossier (§13 FU-1) so it is not lost.

### 7.4 D — one advisory linter rule for all three, plus the frontend picker

**Backend.** Add three optional frozenset parameters to `validate_definition`
(`linter.py:793-799`) alongside the existing `subagent_parent_ids`, thread them through
`WorkflowService.create` / `patch` / `validate` (`workflow_service.py:130-132,173-175,281`), and
populate them in `_linter_valid_ids` (`workflows.py:128-151`) — which **already loads the full
`Agent` objects** at `:146` and discards everything but the id at `:149`, so this costs no
additional query. Add an advisory rule to `advisory_warnings` (the existing warning-only
aggregation point) that walks nodes and warns when `issuer_agent_id` lacks `can_instruct`,
`leader_agent_id` or any `approvers` entry lacks `can_approve`, or `parent_agent_id` lacks
`can_create_subagent`. Warning level only (Q-5); `linter.py:824-829` makes it non-blocking on
both save paths.

**Frontend.** Widen `ProjectAgent` in `projectAgents.ts:9-22` to carry the three booleans (the
API already returns them, `agents.py:148`) and have each config form's `agentOptions` mark
ineligible agents in the picker — disabled or suffixed, with the reason via `$t()` in both
`en.json` and `zh-TW.json`. Do **not** filter them out: an agent already referenced by a saved
definition must remain visible and selectable, or the author cannot see what is wrong.

### 7.5 E — the deployment-compatibility migration (Q-8, open)

Enforcement without repair breaks every working approval gate on deploy, because every agent
carries `{}` (`seed.py:274`). Options, for the user:

- **(i) Derived, narrow backfill — recommended.** A data migration that scans live
  `workflows.definition` JSONB, extracts the same role→agent references `_collect_agent_ids`
  extracts (`linter.py:111-126`), and sets `can_instruct` / `can_approve` to `true` on exactly
  those agents in exactly those roles. Preserves current effective behaviour with no widening
  beyond what an author already wired; deliberately does **not** grant `can_create_subagent`,
  which has no runtime meaning. Insert-only on the JSONB key — it never clears an
  explicitly-set `false`, so an operator who already made a decision keeps it.
- **(ii) Blanket grant.** Rejected: widest possible grant, and it destroys the distinction the
  fix exists to create.
- **(iii) No backfill.** Fails closed and is the most honest, but silently breaks running
  approval gates at deploy. Safe, disruptive; needs release-note treatment either way.

### 7.6 F — `max_alive_subagents`: validate, repair, and stop lying about the default

- **Validate at the boundary.** `agents.py:90,124` types `workflow_capabilities` as bare
  `BoundedConfig`. Add key-level validation for these four keys: three booleans, plus
  `max_alive_subagents` as `int` in `1..20`, **required when `can_create_subagent` is true**
  (`docs/UI/06-agents.md:429`), and rejected/ignored when it is false. This closes the `0` at the
  API rather than in the component, so any client is covered.
- **Repair the persisted values.** In the same migration as 7.5: any `max_alive_subagents`
  outside `1..20` is set to **3** — `[R15.20]` and `SUBAGENT_MAX_CONCURRENT_DEFAULT`
  (`models.py:352`), not the frontend's 5.
- **Fix the frontend default and the coercion.** `AgentDetailView.vue:347,394` change `5` → `3`.
  Guard the clear: the shared control is not fixed here (that is config audit F-22's own
  dossier), so use the guard this same file already applies to `temperature`/`top_p`/`seed` at
  `:328-341` (`Number.isFinite` on the raw string), which is the documented local mitigation. Do
  not reach for `safeNumber` (`InstructConfigForm.vue:107`) — it lives in the workflow slice and
  is not importable across the slice boundary.

### 7.7 Why this corrects rather than masks

The masking fixes available are: filter the frontend picker (client-side authorization — any
API caller bypasses it); or reject at the linter only (a definition can reach the service by
other routes, and revocation after save is unenforced). Both leave the flag decorative.
Reading the capability inside the service, on the transaction that performs the act, is the only
placement where the answer cannot be supplied by the caller — the same constraint the a2a dossier
fixes in its Q-3.

The one place this dossier deliberately does **not** apply that principle is
`can_create_subagent`, and the reason is stated rather than hidden: there is no act to perform.

### 7.8 Data-repair position, stated plainly

Two distinct repairs, one migration:
1. **`max_alive_subagents` outside `1..20` → `3`.** Unambiguous — the value is out of spec on
   every source that defines it, and the API validator added in 7.6 would otherwise 422 the next
   unrelated PATCH of that agent (Q-9).
2. **Capability backfill — Q-8, the user's decision.** Not applied without an answer. This
   dossier does not repair authorization state on the implementer's own authority.

## 8. Regression Test Plan

**The failing test comes first.** Existing coverage on these paths asserts the wrong things:
`TestApprovalCreateGate.test_create_gate`
(`backend/tests/unit/test_orchestration_services.py:297-334`) patches out `_notify_and_arm`
entirely and asserts only the audit action and the room payload;
`TestInstructIssue.test_issue_success` (`:432-448`) asserts `a2a.send.assert_awaited_once()` —
that a send happened, never that the issuer was entitled to it. Neither can detect a missing
authorization check.

**T-1 (leading test) — `test_create_gate_denies_approver_without_can_approve`**, in
`backend/tests/unit/test_orchestration_services.py::TestApprovalCreateGate`. Build the service
with an agents facade returning an agent whose `workflow_capabilities` is `{}`; call `create_gate`
with that agent among `approvers`; assert `ApprovalCapabilityDenied`, that `approvals.insert` is
**never awaited**, and that `_notify_and_arm` is **never awaited** (the no-spend assertion — this
is the one that pins the actual harm). **Fails today**: `create_gate` performs no agent lookup at
all (`approval_service.py:64-79`), so the gate is created and the approver turns are enqueued.
Requires extending `_make_approval_service` (`:123-134`) with an `agents_facade` parameter, which
`_make_instruct_service` (`:137-151`) already has.

Then:

- **T-2 — `test_create_gate_denies_when_only_the_leader_lacks_capability`.** The leader is folded
  into `approvers` by the executor (`approval_gate.py:51-52`), so this must be covered explicitly
  or a leader-only denial silently passes. **Fails today**: same absent lookup.
- **T-3 — `test_create_gate_rejects_whole_gate_not_a_subset`.** Three approvers, one ineligible:
  assert the raise, and assert no gate is inserted with a two-element approver list. Pins Q-6 —
  this is the test that fails if someone later "helpfully" filters the list and changes the
  majority denominator. **Fails today**: no rejection path exists.
- **T-4 — `test_create_gate_denies_missing_or_deleted_approver`.** `get_agent` returns `None`;
  assert denial (fail-closed). **Fails today**.
- **T-5 — `test_create_gate_allows_when_every_approver_is_capable`.** The positive case; assert
  `insert` and `_notify_and_arm` both run. **Does not fail today** — it passes before and after,
  by construction. Stated rather than dressed up: its role is to stop the fix from denying
  everything, which is the failure mode a fail-closed gate invites.
- **T-6 (leading test, instruct) — `test_issue_denies_issuer_without_can_instruct`**, in
  `::TestInstructIssue`. Assert `InstructCapabilityDenied`, and that `instructions.insert` and
  `a2a.send` are **never awaited**. **Fails today**: `issue` never reads the issuer's capabilities
  (`instruct_service.py:51-124`).
- **T-7 — `test_capability_check_precedes_loop_detection`.** A forbidden issuer with a cycling
  path (`target_agent_id` already in `path`): assert `InstructCapabilityDenied`, not
  `InstructLoopDetected`, and that no `rejected_loop` row is INSERTed. **Fails today**: the
  cycle branch (`:73-83`) writes a row and raises first.
- **Existing-test churn, expected and not a masked failure.**
  `TestInstructIssue.test_loop_detected` (`:451-462`), `test_depth_cap` (`:464-474`),
  `test_per_wakeup_count_cap` (`:476-489`) and `test_wall_clock_budget` (`:491-507`) construct
  the service without an agents facade, so `svc._agents` is a real `AgentsFacade` over an
  `AsyncMock` db. Each must be given a capable issuer explicitly. Do **not** leave them relying
  on an `AsyncMock` attribute chain happening to be truthy — that is a test that passes for the
  wrong reason.
- **T-8 — executor port mapping**, in `backend/tests/unit/test_workflow_executors.py`. Extend
  `TestInstructExecutor` (`:420`) with a case where `issue_instruct` raises the new error; assert
  `outcome.state is StepState.FAILED`, `outcome.port == "failure"`, and that the error string
  names the capability. **Fails today**: the error type does not exist. There is no approval-gate
  executor class in that file, so add one for the equivalent assertion against
  `approval_gate.py:112-118`.
- **T-9 — linter advisory**, in `backend/tests/unit/test_workflow_reference_scoping.py`, which
  hosts the `validate_definition` tests (there is no `test_linter.py`; its current contents are
  six functions covering rules 6-8 only, `:36-62`). Assert that a definition naming an incapable
  issuer/approver/parent produces the issue in `result.warnings` with `result.valid is True` and
  `result.errors` empty — the assertion that pins Q-5. **Fails today**: the rule and the
  parameters do not exist.
- **T-10 — API boundary**, in `backend/tests/unit/test_agents_api_models.py`. Assert
  `max_alive_subagents: 0` is rejected, `1..20` accepted, `21` rejected, and that it is required
  when `can_create_subagent` is true. **Fails today**: `BoundedConfig` (`agents.py:90`) accepts
  any of them.
- **T-11 — frontend**, in `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts`. Assert
  the default is 3, not 5, and that clearing the field does not emit `0`. **Fails today**:
  `AgentDetailView.vue:347,394` default to 5 and `SInput.vue:81-85` coerces `''` to `0`.

**Cases that cannot fail today for the right reason — named, not invented:**

- **No `can_create_subagent` runtime test.** After `subagent-spawn-fail-fast`, `spawn` has no
  production caller; a test asserting a gate there would exercise dead code. The only honest
  coverage is T-9's advisory warning.
- **No end-to-end "permitted instruct succeeds" test.** Every ordinary agent pair is denied
  downstream by the a2a dossier's F-9 (Q-4). The positive assertion is that dossier's W-1. What
  is asserted here is one hop earlier and is fully deterministic: a permitted issuer reaches
  `a2a.send`, a denied one does not.
- **No wiring-tier test in this dossier.** The surfaces it would exercise (real scope check, real
  run) are precisely the ones the a2a dossier is rebuilding; adding a wiring test here would
  either duplicate or immediately conflict with W-1..W-6. Recorded as FU-3.

**Anti-requirement:** no test added here may satisfy the capability check by patching
`create_gate`, `issue`, or `_notify_and_arm` wholesale. Mocking the seam under test is the
documented reason the a2a defects survived ~4700 unit tests (a2a dossier §5), and the same
pattern is present here at `test_orchestration_services.py:298-301`.

## 9. Risks and Rollback

| Risk | Severity | Mitigation |
|---|---|---|
| **R1 — every working approval gate breaks on deploy.** Every agent holds `{}` (`seed.py:274`); enforcing `can_approve` denies all of them. This is the dominant risk and the reason Q-8 exists. | **high** | Resolve Q-8 before implementation. Under (i) behaviour is preserved exactly; under (iii) it is a deliberate, release-noted break. Do not implement A without an answer. |
| **R2 — over-broad backfill.** Option (i) writes `true` onto agents automatically. | medium | Derive strictly from role-specific references in live definitions, never blanket; never overwrite an explicitly-stored `false`; log the affected agent ids so an operator can audit and narrow. |
| **R3 — fail-closed gate denies everything.** A wrong truthiness test (e.g. treating a missing key as an error rather than a denial, or vice versa) is silent. | medium | T-5 is the positive control. Keep the predicate a single shared helper so instruct and approval cannot drift. |
| **R4 — capability revocation locks authors out of unrelated edits**, if the linter rule were blocking. | medium | Advisory only (Q-5). Runtime is the enforcement point. |
| **R5 — the new errors have no RFC 7807 problem type.** `G-orchestration.md:254` enumerates seven; neither new error is among them. | low | Both surface only through the executors' `failure` ports today, so no route mapping is required. Recorded as FU-2 for when an agent-facing instruct tool exists. |
| **R6 — collision with `subagent-spawn-fail-fast`.** Both touch the sub-agent surface. | low | Zero file overlap: that dossier edits `executors/subagent_spawn.py`; this one does not (Q-2). Land in either order. |
| **R7 — collision with `a2a-scope-context-wiring`.** Both edit `instruct_service.py`. | low | Different regions: this dossier inserts at the top of `issue` (`:64`); that one changes `:128-136` ordering and `:144,156`. Textual adjacency only; whichever lands second rebases trivially. |
| **R8 — the `max_alive_subagents` validator 422s an existing agent's next unrelated PATCH** if the repair does not ship with it. | low | Ship 7.6's validation and repair in the same change (Q-9). |

**Rollback.** A, B and D are application-layer and revert independently — behaviour returns to
"capabilities inert", which is today's state. The migration in E/F is the only durable write: the
`max_alive_subagents` clamp is not reversible to the original `0` (and should not be — `0` was
never valid), and the capability backfill under (i) sets JSONB keys that old code ignores, so a
code rollback is safe while the data change persists. Prefer landing E last, after A and B have
run in a real environment.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 (§8) fails against current code and passes after the fix.
- [ ] AC-2: an approval gate naming any approver — leader included — without `can_approve` is
      rejected before the gate row is inserted and before any `drive_approver_turn` job is
      enqueued, so no provider key is spent.
- [ ] AC-3: a partially-ineligible approver list rejects the whole gate; no gate is ever created
      with a silently reduced approver set (Q-6).
- [ ] AC-4: an instruct whose issuer lacks `can_instruct` is denied inside `InstructService.issue`
      before any `instructions` row is written, before loop detection, and before `a2a.send`.
- [ ] AC-5: both denials emit an audit row (`approval.forbidden` / `instruct.forbidden`) and
      surface on the node's `failure` port with an error string naming the capability.
- [ ] AC-6: a missing or soft-deleted agent in any of these roles is denied (fail-closed), pinned
      by T-4.
- [ ] AC-7: a definition naming an incapable agent produces a linter **warning**, `valid` stays
      `True`, and the definition still saves — including the edit that removes the offending node.
- [ ] AC-8: the workflow editor marks ineligible agents in the instruct, approval and
      subagent-spawn pickers without removing them, in both locales, with no hardcoded strings.
- [ ] AC-9: `max_alive_subagents` is validated `1..20` at the API and required when
      `can_create_subagent` is true; persisted out-of-range values are repaired to 3; the frontend
      default is 3, not 5.
- [ ] AC-10: **no runtime `can_create_subagent` gate is added**, and the reason is recorded in
      code where a future implementer will find it — at the sub-agent spawn seam, pointing at the
      feature dossier.
- [ ] AC-11: Q-8 is answered by the user and the chosen migration option is implemented and
      recorded in §12. Implementation must not proceed on A without it.
- [ ] AC-12: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` pass in `frontend/`.

## 11. SRS Delta

Not "none". The analysis surfaced two genuine gaps, both small.

1. **`can_approve` has no requirement.** `[R15.10]`–`[R15.14]` (`REQUIREMENTS.md:767-775`)
   describe the approval gate without mentioning the capability, yet `[R15.22]`
   (`:810`) forces it false for sub-agents and the UI documents it as a toggle
   (`docs/UI/06-agents.md:427`). Proposed addition to §15.4, mirroring `[R15.18]`'s phrasing:

   > **[R15.10a]** Only an agent with `workflow_capabilities.can_approve = true` may be named as
   > an approver or leader of an Approval Gate. A gate naming any agent without the capability is
   > rejected in full; approvers are never silently dropped, since that would change the tally
   > denominator defined by `[R15.12]` and `[R15.13]`.

2. **The `[R17.01]` audit action list omits the denial actions.** `REQUIREMENTS.md:845` enumerates
   the Workflow category and does not include the new `instruct.forbidden` /
   `approval.forbidden`. Note the list is **already** non-exhaustive in practice —
   `subagent.depth_exceeded` (`subagent_service.py:115-126`) and `a2a.forbidden` are emitted today
   and absent from it — so this is a consistency amendment, not a blocker. Proposed: extend the
   Workflow row with `instruct.forbidden`, `approval.forbidden`, and (correcting the existing
   drift) `subagent.depth_exceeded`.

Neither is applied before approval. `[R15.20]`'s naming (`max_subagents_alive_simultaneously`
vs. the UI's `max_alive_subagents` vs. the node config's `max_alive_simultaneously`) is left
alone here: renaming a field across three documents and a JSONB key is its own change, recorded
as FU-5.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 — the `can_create_subagent` runtime gate belongs to the sub-agent feature dossier.**
  When `SubagentService.spawn` regains a production caller, the parent's capability must be
  checked there, `[R15.22]`'s forced-false inheritance must actually prevent a sub-agent from
  spawning (today it is enforced only structurally by `parent_instance.parent_id`,
  `subagent_service.py:113-129`), and `max_alive_subagents` must be read from the **agent** rather
  than the node config — `[R15.20]` says "configurable per parent agent" and
  `subagent_spawn.py:45,72` sources it from the node. This overlaps
  `2026-07-22-subagent-spawn-fail-fast` FU-5 and FU-6; the two lists should be merged by whoever
  writes the feature dossier.
- **FU-2 — RFC 7807 problem types for capability denials.** `G-orchestration.md:254` enumerates
  seven; the two new errors have none. Not needed while they surface only through workflow
  executor ports, required the moment an agent-facing instruct or approval tool exists.
- **FU-3 — no wiring-tier coverage was added here**, deliberately, to avoid conflicting with the
  a2a dossier's W-1..W-6. Once that lands, add a wiring case: a capable issuer, a shared context,
  and an instruct that actually succeeds — the assertion neither dossier can make alone.
- **FU-4 — `WorkflowFacade.validate_definition` (`workflow/interfaces/facade.py:46-57`) has zero
  callers** and already drops `subagent_parent_ids`. Widening it here would add an untested
  parameter to an unused surface. Wire it or narrow it; the same trap as the a2a dossier's FU-1.
- **FU-5 — one concept, three names.** `max_subagents_alive_simultaneously` (`REQUIREMENTS.md:793`),
  `max_alive_subagents` (`docs/UI/06-agents.md:429`, the agent JSONB key),
  `max_alive_simultaneously` (`subagent_spawn.py:45`, the node config key). The divergence is why
  a repo-wide grep did not connect the UI control to its supposed consumer. Converge on one name
  when the feature dossier wires it.
- **FU-6 — the shared `SInput` number coercion** (`SInput.vue:81-85`) is patched around locally
  for the fourth time by 7.6. Config audit F-22 owns the shared-control fix; until it lands, every
  new `type="number"` with a non-zero lower bound inherits the trap.
- **FU-7 — `BoundedConfig` as a validation strategy.** `agents.py:90,124` bound size and depth but
  nothing about shape, which is what let both `{}` and `{"max_alive_subagents": 0}` through. Every
  other `BoundedConfig` field on the agent surface carries the same exposure and is worth a sweep
  independent of these bugs.
</content>
