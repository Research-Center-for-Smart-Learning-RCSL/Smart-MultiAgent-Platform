# Workflow Definition — Design Note & Companion Guide

This document accompanies `workflow.schema.json`. The schema enforces **structural** validity; this document specifies the **semantic** rules, the **expression language**, and the **linter** that both run on every save and every run start.

Reference: `REQUIREMENTS.md` §14 "Workflow Engine" and §15 "Wake-up, Approval, Instruct, Sub-Agents".

---

## 1. Execution model recap

- A workflow is a **DAG of activities**; each activity is driven by an **internal FSM**; activities communicate via the event bus.
- A run starts at exactly one node (`entry_node_id`) and ends when a run reaches an `end` node *or* every live branch terminates *or* a `run_max_seconds`/`idle_max_seconds` timeout expires *or* `loop_guard.max_visits_per_node` is exceeded.
- Multiple live branches are allowed (via `parallel` / `join` / `subagent_spawn` with `wait_for_all = false`).

---

## 2. Node types

| Node | Outputs / ports | Purpose |
|---|---|---|
| `trigger` | `default` | Entry point. Defines how a run is initiated. Exactly one `trigger` must exist per workflow; it is the `entry_node_id`. |
| `agent_invocation` | `success`, `failure` | Run an Agent for one turn with an input template, capture its reply into a variable. |
| `approval_gate` | `approved`, `rejected`, `timeout` | Block until N approver agents vote; timeout falls back to the leader's verdict (see §15.4 of REQUIREMENTS). |
| `condition` | user-defined ports (`default_port` fallback) | Evaluate up to 20 boolean branches in order; dispatch to the first matching port. |
| `instruct` | `success`, `failure` | Send an instruction to a target agent. Target cannot refuse (REQUIREMENTS §15.5). Loop detection is mandatory. |
| `subagent_spawn` | `success`, `failure` | Create child agent(s) under a parent. Max recursion depth = 1 (REQUIREMENTS §15.6). |
| `wait_for_event` | `default`, `timeout` | Pause the branch until a qualifying event arrives (room message, A2A message, timer, variable condition). |
| `parallel` | `default` | Fan-out marker. All outgoing edges are taken concurrently. |
| `join` | `default`, `timeout` | Fan-in marker. Waits for `all` / `any` / `count(N)` incoming branches. |
| `set_variable` | `default` | Compute expressions and assign to workflow variables. |
| `end` | — | Terminal. Marks the workflow run as `success` or `failure`. |

### 2.1 Port conventions

Edges reference source ports via `from_port`. The visual editor renders a distinct handle per documented port. Unknown ports are rejected by the linter.

Reserved ports by node type:

| Node | Allowed `from_port` values |
|---|---|
| `trigger`, `agent_invocation` (success case), `set_variable`, `parallel`, `subagent_spawn` (success case), `instruct` (success case), `wait_for_event` (fired case), `join` (all/any/count case) | `default` |
| `agent_invocation`, `instruct`, `subagent_spawn` | `success`, `failure` |
| `approval_gate` | `approved`, `rejected`, `timeout` |
| `condition` | any user-declared `branches[].port` + `default_port` |
| `wait_for_event`, `join` | `default`, `timeout` |
| `end` | (no outgoing edges) |

---

## 3. Expression language (SEL v1)

The condition node, edge guards, `set_variable` assignments, and `wait_for_event.variable_matches.expression` all use SEL — the **SMAP Expression Language**. It is deliberately small, pure, and side-effect-free.

### 3.1 Grammar (EBNF)

```
expression  = or_expr
or_expr     = and_expr ("or" and_expr)*
and_expr    = not_expr ("and" not_expr)*
not_expr    = "not" not_expr | cmp_expr
cmp_expr    = add_expr ( ("=="|"!="|"<"|"<="|">"|">=") add_expr )?
add_expr    = mul_expr ( ("+"|"-") mul_expr )*
mul_expr    = unary  ( ("*"|"/"|"%") unary )*
unary       = "-" unary | primary
primary     = literal | var_ref | func_call | "(" expression ")"
literal     = number | string | "true" | "false" | "null"
var_ref     = "{{" IDENT ("." IDENT | "[" (INT | string) "]")* "}}"
func_call   = IDENT "(" [ expression ("," expression)* ] ")"
```

### 3.2 Allowed functions (whitelist)

| Function | Signature | Notes |
|---|---|---|
| `len(x)` | string/array/object → int | Length of string, array, or object. |
| `lower(s)` / `upper(s)` | string → string | |
| `contains(haystack, needle)` | (string|array, any) → bool | Substring or array-membership. |
| `startswith(s, prefix)` / `endswith(s, suffix)` | (string, string) → bool | |
| `matches(s, pattern)` | (string, regex) → bool | RE2 syntax; 5 ms CPU budget. |
| `int(x)` / `float(x)` / `str(x)` | cast | `int("abc")` raises. |
| `json_get(obj, path)` | (object, string) → any | Dotted/bracket path, e.g. `"messages[0].role"`. |
| `coalesce(a, b, …)` | (...) → any | First non-null. |
| `now_unix()` | () → number | Unix seconds at evaluation time. |
| `abs(x)`, `min(...)`, `max(...)` | numeric | |

**Forbidden.** No variable assignment, no function definition, no attribute-of-function access, no Python dunder methods, no imports, no process-level I/O, no network, no loops, no lambdas. The evaluator is a hand-rolled interpreter; SMAP does **not** pass expressions to `eval`, `exec`, or any templating engine that allows attribute traversal.

### 3.3 Evaluation limits

- Max AST depth: 16.
- Max expression length: 1 000 chars (JSON-schema enforced).
- Max wall-clock per evaluation: 5 ms (per-thread CPU timer).
- Regex timeout: 5 ms (RE2, no catastrophic backtracking).

Any limit breach yields an evaluation error; the node follows its `on_error` strategy, or if none, the run fails.

### 3.4 Template interpolation

`template_string` fields (e.g. `input_template`, `question_template`) are **not** SEL. They are simple `{{ var.path }}` interpolation with the same variable syntax as SEL's `var_ref`. There is **no** control flow in templates. Unresolved vars render as the literal `{{ var.path }}`.

---

## 4. Variables

### 4.1 Scopes

Three scopes are readable in expressions and templates:

| Scope | Prefix | Examples |
|---|---|---|
| Workflow variables | (none) | `{{ answer }}`, `{{ scores[0] }}` |
| Trigger payload | `trigger.` | `{{ trigger.message.content }}` |
| Context / environment | `ctx.` | `{{ ctx.run_id }}`, `{{ ctx.chatroom_id }}`, `{{ ctx.now_unix }}` |

Writable scope: only workflow variables (declared in `variables`). Nodes write through `output_variable` or via `set_variable` assignments.

### 4.2 Types and coercion

- `string | number | boolean | object | array`.
- Reads of undeclared variables yield `null`.
- Comparisons between mismatched types always return `false` (never raise). This is intentional — linting catches most mistakes, and runs should not crash on harmless mis-types.

---

## 5. Semantic linter (runs on save and on run-start)

The JSON Schema catches only structural errors. The following checks are enforced in Python and block both save and run:

### 5.1 Blocking rules (errors)

1. **Exactly one `trigger`** and `entry_node_id` must equal it.
2. **Unique node ids** and unique edge ids.
3. **Every edge** references existing `from` / `to` nodes, valid `from_port` for the source type, and unique `(from, from_port, to)` triples.
4. **Reachability**: every non-trigger node must be reachable from the trigger.
5. **Termination**: every reachable node must have at least one path to an `end`. Exception: `wait_for_event` nodes with no outgoing edges are flagged only as warnings because they are sometimes used as permanent passive listeners inside always-on workflows; the `run_max_seconds` bound still protects the run.
6. **Referenced agents exist**: every `agent_id`, `target_agent_id`, `issuer_agent_id`, `leader_agent_id`, `parent_agent_id`, `approvers[]` must resolve to an Agent in the same Project.
7. **Agent-scope rule**: all referenced agents must live in the **same project** as the workflow. Cross-project references are forbidden.
8. **Chatroom scope**: all referenced `chatroom_id` values must belong to the workflow's Workspace's Project.
9. **SubAgent depth**: any `subagent_spawn` node whose `parent_agent_id` is itself a sub-agent is rejected (depth > 1).
10. **Instruct cycle pre-check**: static analysis of `instruct` edges builds an agent-instruction graph; if it contains a cycle, save is rejected. (Runtime loop detection still runs; this is belt-and-suspenders.)
11. **Condition branch uniqueness**: `branches[].port` values are unique within a condition node and different from `default_port`.
12. **Variable references**: every `{{ var }}` used in templates, guards, or expressions resolves to a declared `variables.<name>` or a `trigger.*` / `ctx.*` known key. Warnings for unknown reads of workflow vars are upgraded to errors only when the value is required by the node (e.g. `input_template` with no `{{…}}` is fine; an unresolvable `{{missing}}` in `condition.when` is not).
13. **Port coverage**: for nodes with multiple deterministic ports (`approval_gate`, `agent_invocation`, `instruct`, `subagent_spawn`), every port must be either connected by at least one edge or explicitly covered by `on_error.strategy = "continue"`. Otherwise the run could silently stall.
14. **Parallel/join pairing**: any `parallel` node must have at least two outgoing edges; any `join` node must have at least two incoming edges. Orphan `parallel` / `join` are rejected.

### 5.2 Advisory rules (warnings)

1. Unreachable workflow variables.
2. `agent_invocation.timeout_seconds` > 300 s (user can confirm and proceed).
3. `wait_for_event` with no timeout path downstream.
4. `approval_gate.timeout_seconds` > 1 hour.
5. Cron expression with sub-minute frequency.
6. `loop_guard.max_visits_per_node` > 1 000.

---

## 6. Runtime behavior summary (for implementers)

- Every node execution creates a `workflow_steps` row: `(run_id, node_id, state, input, output, started_at, ended_at, error)`.
- The engine consumes one node at a time per branch from the event bus. Parallel branches run as independent consumers.
- On-error strategies:
  - `fail`: mark run failed, cancel all sibling branches (`parallel` branches honor this by emitting cancellation events).
  - `continue`: treat the node as succeeded with `output = null`; follow default port.
  - `retry`: retry up to `retry_max` times with `retry_backoff_ms` linear backoff.
  - `fallback`: follow an edge to `fallback_node_id` (must be a valid node id in the same workflow).
- `instruct` nodes always carry `chain_id` and `path` per REQUIREMENTS §15.5. Max chain depth and wall-clock are project-scoped config, not in this schema.
- `approval_gate` publishes an `approval.requested` WebSocket event so the UI can render a live status card per run.

---

## 7. Example

```json
{
  "schema_version": "1.0",
  "name": "Weekly Research Digest",
  "description": "Every Monday at 09:00, research agent drafts a digest, editor agent reviews, leader agent approves.",
  "variables": {
    "draft":  { "type": "string", "default": "" },
    "review": { "type": "string", "default": "" }
  },
  "timeouts": { "run_max_seconds": 1800, "idle_max_seconds": 900 },
  "entry_node_id": "t1",
  "nodes": [
    { "id": "t1", "type": "trigger", "position": { "x": 0, "y": 0 },
      "config": { "trigger_type": "cron", "cron_expression": "0 9 * * 1", "timezone": "Asia/Taipei" } },

    { "id": "draft_node", "type": "agent_invocation", "position": { "x": 200, "y": 0 },
      "config": { "agent_id": "00000000-0000-0000-0000-0000000000aa",
                  "input_template": "Write a digest of last week's news.",
                  "output_variable": "draft",
                  "timeout_seconds": 180 } },

    { "id": "review_node", "type": "agent_invocation", "position": { "x": 400, "y": 0 },
      "config": { "agent_id": "00000000-0000-0000-0000-0000000000bb",
                  "input_template": "Review and suggest edits:\n\n{{ draft }}",
                  "output_variable": "review",
                  "timeout_seconds": 180 } },

    { "id": "approve", "type": "approval_gate", "position": { "x": 600, "y": 0 },
      "config": { "mode": "single",
                  "leader_agent_id": "00000000-0000-0000-0000-0000000000cc",
                  "approvers": ["00000000-0000-0000-0000-0000000000cc"],
                  "timeout_seconds": 1800,
                  "question_template": "Publish this digest?\n\n{{ review }}" } },

    { "id": "publish",    "type": "agent_invocation", "position": { "x": 800, "y": -100 },
      "config": { "agent_id": "00000000-0000-0000-0000-0000000000cc",
                  "input_template": "Post to the team chatroom:\n\n{{ review }}",
                  "output_variable": "posted",
                  "timeout_seconds": 60 } },

    { "id": "discard",    "type": "set_variable", "position": { "x": 800, "y": 100 },
      "config": { "assignments": [ { "variable": "posted", "expression": "\"discarded\"" } ] } },

    { "id": "end_ok",     "type": "end", "position": { "x": 1000, "y": 0 }, "config": { "status": "success", "return_variables": ["posted"] } }
  ],
  "edges": [
    { "id": "e1", "from": "t1",          "to": "draft_node" },
    { "id": "e2", "from": "draft_node",  "to": "review_node", "from_port": "success" },
    { "id": "e3", "from": "review_node", "to": "approve",     "from_port": "success" },
    { "id": "e4", "from": "approve",     "to": "publish",     "from_port": "approved" },
    { "id": "e5", "from": "approve",     "to": "discard",     "from_port": "rejected" },
    { "id": "e6", "from": "approve",     "to": "discard",     "from_port": "timeout" },
    { "id": "e7", "from": "publish",     "to": "end_ok",      "from_port": "success" },
    { "id": "e8", "from": "discard",     "to": "end_ok" }
  ]
}
```

This example passes both the JSON Schema and all 14 semantic linter rules.

---

## 8. Versioning

- `schema_version` is `"1.0"` for this release.
- Non-breaking additions (new node types, new functions, new optional fields) bump to `1.x`.
- Breaking changes bump to `2.0`; the engine will run a one-shot migration script at database upgrade time, and workflows flagged as unmigratable are moved to a `legacy` workspace for manual rework.

---

## 9. Open items flagged for later

1. **Sub-flows / includes**: no support in v1. Workflows cannot invoke other workflows. The stakeholder may request this later; it would be a new node type (`subflow_invoke`) and a new linter rule set.
2. **Human-in-the-loop approvals**: §15.4 approvals are agent-only in v1. When humans enter, `approval_gate` will gain a `human_approvers` field and a new WebSocket panel.
3. **Typed variables**: v1 types are advisory (nothing is coerced at write time). A future `strict_types` flag would enforce.
