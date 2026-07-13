"""Wiring tripwire: the activities context imports nothing from agents (AC-9).

The acyclic-context-graph guarantee (activities-platform-core §5.1) is that
``contexts/activities`` never imports ``contexts/agents`` — MCP/webhook
composition happens in the worker layer through ``AgentsFacade`` only. This
static scan fails if any activities module reintroduces that import.
"""

from __future__ import annotations

import ast
import pathlib

_ACTIVITIES = pathlib.Path(__file__).resolve().parents[2] / "contexts" / "activities"


def test_activities_does_not_import_agents() -> None:
    offenders: list[str] = []
    for py in _ACTIVITIES.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("contexts.agents"):
                    offenders.append(f"{py.name}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("contexts.agents"):
                        offenders.append(f"{py.name}: import {alias.name}")
    assert not offenders, f"activities must not import agents: {offenders}"
