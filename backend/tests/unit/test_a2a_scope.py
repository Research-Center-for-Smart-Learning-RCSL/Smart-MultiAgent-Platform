"""Unit tests for `contexts.agents.application.a2a_scope` (E.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from contexts.agents.application.a2a_scope import A2ACheckInput, evaluate
from contexts.agents.domain.models import (
    Agent,
    AgentModelHint,
    ContextMode,
    PromptStrategy,
)


def _agent(project_id: uuid.UUID, *, enabled: bool, wakeup: dict | None = None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name="x",
        model_hint=AgentModelHint.CLAUDE,
        key_group_id=uuid.uuid4(),
        system_prompt="",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        graphrag_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=enabled,
        wakeup_config=wakeup or {},
        workflow_capabilities={},
        version=1,
        deleted_at=None,
        created_at=datetime.now(tz=UTC),
    )


def test_same_project_shared_context_allowed() -> None:
    p = uuid.uuid4()
    ctx = uuid.uuid4()
    v = evaluate(
        A2ACheckInput(
            caller=_agent(p, enabled=True),
            callee=_agent(p, enabled=True),
            callee_attached_context_ids=frozenset({ctx}),
            caller_invocation_context_id=ctx,
            callee_project_deleted=False,
        )
    )
    assert v.allowed


def test_cross_project_denied() -> None:
    v = evaluate(
        A2ACheckInput(
            caller=_agent(uuid.uuid4(), enabled=True),
            callee=_agent(uuid.uuid4(), enabled=True),
            callee_attached_context_ids=frozenset(),
            caller_invocation_context_id=uuid.uuid4(),
            callee_project_deleted=False,
        )
    )
    assert not v.allowed
    assert "cross-project" in v.reason


def test_call_only_allows_without_shared_context() -> None:
    p = uuid.uuid4()
    wakeup = {"triggers": {"call_only": {"enabled": True}}}
    v = evaluate(
        A2ACheckInput(
            caller=_agent(p, enabled=True),
            callee=_agent(p, enabled=True, wakeup=wakeup),
            callee_attached_context_ids=frozenset(),
            caller_invocation_context_id=None,
            callee_project_deleted=False,
        )
    )
    assert v.allowed


def test_a2a_disabled_on_either_side_denied() -> None:
    p = uuid.uuid4()
    for caller_en, callee_en in ((False, True), (True, False)):
        v = evaluate(
            A2ACheckInput(
                caller=_agent(p, enabled=caller_en),
                callee=_agent(p, enabled=callee_en),
                callee_attached_context_ids=frozenset({uuid.uuid4()}),
                caller_invocation_context_id=uuid.uuid4(),
                callee_project_deleted=False,
            )
        )
        assert not v.allowed


def test_soft_deleted_callee_project_denied() -> None:
    p = uuid.uuid4()
    ctx = uuid.uuid4()
    v = evaluate(
        A2ACheckInput(
            caller=_agent(p, enabled=True),
            callee=_agent(p, enabled=True),
            callee_attached_context_ids=frozenset({ctx}),
            caller_invocation_context_id=ctx,
            callee_project_deleted=True,
        )
    )
    assert not v.allowed
    assert "soft-deleted" in v.reason
