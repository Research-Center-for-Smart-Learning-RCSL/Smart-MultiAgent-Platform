# Phase G — Multi-Agent Orchestration (A2A, Wake-up, Approval, Instruct, Sub-Agents)

**Goal.** Implement the SRS §9.4 + §15 orchestration layer exactly: per-agent A2A inboxes on Redis Streams with the custom envelope, wake-up triggers (`every_n_messages`, `silence_minutes`, `call_only`) with autostop / self-modification / periodic refresh, **agent-only** approval gates with `single / majority / consensus` modes and leader fallback, instruct with chain-based loop detection and depth / count / wall-clock caps, and depth-1 sub-agents with strict inheritance rules.

**Size.** L
**Depends on.** C, D, E, F.
**Unblocks.** H (workflow nodes reuse these primitives).
**Refs.** `REQUIREMENTS.md` §9.4, §15 (all), §18 (approval notification is reserved), §21.1 (approvals, approval_votes, instructions, agent_instances).

## G.0 Scope summary

By phase close:

- Each agent has a dedicated Redis Stream inbox `a2a:agent:{agent_id}`.
- Messages carry the exact R9.13 envelope (`id, from_agent, to_agent, workflow_run_id, type, payload, correlation_id, created_at`).
- Synchronous `call` blocks the caller's turn for up to 60 s by default.
- Wake-up evaluator honours `every_n_messages` (counts user+agent, §15.01), `silence_minutes` (pauses without live users, §15.02 / §15.05b), `autostop_rounds` (§15.03 / §15.04), `allow_self_open=false` (§15.05), `call_only` (§15.05a).
- Agents self-modify only `n` and `t_minutes` via the built-in `update_wakeup` tool; out-of-range clamped + audited.
- Designer sets `refresh_every_hours` to snap wake-up back to authored values periodically.
- Approval gates are strictly between **agents**; `leader_agent_id` mandatory.
- Instruct chains enforce `max_chain_depth=5`, `max_instructions_per_wakeup=5`, `max_chain_seconds=120`, with cycle detection.
- Sub-agents depth = 1; concurrent cap default 3 (hard cap 20); inheritance matches R15.22 exactly.

## G.1 A2A transport — **CODE** — L

**Deliverables.**

- Per-agent Redis Stream inbox: **`a2a:agent:{agent_id}`** (§21.2, R9.14). One consumer per live agent runtime.
- Envelope (R9.13):
  ```json
  {
    "id": "uuid",
    "from_agent": "uuid",
    "to_agent": "uuid | 'broadcast:workspace'",
    "workflow_run_id": "uuid | null",
    "type": "call | reply | notify | instruct",
    "payload": { "text": "...", "tool_calls": [...] },
    "correlation_id": "uuid",
    "created_at": "RFC3339"
  }
  ```
- `smap/contexts/agents/a2a.py::send(envelope)` XADDs to the target inbox (`broadcast:workspace` fan-outs to every agent in the target workflow's workspace).
- Consumer: Arq worker per live agent XREADs with 1 s block, ACKs on success, retries (max 3 with exp backoff), pushes to DLQ `a2a:agent:{agent_id}:dlq` on final failure. DLQ visible to Admin (Phase I).
- Timeout for sync `call`: caller awaits a matching `reply` with `correlation_id` up to **60 s default (configurable per call)** (R9.15).
- `notify` is fire-and-forget (R9.16); `instruct` requires delivery ack (see G.5).
- Broadcast: `to_agent = "broadcast:workspace"` fan-outs to all agents in the workspace's project that pass the scope check.

**Key IDs.** `[R9.12]`–`[R9.16]`.

**Exit criteria.** Chaos test redelivers, DLQs at third failure; 60s sync timeout exercised; broadcast fan-out verified.

## G.2 A2A scope enforcement — **CODE** — S

**Deliverables.**

- Reuse E.4 scope checker at `send()`: reject cross-project; reject when either side has `a2a_enabled=false`; require shared context OR callee `call_only` (R9.17).
- Denial returns structured error and writes audit `a2a.forbidden`.

**Key IDs.** `[R9.17]`.

**Exit criteria.** Scope matrix test green.

## G.3 Wake-up configuration & triggers — **CODE** — M

**Deliverables.**

- `agents.wakeup_config jsonb` shape (§15.1):
  ```json
  {
    "triggers": {
      "every_n_messages": { "enabled": true, "n": 3 },
      "silence_minutes":   { "enabled": true, "t_minutes": 2,
                             "autostop_rounds": 5, "autostop_max_default": 100 },
      "call_only":         { "enabled": false }
    },
    "allow_self_open": false,
    "refresh_every_hours": 24
  }
  ```
- **`every_n_messages`** (R15.01): counts **all messages** in the room (user + agent) scoped to the room. Engine subscribes to `message.created` events; on every `n`-th, enqueues an Arq job to wake the agent in that room.
- **`silence_minutes`** (R15.02, R15.05b): starts only when `ws:presence:{room_id}` is non-empty (live user/guest present); pauses when the set becomes empty.
- **`autostop_rounds`** (R15.03 / R15.04): a "round" = agent message with no subsequent user message. After `autostop_rounds` such rounds, silence trigger stops for the room until a user sends a new message. Default 5, hard cap 100.
- **`allow_self_open = false`** (R15.05): agent cannot speak first in an empty room.
- **`call_only`** (R15.05a): when enabled, ignore `every_n_messages` and `silence_minutes`; only respond to A2A `call` / `instruct`.
- If all three trigger sub-objects are disabled, the agent is inert.

**Key IDs.** `[R15.01]`–`[R15.05b]`.

**Exit criteria.** Per-trigger integration test; `autostop_rounds` boundary; silence timer pause/resume on presence churn.

## G.4 Self-modification of wake-up — **CODE** — S

**Deliverables.**

- Built-in tool `update_wakeup({every_n_messages?: int, silence_minutes?: int})` (R15.06).
- **Only** these two fields are mutable at runtime; all other fields in `wakeup_config` are read-only for the agent.
- Server-side hard bounds (R15.07): `n ∈ [1, 1000]`, `t_minutes ∈ [1, 1440]`. Values outside are **clamped** and the clamp is audit-logged as `agent.wakeup_clamped`.
- Designer may also set soft per-agent bounds at creation (R15.08); self-modification must respect them.

**Key IDs.** `[R15.06]`–`[R15.08]`.

**Exit criteria.** Clamp test logs audit with before/after; non-`n`/`t_minutes` keys rejected.

## G.5 Periodic refresh — **CODE** — S

**Deliverables.**

- `refresh_every_hours` (R15.09): an Arq periodic job per agent resets `wakeup_config` back to the Agent Designer's authored values every T hours. Because there is no agent versioning (R9.02), "authored values" = the most recent human edit to `agents.wakeup_config`; the auth column is a companion `wakeup_authored_snapshot jsonb` stored alongside the agent row.
- Audit event: `agent.wakeup_refreshed`.

**Key IDs.** `[R15.09]`, `[R9.02]`.

**Exit criteria.** Scheduled reset test: runtime edits revert at refresh tick.

## G.6 Approval gates (agent-only) — **CODE** — M

**Deliverables.**

- Alembic revision `0012_approvals`:
  ```
  approvals (
    id uuid pk, workflow_run_id fk workflow_runs,
    mode enum('single','majority','consensus'),
    leader_agent_id fk agents,
    timeout_seconds int,                    -- 1..86400
    state enum('pending','approved','rejected','timeout_leader'),
    started_at, ended_at
  );
  approval_votes (
    approval_id fk approvals, voter_agent_id fk agents,
    vote bool, rationale text,
    cast_at,
    PRIMARY KEY (approval_id, voter_agent_id)
  );
  ```
- Node config (R15.10): `{ mode, approvers: [agent_id…], leader_agent_id, timeout_seconds }`.
- Resolution rules:
  - **`single`** (R15.11): the leader's vote decides; other `approvers` are advisory (their votes still recorded).
  - **`majority`** (R15.12): > 50% of listed approvers must approve; ties broken by leader.
  - **`consensus`** (R15.13): all approvers debate and must converge on the same verdict; if not converged by `timeout_seconds`, leader's verdict wins (state → `timeout_leader`).
- Approver agents consume tokens from **their own** Key Group (R15.14); the leader's Key Group covers the final decision announcement.
- WS: publishes `approval.requested` / `approval.resolved` to `/ws/chatroom/{id}` and `/ws/workflow-runs/{id}`.
- Endpoints: reached only through workflow execution; no direct `POST /approvals` in v1 (humans-as-approvers is R18.02 "reserved for future").

**Key IDs.** `[R15.10]`–`[R15.14]`, §18 (future human approvers noted).

**Exit criteria.** Mode matrix test; `consensus` timeout falls to `timeout_leader`; approver/leader audit separate.

## G.7 Instruct + loop detection — **CODE** — L

**Deliverables.**

- Alembic revision `0013_instructions`:
  ```
  instructions (
    id uuid pk, chain_id uuid, path uuid[], depth int,
    issuer_agent_id fk agents, target_agent_id fk agents,
    payload jsonb,
    state enum('issued','delivered','completed','rejected_loop','timeout'),
    issued_at, resolved_at timestamptz null
  );
  CREATE INDEX ON instructions (chain_id);
  CREATE INDEX ON instructions (issuer_agent_id, issued_at DESC);
  ```
- Instruct tool (R15.15): **target cannot refuse**; message enqueued into target's A2A inbox with `type=instruct`.
- Loop / budget checks before dispatch (R15.16):
  1. Reject if `target in path` → `rejected_loop`; audit `instruct.rejected_loop` with full `path` and `chain_id`.
  2. Reject if `len(path) >= max_chain_depth` (platform default **5**, project-configurable up to **20**).
  3. Reject if issuing agent exceeded `max_instructions_per_wakeup` (default **5**) within its current wake-up slice.
  4. Chain has wall-clock `max_chain_seconds` default **120 s**; exceeding aborts the root workflow_run.
- Every instruct row captures `chain_id, path, issuer, target, payload_hash, result, depth_at_issue` (R15.17).
- Static cycle pre-check (H.5 linter rule 10) catches design-time loops in workflows.

**Key IDs.** `[R15.15]`–`[R15.17]`.

**Exit criteria.** Direct A→A rejected; A→B→A rejected; depth 6 rejected by default; budget-exhaust aborts root run.

## G.8 Sub-agents (depth = 1) — **CODE** — M

**Deliverables.**

- Use **existing** `agent_instances` table (§21.1): `(id, agent_id, parent_id uuid null, chatroom_id, run_context jsonb, spawned_at, destroyed_at)`; `parent_id` FK into `agent_instances.id` (self-ref).
- Spawn built-in tool: `spawn_subagent(parent_agent_id, task_description)` (R15.18) — creates an `agent_instances` row with `parent_id` set and hydrates a short-lived runtime.
- **Recursion depth exactly 1** (R15.19): if the caller is itself a sub-agent (`parent_id IS NOT NULL`), reject with `subagent-depth-exceeded` and audit.
- **`max_subagents_alive_simultaneously`** configurable per parent (default **3**, hard cap **20**) (R15.20).
- Lifecycle (R15.21): sub-agent destroyed on task completion or error; ephemeral context purged; `agent_instances` row retained **30 days** for audit then deleted.
- **Inheritance table** (R15.22 — exact):

  | Field | Inherited? |
  |---|---|
  | `key_group_id` | ✓ (usage attributes to parent's key owner) |
  | `system_prompt` + `prompt_strategy` | ✓ (task description appended as user-role message) |
  | `model_hint` | ✓ |
  | `a2a_enabled` | ✗ (forced `false`) |
  | `mcp_servers` | ✓ |
  | `rag_config_id` | ✗ (forced `null`) |
  | `graphrag_config_id` | ✗ (forced `null`) |
  | `context_mode` + `context_token_cap` | ✓ |
  | `wakeup_config` | ✗ (N/A) |
  | `workflow_capabilities.can_create_subagent` | ✗ (forced `false`) |
  | `workflow_capabilities.can_instruct / can_approve` | ✗ (forced `false`) |
- **Usage attribution** (R15.23): every provider call by a sub-agent writes a `key_usage_events` row with `agent_id = agent_instances.id` AND `parent_agent_id = parent_agents.id`. Billing always hits the parent's Key Group owner.

**Key IDs.** `[R15.18]`–`[R15.23]`.

**Exit criteria.** Depth 2 rejected; concurrent cap enforced; inheritance matrix verified; 30-day retention verified.

## G.9 Audit & observability — **CODE** — S

**Deliverables.**

- Audit events: `a2a.sent`, `a2a.dlq`, `a2a.forbidden`, `wakeup.fired`, `agent.wakeup_clamped`, `agent.wakeup_refreshed`, `approval.requested`, `approval.resolved`, `instruct.issued`, `instruct.rejected_loop`, `subagent.spawned`, `subagent.destroyed`.
- Metrics: `a2a_messages_total{type}`, `a2a_dlq_total`, `wakeup_fires_total{kind}`, `approval_resolutions_total{mode,outcome}`, `instruct_chain_depth_histogram`, `subagent_concurrency{parent}`.

**Key IDs.** §17.1 Workflow category.

**Exit criteria.** Counters move under a scripted multi-agent scenario.

## G.10 Frontend orchestration affordances — **CODE** — M

**Objective.** UI surface within `slices/conversation/` (and `slices/workflow/` for approval viewer) mirroring G.1–G.8.

**Deliverables.**

- Wake-up config editor in agent binding panel (within chatroom settings) — users see live `wakeup_config` and can edit designer-level fields.
- A2A DLQ viewer (per chatroom, room owner / admin only).
- Approval inline cards: subscribed via `/ws/chatroom/{id}` approval events; agents listed with vote state; leader marker; timeout countdown.
- Sub-agent tree: nested message thread under the parent invocation.
- Slash commands in composer: `/compact` (forces compaction on active agent).
- Instruct chains: Admin-only backstage in `slices/workflow/WorkflowRunView` (per R14.10 "not surfaced in chat room UI").

**Key IDs.** §24.2, §15, §14.10.

**Exit criteria.** Playwright: user message triggers approval gate; mocked approvers vote; path renders live.

## G.∞ Phase gate

- [ ] Per-agent `a2a:agent:{id}` streams; DLQ after 3 failures; sync `call` 60 s.
- [ ] All three wake-up triggers match R15.01–R15.05a; `autostop_rounds` boundary verified.
- [ ] `update_wakeup` clamps and audits; only `n` / `t_minutes` writable.
- [ ] `refresh_every_hours` snapshot-revert works.
- [ ] Approvals use `approvals / approval_votes` with agents only; `timeout_leader` path verified.
- [ ] Instruct enforces depth=5, per-wakeup 5, chain-seconds 120; `rejected_loop` audit present.
- [ ] Sub-agent depth=1, default 3 concurrent, 20 cap; inheritance matrix verified byte-for-byte.
- [ ] `key_usage_events.parent_agent_id` populated on sub-agent calls.
- [ ] `00-overview.md` §0.8: G = done.

## Cross-cutting checklist

1. **AuthZ tap.** A2A scope = R9.17 runtime check; instruct + subagent require `workflow_capabilities`.
2. **Audit tap.** Full R17.1 Workflow category coverage listed in G.9.
3. **Rate limit bucket.** `a2a-send`, `instruct-issue`, `subagent-spawn`, `approval-vote` (internal, per-agent).
4. **Observability.** Dashboards per G.9 metrics.
5. **RFC 7807.** `https://smap.local/problems/{a2a-forbidden, a2a-timeout, instruct-loop-detected, instruct-budget-exceeded, subagent-depth-exceeded, subagent-concurrency-exceeded, approval-timeout-leader}`.
6. **Migration policy.** `0012_approvals`, `0013_instructions` with N-1 compatibility; `agent_instances.parent_id` self-FK included.
7. **Secrets.** None introduced; builds on existing envelope paths.

## Risks

- **Thundering wake-ups.** `n=1` on a hot room can DoS a provider key; clamp bounds + D.6 exhaustion + `rate_limit_policies` 29 mitigate.
- **Approval deadlock in consensus mode.** `timeout_seconds` forced by schema (1..86400); `timeout_leader` guarantees forward progress.
- **Sub-agent billing confusion.** Dashboard surfaces both `agent_id` + `parent_agent_id`; reporting groups by parent in "by agent" panel.
- **Instruct chain bloat.** `path uuid[]` capped by depth. Bounded growth is enforced by the nightly 365-day retention sweep on `audit_logs` (`backend/alembic/versions/0004_audit.py`); the table is **not** partitioned — earlier wording in this file claimed monthly partitioning but no migration creates it.
