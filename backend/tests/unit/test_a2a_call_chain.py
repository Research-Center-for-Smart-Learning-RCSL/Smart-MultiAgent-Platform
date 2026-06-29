"""A2A synchronous call depth/cycle guard (R9.15)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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


def test_legacy_envelope_without_chain_defaults() -> None:
    # A message serialised before this field existed must still parse.
    raw = {
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
