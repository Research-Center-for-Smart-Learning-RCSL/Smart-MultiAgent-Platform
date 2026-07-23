"""A2A synchronous call depth/cycle guard (R9.15)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.orchestration.application import a2a_call_chain
from contexts.orchestration.domain.errors import A2ACallDepthExceeded, A2ACallLoop
from contexts.orchestration.domain.models import (
    A2A_CALL_MAX_DEPTH,
    A2AEnvelope,
    A2AMessageType,
)

_A = str(uuid.uuid4())
_B = str(uuid.uuid4())


def test_root_hop_is_depth_one() -> None:
    depth, path = a2a_call_chain.next_hop(_A)
    assert depth == 1
    assert path == (_A,)


def test_nested_hop_extends_path() -> None:
    with a2a_call_chain.enter(1, (_A,)):
        depth, path = a2a_call_chain.next_hop(_B)
    assert depth == 2
    assert path == (_A, _B)


def test_cycle_is_rejected() -> None:
    with a2a_call_chain.enter(2, (_A, _B)), pytest.raises(A2ACallLoop):
        a2a_call_chain.next_hop(_A)


def test_depth_cap_is_enforced() -> None:
    deep_path = tuple(str(uuid.uuid4()) for _ in range(A2A_CALL_MAX_DEPTH))
    with a2a_call_chain.enter(A2A_CALL_MAX_DEPTH, deep_path), pytest.raises(A2ACallDepthExceeded):
        a2a_call_chain.next_hop(_B)


def test_chain_resets_after_context() -> None:
    with a2a_call_chain.enter(3, (_A, _B)):
        pass
    assert a2a_call_chain.current() == (0, ())


def test_explicit_base_extends_inbound_chain() -> None:
    # U-1: the workflow-worker path supplies the inbound chain explicitly (its
    # ContextVar is empty — a different process bound the CALL). A hop to C at
    # base (1, (B,)) becomes depth 2, path (B, C).
    _C = str(uuid.uuid4())
    depth, path = a2a_call_chain.next_hop(_C, base=(1, (_B,)))
    assert depth == 2
    assert path == (_B, _C)


def test_explicit_base_detects_cycle() -> None:
    # U-1: targeting an agent already on the inbound path raises, even though the
    # ContextVar is at its default (the cross-process cycle F-24 could not catch).
    assert a2a_call_chain.current() == (0, ())
    with pytest.raises(A2ACallLoop):
        a2a_call_chain.next_hop(_B, base=(1, (_B,)))


def test_explicit_base_wins_over_contextvar() -> None:
    # U-1: when both are present the explicit base is authoritative.
    _C = str(uuid.uuid4())
    with a2a_call_chain.enter(1, (_A,)):
        depth, path = a2a_call_chain.next_hop(_C, base=(2, (_A, _B)))
    assert depth == 3
    assert path == (_A, _B, _C)


def test_envelope_roundtrips_call_chain() -> None:
    env = A2AEnvelope(
        id=uuid.uuid4(),
        from_agent=uuid.uuid4(),
        to_agent=_B,
        workflow_run_id=None,
        type=A2AMessageType.CALL,
        payload={"input": "hi"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        call_depth=2,
        call_path=(_A, _B),
    )
    restored = A2AEnvelope.from_dict(env.to_dict())
    assert restored.call_depth == 2
    assert restored.call_path == (_A, _B)


async def test_dispatch_a2a_signal_includes_call_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-2 (dispatch side): the a2a workflow-signal carries call_depth/call_path
    across the process boundary — the exact fields _dispatch dropped (F-24)."""
    from contexts.orchestration.application import a2a_handler

    captured: dict[str, Any] = {}

    async def _fake_enqueue(task: str, *args: Any, **_: Any) -> None:
        captured["task"] = task
        captured["args"] = args

    monkeypatch.setattr("shared_kernel.queue.enqueue", _fake_enqueue)

    env = A2AEnvelope(
        id=uuid.uuid4(),
        from_agent=uuid.uuid4(),
        to_agent=_B,
        workflow_run_id=None,
        type=A2AMessageType.CALL,
        payload={"input": "hi"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        call_depth=2,
        call_path=(_A, _B),
    )
    await a2a_handler._dispatch_a2a_workflow_signal(env)

    assert captured["task"] == "workflow_signal"
    source, payload = captured["args"]
    assert source == "a2a"
    assert payload["call_depth"] == 2
    assert payload["call_path"] == [_A, _B]


async def test_agent_invocation_forwards_inbound_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-2 (read side): the agent_invocation executor reads the inbound chain out
    of ctx.trigger_payload and threads it into a2a_call — the seam that broke."""
    from contexts.workflow.application.executors import agent_invocation
    from contexts.workflow.domain.models import NodeSpec, NodeType, RunContext

    captured: dict[str, Any] = {}

    class _FakeFacade:
        def __init__(self, _db: Any) -> None: ...

        async def a2a_call(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"reply": "ok"}

    async def _noop_emit(*_a: Any, **_k: Any) -> None: ...

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _FakeFacade)
    monkeypatch.setattr("shared_kernel.audit.emit", _noop_emit)

    ctx = RunContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        workflow_def={},
        variables={},
        trigger_payload={"call_depth": 2, "call_path": [_A, _B]},
    )
    node = NodeSpec(
        id="n1",
        type=NodeType.AGENT_INVOCATION,
        config={"agent_id": str(uuid.uuid4()), "input_template": "hi"},
    )
    outcome = await agent_invocation.execute(ctx, node, None)  # type: ignore[arg-type]

    assert outcome.port == "success"
    assert captured["inbound_call_depth"] == 2
    assert captured["inbound_call_path"] == [_A, _B]


def test_legacy_envelope_without_chain_defaults() -> None:
    # A message serialised before this field existed must still parse.
    raw: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "from_agent": None,
        "to_agent": _A,
        "workflow_run_id": None,
        "type": "call",
        "payload": {},
        "correlation_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
    }
    restored = A2AEnvelope.from_dict(raw)
    assert restored.call_depth == 0
    assert restored.call_path == ()
