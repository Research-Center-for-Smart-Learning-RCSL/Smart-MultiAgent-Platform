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


# Side-effect imports — register all executors.
#
# Every module below calls ``@register(NodeType.X)`` at import time; importing
# it here is what populates ``_REGISTRY``. The per-line unused-import suppression
# keeps an automated lint pass (the ff19610 regression that deleted 10 of these
# and left only ``trigger``) from silently stripping a registration again — the
# imports are deliberately "unused", and a *block*-level suppression does NOT
# stop ruff's autofix from removing inner names, so each line carries its own.
# The ``test_executor_completeness`` test is the real backstop: every
# ``NodeType`` must resolve via ``get_executor``.
from contexts.workflow.application.executors import agent_invocation as _agent_invocation  # noqa: E402, F401
from contexts.workflow.application.executors import approval_gate as _approval_gate  # noqa: E402, F401
from contexts.workflow.application.executors import condition as _condition  # noqa: E402, F401
from contexts.workflow.application.executors import end as _end  # noqa: E402, F401
from contexts.workflow.application.executors import instruct as _instruct  # noqa: E402, F401
from contexts.workflow.application.executors import join as _join  # noqa: E402, F401
from contexts.workflow.application.executors import parallel as _parallel  # noqa: E402, F401
from contexts.workflow.application.executors import set_variable as _set_variable  # noqa: E402, F401
from contexts.workflow.application.executors import subagent_spawn as _subagent_spawn  # noqa: E402, F401
from contexts.workflow.application.executors import trigger as _trigger  # noqa: E402, F401
from contexts.workflow.application.executors import wait_for_event as _wait_for_event  # noqa: E402, F401
