"""The `chat_export` task ends in `failed` when arq cancels it (F-5, Q-5).

arq enforces a job timeout by cancelling the coroutine. `CancelledError` is a
`BaseException`, so a handler for `Exception` never ran and the job read
`running` for the 24 hours its record lives.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.workers.tasks import conversation as task_mod
from contexts.conversation.application.export_service import ExportJobState, ExportJobStatus
from contexts.conversation.domain.models import ExportSenderScope

_JOB, _ROOM, _OWNER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"failed": [], "ready": [], "building": asyncio.Event()}
    state = ExportJobState(
        job_id=_JOB,
        chatroom_id=_ROOM,
        owner_user_id=_OWNER,
        status=ExportJobStatus.QUEUED,
        created_at=datetime.now(UTC),
        object_key=None,
        bucket=None,
        sender_scope=ExportSenderScope.ALL,
    )

    async def _get(job_id: uuid.UUID) -> ExportJobState:
        return state

    async def _running(job_id: uuid.UUID) -> None:
        pass

    async def _failed(*, job_id: uuid.UUID, error: str) -> None:
        # Slow on purpose: the write must survive the cancellation it runs under.
        await asyncio.sleep(0.01)
        calls["failed"].append(error)

    async def _ready(**kwargs: object) -> None:
        calls["ready"].append(kwargs)

    class _Service:
        def __init__(self, db: object) -> None:
            pass

        async def build_and_upload_export(self, **kwargs: object) -> tuple[str, str]:
            calls["building"].set()
            await asyncio.Event().wait()  # a build that outlives its timeout
            raise AssertionError("unreachable")

    @asynccontextmanager
    async def _session() -> Any:
        yield object()

    monkeypatch.setattr(task_mod.export_service, "get", _get)
    monkeypatch.setattr(task_mod.export_service, "mark_running", _running)
    monkeypatch.setattr(task_mod.export_service, "mark_failed", _failed)
    monkeypatch.setattr(task_mod.export_service, "mark_ready", _ready)
    monkeypatch.setattr(task_mod, "get_sessionmaker", lambda: _session)
    monkeypatch.setattr("contexts.conversation.application.chat_export_service.ChatExportService", _Service)
    return calls


async def test_a_cancelled_export_is_marked_failed_and_still_cancels(recorded: dict[str, Any]) -> None:
    job = asyncio.create_task(task_mod.chat_export({}, str(_JOB), str(_ROOM), str(_OWNER)))
    building = asyncio.create_task(recorded["building"].wait())
    done, _ = await asyncio.wait({job, building}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    assert building in done, f"the build never started: {job.exception() if job.done() else 'hung'}"
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert recorded["failed"] == ["timed out"]
    assert recorded["ready"] == []


def test_chat_export_runs_once_with_its_own_timeout() -> None:
    """Q-5: no retry (a re-run would deliver a second archive and audit row),
    and a timeout long enough for a 2 GiB attachment copy."""
    from app.workers.main import WorkerSettings

    entry = next(f for f in WorkerSettings.functions if getattr(f, "name", None) == "chat_export")
    assert entry.max_tries == 1
    assert entry.timeout_s == task_mod.CHAT_EXPORT_TIMEOUT_S == 1800
