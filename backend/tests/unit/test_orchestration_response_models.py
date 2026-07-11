"""Orchestration read-API response models (dossier 2026-07-11-orchestration-response-models).

Pins the typed response contract added to `app/api/v1/orchestration.py`: each `_*_out`
helper must produce a model whose JSON serialization carries every field the frontend
reads, with the right types — and the DLQ `attempt_count` must land as a JSON number even
though `read_dlq` hands it over as a string (Q-1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.api.v1.orchestration import (
    ApprovalOut,
    ApprovalWithVotesOut,
    DlqEntryOut,
    _approval_out,
    _approval_with_votes_out,
    _instance_out,
    _instruction_out,
)
from contexts.orchestration.domain.models import (
    AgentInstance,
    Approval,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
    Instruction,
    InstructionState,
)

_TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _approval() -> Approval:
    return Approval(
        id=_uid(1),
        workflow_run_id=_uid(2),
        mode=ApprovalMode.MAJORITY,
        leader_agent_id=_uid(3),
        approver_agent_ids=(_uid(3), _uid(4)),
        timeout_seconds=300,
        state=ApprovalState.PENDING,
        started_at=_TS,
        ended_at=None,
    )


def test_approval_out_serializes_every_field_with_enum_values() -> None:
    out = _approval_out(_approval())
    assert isinstance(out, ApprovalOut)
    body = out.model_dump(mode="json")
    assert body == {
        "id": str(_uid(1)),
        "workflow_run_id": str(_uid(2)),
        "mode": "majority",
        "leader_agent_id": str(_uid(3)),
        "approver_agent_ids": [str(_uid(3)), str(_uid(4))],
        "timeout_seconds": 300,
        "state": "pending",
        "started_at": _TS.isoformat(),
        "ended_at": None,
    }
    # The list route uses ApprovalOut — it must NOT carry a votes key.
    assert "votes" not in body


def test_approval_with_votes_out_embeds_typed_votes() -> None:
    vote = ApprovalVote(
        approval_id=_uid(1),
        voter_agent_id=_uid(4),
        vote=True,
        rationale="looks good",
        cast_at=_TS,
    )
    out = _approval_with_votes_out(_approval(), [vote])
    assert isinstance(out, ApprovalWithVotesOut)
    body = out.model_dump(mode="json")
    assert body["state"] == "pending"
    assert body["votes"] == [
        {
            "approval_id": str(_uid(1)),
            "voter_agent_id": str(_uid(4)),
            "vote": True,
            "rationale": "looks good",
            "cast_at": _TS.isoformat(),
        }
    ]


def test_instruction_out_serializes_every_field() -> None:
    instruction = Instruction(
        id=_uid(10),
        chain_id=_uid(11),
        path=(_uid(3), _uid(4)),
        depth=2,
        issuer_agent_id=_uid(3),
        target_agent_id=_uid(4),
        payload={"task": "do it"},
        state=InstructionState.DELIVERED,
        issued_at=_TS,
        resolved_at=None,
    )
    body = _instruction_out(instruction).model_dump(mode="json")
    assert body == {
        "id": str(_uid(10)),
        "chain_id": str(_uid(11)),
        "path": [str(_uid(3)), str(_uid(4))],
        "depth": 2,
        "issuer_agent_id": str(_uid(3)),
        "target_agent_id": str(_uid(4)),
        "payload": {"task": "do it"},
        "state": "delivered",
        "issued_at": _TS.isoformat(),
        "resolved_at": None,
    }


def test_instance_out_preserves_nullable_fields() -> None:
    instance = AgentInstance(
        id=_uid(20),
        agent_id=_uid(21),
        parent_id=None,
        chatroom_id=None,
        run_context={"k": "v"},
        task_description=None,
        state="running",
        spawned_at=_TS,
        destroyed_at=None,
    )
    body = _instance_out(instance).model_dump(mode="json")
    assert body == {
        "id": str(_uid(20)),
        "agent_id": str(_uid(21)),
        "parent_id": None,
        "chatroom_id": None,
        "run_context": {"k": "v"},
        "task_description": None,
        "state": "running",
        "spawned_at": _TS.isoformat(),
        "destroyed_at": None,
    }


def test_dlq_entry_out_coerces_attempt_count_to_number() -> None:
    # read_dlq stringifies every Redis field, so attempt_count arrives as "3".
    raw = {
        "stream_entry_id": "1700000000000-0",
        "stream_id": "a2a:agent:x:dlq",
        "envelope": "{}",
        "attempt_count": "3",
        "last_error": "boom",
        "moved_at": _TS.isoformat(),
    }
    out = DlqEntryOut(**raw)
    assert out.attempt_count == 3
    assert isinstance(out.attempt_count, int)
    # And it serializes as a JSON number, matching the frontend's numeric type (Q-1).
    assert out.model_dump(mode="json")["attempt_count"] == 3
