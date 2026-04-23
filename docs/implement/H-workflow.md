# Phase H — Workflow Engine

**Goal.** Implement the DAG+FSM workflow engine exactly as `docs/workflow.schema.json` and `docs/workflow.schema.md` define it: versionless authoring (editing overwrites in place, R14.06 / R9.02), the full 11-node executor set, the SEL v1 interpreter with the precise function whitelist, the 14 semantic-linter rules, the runs engine backed by Arq + `workflow_runs` / `workflow_steps`, and the 90-day `workflow_runs_archive` policy.

**Size.** XL
**Depends on.** C–E, F, G (approval + instruct + subagent reused).
**Unblocks.** I (admin cancel / reset surfaces).
**Refs.** `REQUIREMENTS.md` §14 (all); §21.1 `workflows / workflow_runs / workflow_steps / workflow_runs_archive`; §22.11; `docs/workflow.schema.json`; `docs/workflow.schema.md`.

## H.0 Scope summary

By close:

- Workflow authoring: one jsonb `definition` per workflow, **versionless** — edits overwrite in place using `If-Match: <version>` optimistic lock (R14.06 + R9.02).
- `POST /api/workspaces/{id}/workflows/validate` runs the JSON Schema + all 14 semantic linter rules; never persists.
- Triggers: `manual`, `cron`, `message_received`, `a2a_event`, `wakeup_signal` (per `workflow.schema.json` — **no webhook**).
- SEL v1 interpreter enforces AST depth ≤ 16, expression length ≤ 1000 chars, 5 ms CPU budget, RE2-only regex with 5 ms regex budget.
- Runs FSM: `running | waiting | succeeded | failed | cancelled`.
- Archive policy: move runs older than 90 d to `workflow_runs_archive` with only a jsonb summary retained.
- Trace visible to Admin + Project Owner in backstage panel; not surfaced in chat UI (R14.10).

## H.1 Workflows schema & CRUD — **CODE** — M

**Deliverables.**

- Alembic revision `0014_workflows`:
  ```
  workflows (
    id uuid pk, workspace_id fk workspaces,
    name, definition jsonb,
    version int not null default 1,           -- optimistic lock only, NOT semantic version
    created_at, deleted_at,
    UNIQUE (workspace_id, name) WHERE deleted_at IS NULL
  );
  ```
- **No** `workflow_versions` or `current_version_id`. R14.06 explicitly forbids versioning.
- Endpoints (§22.11):
  - `GET /api/workspaces/{id}/workflows`
  - `POST /api/workspaces/{id}/workflows/validate` — runs JSON Schema + 14 semantic rules; returns `{valid, errors:[], warnings:[]}` without persisting.
  - `POST /api/workspaces/{id}/workflows` — runs the same validation server-side before insert.
  - `PATCH /api/workflows/{id}` — edit in place; requires `If-Match: <version>`.
  - `DELETE /api/workflows/{id}` — soft-delete.
  - `POST /api/workflows/{id}/runs` — trigger.
  - `GET /api/workflows/{id}/runs`
  - `GET /api/workflow-runs/{id}`
  - `POST /api/workflow-runs/{id}/cancel`
- Soft-deleted workflows follow the 60-day recovery window (R8.11).

**Key IDs.** `[R14.01]`–`[R14.06]`, §22.11.

**Exit criteria.** CRUD + If-Match conflict test; malformed definition rejected by schema + linter before write.

## H.2 SEL v1 interpreter — **CODE** — L

**Deliverables.**

- `smap/contexts/workflow/sel/` — hand-rolled lexer + parser + AST + evaluator per `workflow.schema.md` §3.1 grammar. **No `eval`/`exec`, no templating engine that allows attribute traversal.**
- **Allowed functions** (exactly): `len`, `lower`, `upper`, `contains`, `startswith`, `endswith`, `matches`, `int`, `float`, `str`, `json_get`, `coalesce`, `now_unix`, `abs`, `min`, `max`.
- **Forbidden** (§3.2): variable assignment, function definition, attribute-of-function access, Python dunder methods, imports, process I/O, network, loops, lambdas.
- **Limits** (§3.3): AST depth ≤ 16; expression length ≤ 1000 chars (enforced in JSON Schema); wall-clock ≤ 5 ms per evaluation (per-thread CPU timer); regex via `google-re2` with 5 ms timeout and no catastrophic backtracking.
- **Variable scopes** (§4.1): workflow variables (no prefix), `trigger.*`, `ctx.*`; only workflow variables are writable. Reads of undeclared variables yield `null`; type-mismatched comparisons return `false` without raising (§4.2).
- **Template interpolation** (§3.4): `template_string` fields use simple `{{ var.path }}` interpolation with the same `var_ref` grammar; **no control flow in templates**; unresolved vars render as the literal `{{ var.path }}`.

**Key IDs.** `docs/workflow.schema.md` §3, §4.

**Exit criteria.** Budget-overflow + forbidden-construct + catastrophic-regex inputs all trip cleanly without harming the worker.

## H.3 Node executors (11) — **CODE** — XL

**Deliverables.** One executor per node type per `workflow.schema.md` §2:

| Node | Ports | Key behaviour |
|---|---|---|
| `trigger` | `default` | Exactly one per workflow (H.5 rule 1). Holds `trigger_type ∈ {manual, cron, message_received, a2a_event, wakeup_signal}` + type-specific config (cron expression + timezone, etc.). |
| `agent_invocation` | `success`, `failure` | Run one Agent turn with `input_template` (template interpolation). Capture reply into `output_variable`. Uses Agent's Key Group + prompt strategy + context mode. Logs `origin='workflow'` (R14.09). |
| `approval_gate` | `approved`, `rejected`, `timeout` | Config: `mode`, `approvers[]`, `leader_agent_id`, `timeout_seconds`, `question_template`. Reuses G.6 `approvals/approval_votes` tables. |
| `condition` | user-declared ports + `default_port` | Up to 20 branches, first matching SEL expression wins. Ports must be unique (H.5 rule 11). |
| `instruct` | `success`, `failure` | Reuses G.7 instruct; carries `chain_id` + `path`; runtime loop detection. |
| `subagent_spawn` | `success`, `failure` | Reuses G.8; `parent_agent_id` must not itself be a sub-agent (H.5 rule 9). |
| `wait_for_event` | `default`, `timeout` | Park branch until a matching event (room message, A2A message, timer, variable condition). Releases worker slot; resume via dispatcher XADD. |
| `parallel` | `default` | Fan-out; all outgoing edges taken concurrently. |
| `join` | `default`, `timeout` | Fan-in with `strategy ∈ {all, any, count:N}`. |
| `set_variable` | `default` | Evaluate SEL assignments, write to workflow variables. |
| `end` | — | Terminal. `status: success|failure`, optional `return_variables`. |

- Executor signature: `async def execute(ctx: RunContext, node: NodeSpec) -> StepOutcome`.
- On-error strategies (`workflow.schema.md` §6): `fail` (cancel siblings + mark run failed), `continue` (treat as success, follow default port), `retry` (retry up to `retry_max` with linear `retry_backoff_ms`), `fallback` (follow edge to `fallback_node_id`).
- Each execution writes a `workflow_steps` row.
- `approval_gate` publishes `approval.requested` on `/ws/workflow-runs/{id}` (`workflow.schema.md` §6).

**Key IDs.** §14.1 / §14.3; `workflow.schema.json`; `workflow.schema.md` §2, §6.

**Exit criteria.** One integration test per node type; parallel/join chaos test; on-error matrix covered.

## H.4 Runs engine & FSM — **CODE** — L

**Deliverables.**

- Alembic revision `0015_workflow_runs`:
  ```
  workflow_runs (
    id uuid pk, workflow_id fk workflows,
    trigger_type text, started_by_user_id fk users null,
    state enum('running','waiting','succeeded','failed','cancelled'),
    started_at, ended_at timestamptz null
  );
  workflow_steps (
    id uuid pk, run_id fk workflow_runs, node_id text,
    state enum('pending','running','succeeded','failed','skipped','cancelled'),
    started_at, ended_at timestamptz null,
    input jsonb, output jsonb, error text null
  );
  CREATE INDEX ON workflow_runs (workflow_id, started_at DESC);
  CREATE INDEX ON workflow_runs (state, started_at);
  CREATE INDEX ON workflow_steps (run_id, started_at);
  ```
- Arq task `run_workflow_step(run_id)` resumes the run: load state, execute next ready node, persist `workflow_steps` row, reschedule or park as `waiting` (for `wait_for_event` / `approval_gate` / parked `parallel` branches).
- Parking primitives: Redis key `wf:wait:{run_id}:{event_name}` + DB `state='waiting'`; a dispatcher XADDs on matching events.
- Triggers:
  - **`manual`** — explicit `POST /api/workflows/{id}/runs`.
  - **`cron`** — single scheduler worker computes next fire times (timezone-aware) and enqueues runs.
  - **`message_received`** — subscribed to message-created events in bound chatrooms.
  - **`a2a_event`** — subscribed to A2A stream patterns.
  - **`wakeup_signal`** — subscribed to `wakeup.fired` events.
- Run-level bounds honoured: `run_max_seconds`, `idle_max_seconds`, `loop_guard.max_visits_per_node` (`workflow.schema.md` §1).
- Cancellation: `POST /api/workflow-runs/{id}/cancel` sets `state='cancelled'` and cancels all live branches (emits `workflow.step_finished` with `state='cancelled'`).
- Run-level audit: `workflow.run_started / _finished / _cancelled`; step-level: `workflow.step_started / _finished / _failed`.

**Key IDs.** `[R14.07]`–`[R14.09]`, §21.1 workflow tables.

**Exit criteria.** Kill worker mid-node → on restart, resumes from last acked step; `waiting` releases worker slot.

## H.5 Semantic linter (14 rules) — **CODE** — M

**Deliverables.** Exactly the 14 blocking rules from `workflow.schema.md` §5.1, each a pure function `check(definition) -> list[LintIssue]`:

1. **Exactly one `trigger`** and `entry_node_id` equals it.
2. **Unique** node ids and unique edge ids.
3. **Edges valid**: `from`/`to` exist; `from_port` legal for source type; `(from, from_port, to)` triples unique.
4. **Reachability**: every non-trigger node reachable from the trigger.
5. **Termination**: every reachable node has ≥ 1 path to an `end`. Exception: `wait_for_event` with no outgoing edges — warn only.
6. **Referenced agents exist** (all of `agent_id, target_agent_id, issuer_agent_id, leader_agent_id, parent_agent_id, approvers[]`) and live in the workflow's Project.
7. **Agent-scope rule**: all referenced agents live in the **same Project** as the workflow.
8. **Chatroom scope**: any referenced `chatroom_id` belongs to a chatroom in the workflow's Workspace's Project.
9. **Sub-agent depth**: `subagent_spawn.parent_agent_id` must not itself be a sub-agent.
10. **Instruct cycle pre-check**: build a static agent-instruction graph from `instruct` edges; reject if it contains a cycle.
11. **Condition branch uniqueness**: `branches[].port` values unique within a `condition` node and different from `default_port`.
12. **Variable references**: every `{{ var }}` in templates / guards / expressions resolves to a declared `variables.<name>` / `trigger.*` / `ctx.*` known key. Unknown reads in *required* fields (e.g. `condition.when`) are errors; optional template misses are warnings.
13. **Port coverage**: for deterministic multi-port nodes (`approval_gate`, `agent_invocation`, `instruct`, `subagent_spawn`), every port is either connected by at least one edge OR explicitly covered by `on_error.strategy='continue'`.
14. **Parallel/join pairing**: `parallel` nodes ≥ 2 outgoing edges; `join` nodes ≥ 2 incoming edges.

- Advisory warnings (`workflow.schema.md` §5.2): unreachable variables; `agent_invocation.timeout_seconds > 300`; `wait_for_event` with no timeout path; `approval_gate.timeout_seconds > 3600`; cron < 1 min; `loop_guard.max_visits_per_node > 1000`.
- `validate` endpoint **aggregates** all errors+warnings (does not stop at first).

**Key IDs.** `workflow.schema.md` §5.

**Exit criteria.** One fixture per rule (positive + negative); `validate` aggregates correctly.

## H.6 Run archive — **CODE** — S

**Deliverables.**

- Alembic revision `0016_workflow_runs_archive`:
  ```
  workflow_runs_archive (
    id uuid pk, workflow_id fk workflows,
    trigger_type text, started_by_user_id fk users null,
    state text,
    started_at, ended_at,
    summary jsonb,                    -- {node_count, failures, total_tokens, ...}
    archived_at
  );
  ```
- Nightly worker moves runs with `ended_at < now() - 90 days`:
  - Summarise into `summary jsonb`.
  - Delete matching `workflow_steps` rows.
  - Insert the archive row in the same transaction.
- Admin can adjust cutoff via `rate_limit_policies`-style runtime config (§21.1 comment).
- Listing endpoints accept `include_archive=true` to union both tables.

**Key IDs.** §21.1 `workflow_runs_archive` comment, `[R20.xx]`.

**Exit criteria.** Fast-forward test archives + idempotent on re-run.

## H.7 Frontend workflow slice — **CODE** — L

**Objective.** `slices/workflow/` fully built per §24.2 + §14.2.

**Deliverables.**

- Views: `WorkflowListView`, `WorkflowEditorView` (Vue Flow canvas — `@vue-flow/core`; lazy-loaded in its own chunk per R24.45), `WorkflowRunView`, `WorkflowBackstageView` (Admin + Project Owner only, per R14.10 — trace, sub-agent tree, instruction chains, approval histories; **not** surfaced in chat).
- Editor supports drag-drop, copy/paste, undo/redo, linting (debounced 500 ms call to `/validate`), and a dry-run simulator that steps through with synthetic inputs (R14.06).
- Run inspector: subscribes to `/ws/workflow-runs/{id}` for live step transitions & approval prompts (§22.14).
- Versioning affordances **absent** by design (R14.06).

**Key IDs.** `[R14.04]`–`[R14.06]`, `[R14.10]`, §24.2, §22.14.

**Exit criteria.** Playwright: author → validate → run → watch steps live → backstage view visible to PO but hidden in chat.

## H.∞ Phase gate

- [ ] `workflows` has no version table; only `version int` optimistic lock.
- [ ] `validate` endpoint runs all 14 rules; aggregates errors; no DB write.
- [ ] SEL function set matches spec exactly; budgets + RE2 enforced.
- [ ] All 11 node executors pass integration tests.
- [ ] `workflow_runs.state` enum matches spec exactly (no `queued` / `paused`).
- [ ] `workflow_steps` table name correct.
- [ ] No `webhook` trigger present anywhere.
- [ ] 90-day archive move green.
- [ ] Backstage visible to Admin + PO only; chat UI has no trace surface.
- [ ] `00-overview.md` §0.8: H = done.

## Cross-cutting checklist

1. **AuthZ tap.** Endpoints under capability 16 (create workflow); run cancel via §22.13 admin + Project Owner.
2. **Audit tap.** `workflow.created/edited/deleted`, `workflow.run_started/_finished/_cancelled`, `workflow.step_started/_finished/_failed`, `instruct.issued/rejected_loop`, `subagent.spawned/destroyed`, `approval.requested/resolved`.
3. **Rate limit bucket.** `workflow-run`, `workflow-validate`, `workflow-step` (worker-side).
4. **Observability.** Metrics `workflow_runs_total{state}`, `workflow_step_duration_ms`, `sel_budget_overflow_total`, `linter_issues_total{rule}`.
5. **RFC 7807.** `https://smap.local/problems/{workflow-validation, sel-budget-exceeded, workflow-step-failed, workflow-run-cancelled, workflow-run-timeout, loop-guard-exceeded}`.
6. **Migration policy.** `0014_workflows`, `0015_workflow_runs`, `0016_workflow_runs_archive`.
7. **Secrets.** None introduced.

## Risks

- **SEL escape.** Primary attack surface; fuzz-test the grammar on each release; pin `google-re2` version.
- **Parked runs lingering.** `wait_for_event` honoured only up to `run_max_seconds` / `idle_max_seconds`; dispatcher reaps expired parks.
- **`parallel`/`join` failure modes.** `all` fails on first branch failure (siblings cancelled); `any` succeeds on first success (siblings cancelled); `count:N` documented clearly.
- **Archive skew.** Worker last-run metric alerts if > 25 h old; cutoff configurable to drain backlog safely.
