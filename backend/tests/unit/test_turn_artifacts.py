"""Unit test for TurnEngine._persist_artifacts (Code-Interpreter artifacts).

Drives the method with a fake DB + a stubbed AttachmentService so no MinIO or
Postgres is required — asserts dedupe, the skip of non-inlined artifacts, and
that binding targets the reply message id.
"""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from contexts.agents.application.runtime.turn_engine import TurnEngine


class _FakeDB:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeAttachmentService:
    instances: ClassVar[list[_FakeAttachmentService]] = []

    def __init__(self, _db: Any) -> None:
        self.ingested: list[dict[str, Any]] = []
        self.bound: dict[str, Any] | None = None
        _FakeAttachmentService.instances.append(self)

    async def ingest_agent_artifact(self, **kwargs: Any) -> SimpleNamespace:
        self.ingested.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    async def bind_agent_artifacts(self, **kwargs: Any) -> int:
        self.bound = kwargs
        return len(kwargs["attachment_ids"])


@pytest.mark.asyncio
async def test_persist_artifacts_dedupes_skips_and_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = SimpleNamespace(_db=_FakeDB())
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    msg_id = uuid.uuid4()
    room = uuid.uuid4()
    png = base64.b64encode(b"\x89PNG").decode("ascii")
    artifacts = [
        {"filename": "chart.png", "mime": "image/png", "rel_path": "/w/chart.png", "b64": png},
        {"filename": "chart.png", "mime": "image/png", "rel_path": "/w/chart.png", "b64": png},  # dup
        {"filename": "big.csv", "mime": "text/csv", "rel_path": "/w/big.csv", "b64": None},  # not inlined
    ]

    count = await TurnEngine._persist_artifacts(engine, agent, room, msg_id, artifacts)  # type: ignore[arg-type]

    assert count == 1  # dedup + skip of the non-inlined artifact
    svc = _FakeAttachmentService.instances[-1]
    assert len(svc.ingested) == 1
    assert svc.ingested[0]["filename"] == "chart.png"
    assert svc.ingested[0]["project_id"] == agent.project_id
    assert svc.bound is not None
    assert svc.bound["message_id"] == msg_id
    assert svc.bound["chatroom_id"] == room
    assert engine._db.committed is True


@pytest.mark.asyncio
async def test_persist_artifacts_empty_is_noop() -> None:
    engine = SimpleNamespace(_db=_FakeDB())
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    count = await TurnEngine._persist_artifacts(engine, agent, uuid.uuid4(), uuid.uuid4(), [])  # type: ignore[arg-type]
    assert count == 0
    assert engine._db.committed is False
