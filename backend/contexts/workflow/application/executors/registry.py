"""Executor dispatch registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import NodeSpec, NodeType, RunContext, StepOutcome

Executor = Callable[[RunContext, NodeSpec, AsyncSession], Awaitable[StepOutcome]]

_REGISTRY: dict[NodeType, Executor] = {}


def register(node_type: NodeType) -> Callable[[Executor], Executor]:
    def decorator(fn: Executor) -> Executor:
        _REGISTRY[node_type] = fn
        return fn
    return decorator


def get_executor(node_type: NodeType) -> Executor:
    executor = _REGISTRY.get(node_type)
    if executor is None:
        raise ValueError(f"No executor registered for node type {node_type.value}")
    return executor


# Side-effect imports — register all executors
from contexts.workflow.application.executors import (  # noqa: E402, F401
    trigger as _trigger,
    agent_invocation as _agent_invocation,
    approval_gate as _approval_gate,
    condition as _condition,
    instruct as _instruct,
    subagent_spawn as _subagent_spawn,
    wait_for_event as _wait_for_event,
    parallel as _parallel,
    join as _join,
    set_variable as _set_variable,
    end as _end,
)
