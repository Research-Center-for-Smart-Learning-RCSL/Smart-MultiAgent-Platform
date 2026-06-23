"""Semantic linter — 16 blocking rules + 6 advisory warnings (§5).

Rule 15 (SEC-L5) parses every SEL expression at save time so invalid or
non-whitelisted expressions are rejected up front instead of failing silently
at run time.

Each rule is a pure function: check(definition, ...) → list[LintIssue].
The aggregator collects all issues without stopping at the first error.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from contexts.workflow.domain.errors import (
    SELBudgetExceeded,
    SELForbiddenConstruct,
    SELSyntaxError,
)
from contexts.workflow.domain.models import LintIssue, ValidationResult
from contexts.workflow.sel.evaluator import validate as validate_sel

_VAR_REF_RE = re.compile(
    r"\{\{\s*"
    r"([a-zA-Z_][a-zA-Z0-9_]*"
    r"(?:\.[a-zA-Z_][a-zA-Z0-9_]*|\[\d+\]|\[\"[^\"]*\"\]|\['[^']*'\])*)"
    r"\s*\}\}",
)

# Ports each node type is allowed to emit from
_ALLOWED_PORTS: dict[str, set[str]] = {
    "trigger": {"default"},
    "agent_invocation": {"success", "failure"},
    "approval_gate": {"approved", "rejected", "timeout"},
    "condition": set(),  # user-declared ports + default_port — checked dynamically
    "instruct": {"success", "failure"},
    "subagent_spawn": {"success", "failure"},
    "wait_for_event": {"default", "timeout"},
    "parallel": {"default"},
    "join": {"default", "timeout"},
    "set_variable": {"default"},
    "end": set(),
}

# Nodes with deterministic multi-ports that must all be covered (rule 13)
_MULTI_PORT_NODES: dict[str, set[str]] = {
    "agent_invocation": {"success", "failure"},
    "approval_gate": {"approved", "rejected", "timeout"},
    "instruct": {"success", "failure"},
    "subagent_spawn": {"success", "failure"},
}

# Known ctx.* keys
_KNOWN_CTX_KEYS = frozenset(
    {
        "run_id",
        "workflow_id",
        "chatroom_id",
        "workspace_id",
        "project_id",
        "now_unix",
        "trigger_type",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nodes_by_id(defn: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in defn.get("nodes", [])}


def _build_adjacency(
    defn: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Return (outgoing, incoming) edge maps keyed by node id."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    inc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in defn.get("edges", []):
        out[e["from"]].append(e)
        inc[e["to"]].append(e)
    return dict(out), dict(inc)


def _extract_var_refs(text: str) -> list[str]:
    return [m.group(1).split(".")[0].split("[")[0] for m in _VAR_REF_RE.finditer(text)]


def _collect_all_text_fields(config: dict[str, Any]) -> list[str]:
    """Collect all string values from config that could contain {{ refs }}."""
    texts: list[str] = []
    for v in config.values():
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict):
                    texts.extend(_collect_all_text_fields(item))
        elif isinstance(v, dict):
            texts.extend(_collect_all_text_fields(v))
    return texts


def _collect_agent_ids(node: dict[str, Any]) -> list[str]:
    """Extract all agent UUID references from a node config."""
    ids: list[str] = []
    config = node.get("config", {})
    for key in (
        "agent_id",
        "target_agent_id",
        "issuer_agent_id",
        "leader_agent_id",
        "parent_agent_id",
    ):
        if key in config:
            ids.append(config[key])
    if "approvers" in config:
        ids.extend(config["approvers"])
    return ids


# ---------------------------------------------------------------------------
# Rule 1: Exactly one trigger, entry_node_id equals it
# ---------------------------------------------------------------------------


def rule_01_single_trigger(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    triggers = [n for n in defn.get("nodes", []) if n.get("type") == "trigger"]
    if len(triggers) == 0:
        issues.append(LintIssue(1, "error", "Workflow must have exactly one trigger node"))
    elif len(triggers) > 1:
        for t in triggers[1:]:
            issues.append(LintIssue(1, "error", "Multiple trigger nodes found", node_id=t["id"]))
    if triggers:
        entry = defn.get("entry_node_id")
        if entry != triggers[0]["id"]:
            issues.append(
                LintIssue(
                    1,
                    "error",
                    f"entry_node_id '{entry}' does not match trigger node '{triggers[0]['id']}'",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Rule 2: Unique node ids and edge ids
# ---------------------------------------------------------------------------


def rule_02_unique_ids(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    node_ids: set[str] = set()
    for n in defn.get("nodes", []):
        nid = n["id"]
        if nid in node_ids:
            issues.append(LintIssue(2, "error", f"Duplicate node id: {nid}", node_id=nid))
        node_ids.add(nid)

    edge_ids: set[str] = set()
    for e in defn.get("edges", []):
        eid = e["id"]
        if eid in edge_ids:
            issues.append(LintIssue(2, "error", f"Duplicate edge id: {eid}", edge_id=eid))
        edge_ids.add(eid)
    return issues


# ---------------------------------------------------------------------------
# Rule 3: Edges valid
# ---------------------------------------------------------------------------


def rule_03_edges_valid(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    nodes = _nodes_by_id(defn)
    seen_triples: set[tuple[str, str, str]] = set()

    for e in defn.get("edges", []):
        eid = e["id"]
        frm, to = e.get("from", ""), e.get("to", "")
        port = e.get("from_port", "default")

        if frm not in nodes:
            issues.append(LintIssue(3, "error", f"Edge references unknown from node '{frm}'", edge_id=eid))
        if to not in nodes:
            issues.append(LintIssue(3, "error", f"Edge references unknown to node '{to}'", edge_id=eid))

        if frm in nodes:
            ntype = nodes[frm].get("type", "")
            allowed = _ALLOWED_PORTS.get(ntype, set())
            if ntype == "condition":
                config = nodes[frm].get("config", {})
                branch_ports = {b["port"] for b in config.get("branches", [])}
                default_port = config.get("default_port", "default")
                allowed = branch_ports | {default_port}
            if ntype == "end":
                issues.append(
                    LintIssue(3, "error", f"end node '{frm}' cannot have outgoing edges", edge_id=eid),
                )
            elif allowed and port not in allowed:
                issues.append(
                    LintIssue(
                        3,
                        "error",
                        f"Port '{port}' not valid for {ntype} node '{frm}'",
                        edge_id=eid,
                    )
                )

        triple = (frm, port, to)
        if triple in seen_triples:
            issues.append(LintIssue(3, "error", f"Duplicate edge triple ({frm}, {port}, {to})", edge_id=eid))
        seen_triples.add(triple)

    return issues


# ---------------------------------------------------------------------------
# Rule 4: Reachability from trigger
# ---------------------------------------------------------------------------


def rule_04_reachability(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    nodes = _nodes_by_id(defn)
    outgoing, _ = _build_adjacency(defn)
    entry = defn.get("entry_node_id", "")

    if entry not in nodes:
        return issues  # rule 1 will catch this

    reachable: set[str] = set()
    stack = [entry]
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        for e in outgoing.get(nid, []):
            stack.append(e["to"])

    for nid in nodes:
        if nid != entry and nid not in reachable:
            issues.append(LintIssue(4, "error", f"Node '{nid}' is unreachable from the trigger", node_id=nid))
    return issues


# ---------------------------------------------------------------------------
# Rule 5: Termination — path to end
# ---------------------------------------------------------------------------


def rule_05_termination(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    nodes = _nodes_by_id(defn)
    outgoing, _ = _build_adjacency(defn)

    end_nodes = {nid for nid, n in nodes.items() if n.get("type") == "end"}
    if not end_nodes:
        issues.append(LintIssue(5, "error", "Workflow has no end node"))
        return issues

    can_reach_end: set[str] = set(end_nodes)
    changed = True
    while changed:
        changed = False
        for nid in nodes:
            if nid in can_reach_end:
                continue
            for e in outgoing.get(nid, []):
                if e["to"] in can_reach_end:
                    can_reach_end.add(nid)
                    changed = True
                    break

    entry = defn.get("entry_node_id", "")
    reachable: set[str] = set()
    stack = [entry]
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        for e in outgoing.get(nid, []):
            stack.append(e["to"])

    for nid in reachable:
        if nid in end_nodes:
            continue
        if nid not in can_reach_end:
            ntype = nodes[nid].get("type", "")
            if ntype == "wait_for_event" and not outgoing.get(nid):
                issues.append(
                    LintIssue(
                        5,
                        "warning",
                        f"wait_for_event node '{nid}' has no path to end (permanent listener?)",
                        node_id=nid,
                    )
                )
            else:
                issues.append(LintIssue(5, "error", f"Node '{nid}' has no path to any end node", node_id=nid))
    return issues


# ---------------------------------------------------------------------------
# Rule 6 & 7: Referenced agents exist + same project
# (These require DB lookups — accept agent_ids_in_project as input)
# ---------------------------------------------------------------------------


def rule_06_agents_exist(
    defn: dict[str, Any],
    valid_agent_ids: frozenset[str],
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for n in defn.get("nodes", []):
        for aid in _collect_agent_ids(n):
            if aid not in valid_agent_ids:
                issues.append(
                    LintIssue(
                        6,
                        "error",
                        f"Referenced agent '{aid}' does not exist in the workflow's project",
                        node_id=n["id"],
                    )
                )
    return issues


def rule_07_agent_scope(
    defn: dict[str, Any],
    valid_agent_ids: frozenset[str],
) -> list[LintIssue]:
    # Rule 7 is enforced by rule 6 — valid_agent_ids already scoped to project
    return []


# ---------------------------------------------------------------------------
# Rule 8: Chatroom scope
# ---------------------------------------------------------------------------


def rule_08_chatroom_scope(
    defn: dict[str, Any],
    valid_chatroom_ids: frozenset[str],
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for n in defn.get("nodes", []):
        config = n.get("config", {})
        for key in ("chatroom_id", "target_chatroom_id"):
            cid = config.get(key)
            if cid and cid not in valid_chatroom_ids:
                issues.append(
                    LintIssue(
                        8,
                        "error",
                        f"Chatroom '{cid}' not in workspace's project scope",
                        node_id=n["id"],
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Rule 9: Sub-agent depth
# ---------------------------------------------------------------------------


def rule_09_subagent_depth(
    defn: dict[str, Any],
    subagent_parent_ids: frozenset[str],
) -> list[LintIssue]:
    """subagent_parent_ids: set of agent IDs that are themselves sub-agents."""
    issues: list[LintIssue] = []
    for n in defn.get("nodes", []):
        if n.get("type") != "subagent_spawn":
            continue
        parent = n.get("config", {}).get("parent_agent_id")
        if parent and parent in subagent_parent_ids:
            issues.append(
                LintIssue(
                    9,
                    "error",
                    f"parent_agent_id '{parent}' is itself a sub-agent (depth > 1 forbidden)",
                    node_id=n["id"],
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Rule 10: Instruct cycle pre-check
# ---------------------------------------------------------------------------


def rule_10_instruct_cycle(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    # Build static agent-instruction graph: issuer → target
    graph: dict[str, set[str]] = defaultdict(set)
    for n in defn.get("nodes", []):
        if n.get("type") != "instruct":
            continue
        config = n.get("config", {})
        issuer = config.get("issuer_agent_id", "")
        target = config.get("target_agent_id", "")
        if issuer and target:
            graph[issuer].add(target)

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2  # noqa: N806 — conventional DFS colour constants
    color: dict[str, int] = {aid: WHITE for aid in graph}
    for target_set in graph.values():
        for t in target_set:
            if t not in color:
                color[t] = WHITE

    def _dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, set()):
            if color.get(neighbor, WHITE) == GRAY:
                return True
            if color.get(neighbor, WHITE) == WHITE and _dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for start in list(color):
        if color[start] == WHITE and _dfs(start):
            issues.append(
                LintIssue(
                    10,
                    "error",
                    "Instruct edges form a cycle in the agent-instruction graph",
                )
            )
            break

    return issues


# ---------------------------------------------------------------------------
# Rule 11: Condition branch uniqueness
# ---------------------------------------------------------------------------


def rule_11_condition_branches(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for n in defn.get("nodes", []):
        if n.get("type") != "condition":
            continue
        config = n.get("config", {})
        default_port = config.get("default_port", "default")
        seen: set[str] = set()
        for b in config.get("branches", []):
            port = b.get("port", "")
            if port in seen:
                issues.append(
                    LintIssue(
                        11,
                        "error",
                        f"Duplicate branch port '{port}'",
                        node_id=n["id"],
                    )
                )
            if port == default_port:
                issues.append(
                    LintIssue(
                        11,
                        "error",
                        f"Branch port '{port}' conflicts with default_port",
                        node_id=n["id"],
                    )
                )
            seen.add(port)
    return issues


# ---------------------------------------------------------------------------
# Rule 12: Variable references resolve
# ---------------------------------------------------------------------------


def rule_12_variable_references(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    declared_vars = set(defn.get("variables", {}).keys())

    for n in defn.get("nodes", []):
        config = n.get("config", {})
        texts = _collect_all_text_fields(config)
        ntype = n.get("type", "")

        for text in texts:
            for ref_name in _extract_var_refs(text):
                if ref_name in ("trigger", "ctx"):
                    continue
                if ref_name not in declared_vars:
                    # Required fields in condition.when are errors; templates are warnings
                    is_condition_when = ntype == "condition"
                    level = "error" if is_condition_when else "warning"
                    issues.append(
                        LintIssue(
                            12,
                            level,
                            f"Variable reference '{ref_name}' is not declared",
                            node_id=n["id"],
                        )
                    )
    return issues


# ---------------------------------------------------------------------------
# Rule 13: Port coverage
# ---------------------------------------------------------------------------


def rule_13_port_coverage(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    outgoing, _ = _build_adjacency(defn)
    nodes = _nodes_by_id(defn)

    for nid, n in nodes.items():
        ntype = n.get("type", "")
        required_ports = _MULTI_PORT_NODES.get(ntype)
        if not required_ports:
            continue

        config = n.get("config", {})
        on_error = config.get("on_error", {})
        strategy = on_error.get("strategy", "fail")

        connected_ports = {e.get("from_port", "default") for e in outgoing.get(nid, [])}

        for port in required_ports:
            if port not in connected_ports and strategy != "continue":
                issues.append(
                    LintIssue(
                        13,
                        "error",
                        f"Port '{port}' of {ntype} node '{nid}' is not connected "
                        f"and on_error.strategy is not 'continue'",
                        node_id=nid,
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Rule 14: Parallel/join pairing
# ---------------------------------------------------------------------------


def rule_14_parallel_join(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    outgoing, incoming = _build_adjacency(defn)
    nodes_by_id = _nodes_by_id(defn)

    for n in defn.get("nodes", []):
        nid = n["id"]
        ntype = n.get("type", "")
        if ntype == "parallel":
            out_count = len(outgoing.get(nid, []))
            if out_count < 2:
                issues.append(
                    LintIssue(
                        14,
                        "error",
                        f"parallel node '{nid}' needs ≥ 2 outgoing edges, has {out_count}",
                        node_id=nid,
                    )
                )
        elif ntype == "join":
            in_count = len(incoming.get(nid, []))
            if in_count < 2:
                issues.append(
                    LintIssue(
                        14,
                        "error",
                        f"join node '{nid}' needs ≥ 2 incoming edges, has {in_count}",
                        node_id=nid,
                    )
                )

            # A join in mode=all (default) fed (directly or through a chain
            # of single-input passthrough nodes) by a condition will deadlock:
            # condition routes flow to exactly one branch, so the join can
            # never collect all incoming edges.
            join_mode = n.get("config", {}).get("mode", "all")
            if join_mode == "all":
                for edge in incoming.get(nid, []):
                    cursor = edge.get("from", "")
                    visited: set[str] = set()
                    while cursor and cursor not in visited:
                        visited.add(cursor)
                        pred = nodes_by_id.get(cursor)
                        if not pred:
                            break
                        if pred.get("type") == "condition":
                            issues.append(
                                LintIssue(
                                    14,
                                    "error",
                                    f"join node '{nid}' (mode=all) is fed by "
                                    f"condition node '{pred['id']}' — condition "
                                    f"routes to only one branch, so the join "
                                    f"will deadlock",
                                    node_id=nid,
                                )
                            )
                            break
                        cursor_inc = incoming.get(cursor, [])
                        if len(cursor_inc) == 1:
                            cursor = cursor_inc[0].get("from", "")
                        else:
                            break
    return issues


# ---------------------------------------------------------------------------
# Rule 15: SEL expressions parse + use only whitelisted functions (SEC-L5)
# ---------------------------------------------------------------------------


def rule_15_sel_expressions(defn: dict[str, Any]) -> list[LintIssue]:
    """Parse every SEL expression at save time.

    Previously the linter never parsed SEL, so a syntactically invalid or
    non-whitelisted expression saved cleanly and only failed at run time —
    where the condition executor swallows the error and silently falls through
    to the default port. Validating here turns that into a blocking save error.
    """
    issues: list[LintIssue] = []
    for n in defn.get("nodes", []):
        ntype = n.get("type", "")
        config = n.get("config", {})
        exprs: list[str] = []
        if ntype == "condition":
            exprs = [b.get("when", "") for b in config.get("branches", [])]
        elif ntype == "set_variable":
            exprs = [a.get("expression", "") for a in config.get("assignments", [])]

        for expr in exprs:
            # Empty/whitespace expressions are caught by the structural rules
            # (a condition branch needs a real predicate); don't double-report.
            if not isinstance(expr, str) or not expr.strip():
                continue
            try:
                validate_sel(expr)
            except (SELSyntaxError, SELForbiddenConstruct, SELBudgetExceeded) as exc:
                issues.append(
                    LintIssue(15, "error", f"Invalid SEL expression {expr!r}: {exc}", node_id=n["id"]),
                )
    return issues


# ---------------------------------------------------------------------------
# Rule 16: fallback_node_id references an existing node
# ---------------------------------------------------------------------------


def rule_16_fallback_node_exists(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    node_ids = {n["id"] for n in defn.get("nodes", [])}
    for n in defn.get("nodes", []):
        on_error = n.get("config", {}).get("on_error") or {}
        if on_error.get("strategy") != "fallback":
            continue
        fid = on_error.get("fallback_node_id")
        if not fid:
            issues.append(
                LintIssue(
                    16,
                    "error",
                    f"Node '{n['id']}' has on_error.strategy=fallback but no fallback_node_id",
                    node_id=n["id"],
                )
            )
        elif fid == n["id"]:
            issues.append(
                LintIssue(
                    16,
                    "error",
                    f"Node '{n['id']}' has fallback_node_id pointing to itself "
                    f"(causes recursive retry storm until loop guard fires)",
                    node_id=n["id"],
                )
            )
        elif fid not in node_ids:
            issues.append(
                LintIssue(
                    16,
                    "error",
                    f"Node '{n['id']}' references fallback_node_id '{fid}' which does not exist",
                    node_id=n["id"],
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Advisory warnings (§5.2)
# ---------------------------------------------------------------------------


def advisory_warnings(defn: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    declared_vars = set(defn.get("variables", {}).keys())
    outgoing, _ = _build_adjacency(defn)

    # Collect used vars
    used_vars: set[str] = set()
    for n in defn.get("nodes", []):
        config = n.get("config", {})
        for text in _collect_all_text_fields(config):
            used_vars.update(_extract_var_refs(text))
        if n.get("type") == "agent_invocation":
            ov = config.get("output_variable")
            if ov:
                used_vars.add(ov)
        if n.get("type") == "set_variable":
            for a in config.get("assignments", []):
                used_vars.add(a.get("variable", ""))

    # W1: Unreachable variables
    for vname in declared_vars:
        if vname not in used_vars:
            issues.append(LintIssue(0, "warning", f"Variable '{vname}' is declared but never referenced"))

    for n in defn.get("nodes", []):
        config = n.get("config", {})
        ntype = n.get("type", "")

        # W2: agent_invocation timeout > 300
        if ntype == "agent_invocation":
            ts = config.get("timeout_seconds", 120)
            if ts > 300:
                issues.append(LintIssue(0, "warning", f"timeout_seconds={ts} > 300", node_id=n["id"]))

        # W3: wait_for_event with no timeout path
        if ntype == "wait_for_event":
            edges = outgoing.get(n["id"], [])
            has_timeout = any(e.get("from_port") == "timeout" for e in edges)
            if not has_timeout:
                issues.append(LintIssue(0, "warning", "wait_for_event has no timeout edge", node_id=n["id"]))

        # W4: approval_gate timeout > 3600
        if ntype == "approval_gate":
            ts = config.get("timeout_seconds", 0)
            if ts > 3600:
                issues.append(
                    LintIssue(0, "warning", f"approval timeout_seconds={ts} > 3600", node_id=n["id"]),
                )

        # W5: Cron sub-minute frequency
        if ntype == "trigger" and config.get("trigger_type") == "cron":
            cron_expr = config.get("cron_expression", "")
            parts = cron_expr.split()
            if parts and parts[0] not in ("*", "0"):
                pass  # Not sub-minute
            elif parts and parts[0] == "*":
                issues.append(
                    LintIssue(
                        0,
                        "warning",
                        "Cron expression fires every minute or faster",
                        node_id=n["id"],
                    ),
                )

    # W6: loop_guard > 1000
    lg = defn.get("loop_guard", {}).get("max_visits_per_node", 200)
    if lg > 1000:
        issues.append(LintIssue(0, "warning", f"loop_guard.max_visits_per_node={lg} > 1000"))

    return issues


# ---------------------------------------------------------------------------
# Aggregate validator
# ---------------------------------------------------------------------------


def validate_definition(
    defn: dict[str, Any],
    *,
    valid_agent_ids: frozenset[str] = frozenset(),
    valid_chatroom_ids: frozenset[str] = frozenset(),
    subagent_parent_ids: frozenset[str] = frozenset(),
) -> ValidationResult:
    """Run all 16 blocking rules + advisory warnings. Returns aggregate result."""
    all_issues: list[LintIssue] = []

    # Structural rules (no DB needed)
    all_issues.extend(rule_01_single_trigger(defn))
    all_issues.extend(rule_02_unique_ids(defn))
    all_issues.extend(rule_03_edges_valid(defn))
    all_issues.extend(rule_04_reachability(defn))
    all_issues.extend(rule_05_termination(defn))
    all_issues.extend(rule_06_agents_exist(defn, valid_agent_ids))
    all_issues.extend(rule_07_agent_scope(defn, valid_agent_ids))
    all_issues.extend(rule_08_chatroom_scope(defn, valid_chatroom_ids))
    all_issues.extend(rule_09_subagent_depth(defn, subagent_parent_ids))
    all_issues.extend(rule_10_instruct_cycle(defn))
    all_issues.extend(rule_11_condition_branches(defn))
    all_issues.extend(rule_12_variable_references(defn))
    all_issues.extend(rule_13_port_coverage(defn))
    all_issues.extend(rule_14_parallel_join(defn))
    all_issues.extend(rule_15_sel_expressions(defn))
    all_issues.extend(rule_16_fallback_node_exists(defn))

    # Advisory
    all_issues.extend(advisory_warnings(defn))

    errors = [i for i in all_issues if i.level == "error"]
    warnings = [i for i in all_issues if i.level == "warning"]

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
