"""Research export delivery: expiry, re-attach, authority, timeouts.

Dossier: docs/tasks/2026-11-08-research-data-integrity/spec.md, milestone 1
(audit 2026-10-05 F-5, F-6, F-12, F-13, F-14; AC-1 to AC-6).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import research_export as routes
from app.workers.tasks import conversation as task_mod
from contexts.conversation.application import research_export_service as svc
from contexts.conversation.application.research_export_service import (
    ResearchExportJobState,
    ResearchExportJobStatus,
)
from contexts.conversation.domain.errors import ResearchExportExpired, ResearchExportInProgress
from shared_kernel.auth.permissions import Principal

_WORKSPACE = uuid.uuid4()
_OWNER = uuid.uuid4()
_T0 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


class _Redis:
    """The subset of redis.asyncio the job store uses, with TTLs recorded."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttl[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def eval(self, script: str, numkeys: int, key: str, expected: str, new: str, ttl: str) -> int:
        if self.store.get(key) != expected:
            return 0
        self.store[key] = new
        self.ttl[key] = int(ttl)
        return 1


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    fake = _Redis()
    monkeypatch.setattr(svc, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    current = [_T0]
    monkeypatch.setattr(svc, "now", lambda: current[0])
    return current


def _principal(user_id: uuid.UUID = _OWNER, *, is_admin: bool = False) -> Principal:
    return Principal(user_id=user_id, is_admin=is_admin, email_verified=True)


# ---- AC-1: a ready record never outlives its file --------------------------


async def test_ready_record_expires_23_hours_after_the_upload_started(
    redis: _Redis, clock: list[datetime]
) -> None:
    state = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert await svc.mark_running(state.job_id)
    upload_started = _T0
    clock[0] = _T0 + timedelta(minutes=10)
    assert await svc.mark_ready(
        job_id=state.job_id, bucket="exports", object_key="k", upload_started_at=upload_started
    )
    # 23 h from the upload's start, not from mark_ready: the sweep and the bucket
    # lifecycle count the object's age from the upload.
    assert redis.ttl[f"research_export:{state.job_id}"] == int(
        timedelta(hours=22, minutes=50).total_seconds()
    )


async def test_a_build_that_outlived_the_archive_lifetime_is_failed_not_ready(
    redis: _Redis, clock: list[datetime]
) -> None:
    state = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert await svc.mark_running(state.job_id)
    clock[0] = _T0 + svc.READY_LIFETIME
    assert not await svc.mark_ready(
        job_id=state.job_id, bucket="exports", object_key="k", upload_started_at=_T0
    )
    after = await svc.get(state.job_id)
    assert after is not None
    assert (after.status, after.error) == (ResearchExportJobStatus.FAILED, "expired before ready")


async def test_ready_export_whose_object_is_gone_is_expired_not_ready(
    redis: _Redis, clock: list[datetime]
) -> None:
    ready = ResearchExportJobState(
        job_id=uuid.uuid4(),
        workspace_id=_WORKSPACE,
        owner_user_id=_OWNER,
        status=ResearchExportJobStatus.READY,
        created_at=_T0,
        bucket="exports",
        object_key="gone.zip",
    )
    minio = AsyncMock()
    minio.stat.return_value = None
    with (
        patch.object(routes.research_export_service, "get", AsyncMock(return_value=ready)),
        patch.object(routes, "still_entitled", AsyncMock(return_value=True)),
        patch.object(routes, "get_minio_client", return_value=minio),
        pytest.raises(ResearchExportExpired),
    ):
        await routes.get_research_export(job_id=ready.job_id, principal=_principal(), db=AsyncMock())


# ---- AC-2 / AC-3: one active export per owner per workspace, re-attachable --


async def test_second_create_while_one_runs_names_the_running_job(
    redis: _Redis, clock: list[datetime]
) -> None:
    first = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    with pytest.raises(ResearchExportInProgress) as exc:
        await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert exc.value.job_id == first.job_id


async def test_create_after_the_previous_finished_takes_over_and_becomes_latest(
    redis: _Redis, clock: list[datetime]
) -> None:
    first = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert await svc.mark_running(first.job_id)
    assert await svc.mark_ready(job_id=first.job_id, bucket="exports", object_key="k", upload_started_at=_T0)
    second = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    latest = await svc.latest(owner_user_id=_OWNER, workspace_id=_WORKSPACE)
    assert latest is not None
    assert latest.job_id == second.job_id


async def test_latest_returns_the_ready_job_after_the_dialog_lost_it(
    redis: _Redis, clock: list[datetime]
) -> None:
    job = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert await svc.mark_running(job.job_id)
    assert await svc.mark_ready(job_id=job.job_id, bucket="exports", object_key="k", upload_started_at=_T0)
    latest = await svc.latest(owner_user_id=_OWNER, workspace_id=_WORKSPACE)
    assert latest is not None
    assert (latest.job_id, latest.status) == (job.job_id, ResearchExportJobStatus.READY)
    assert await svc.latest(owner_user_id=uuid.uuid4(), workspace_id=_WORKSPACE) is None


async def test_an_abandoned_running_job_does_not_hold_the_workspace(
    redis: _Redis, clock: list[datetime]
) -> None:
    stuck = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    await svc.mark_running(stuck.job_id)
    clock[0] = _T0 + svc.ACTIVE_EXPORT_STALE_AFTER + timedelta(seconds=1)
    fresh = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    old = await svc.get(stuck.job_id)
    assert old is not None
    assert old.status == ResearchExportJobStatus.FAILED
    assert fresh.job_id != stuck.job_id
    # The abandoned run cannot later overwrite that failure with a ready record.
    assert not await svc.mark_ready(
        job_id=stuck.job_id, bucket="exports", object_key="k", upload_started_at=_T0
    )
    after = await svc.get(stuck.job_id)
    assert after is not None
    assert (after.status, after.error) == (ResearchExportJobStatus.FAILED, "abandoned")


async def test_a_job_that_waited_in_the_queue_keeps_the_slot_while_it_runs(
    redis: _Redis, clock: list[datetime]
) -> None:
    job = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    clock[0] = _T0 + timedelta(minutes=20)
    assert await svc.mark_running(job.job_id)
    # Past the stale window from creation, well inside it from the run's start.
    clock[0] = _T0 + svc.ACTIVE_EXPORT_STALE_AFTER + timedelta(minutes=1)
    with pytest.raises(ResearchExportInProgress) as exc:
        await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert exc.value.job_id == job.job_id


async def test_a_job_runs_only_from_queued_and_a_finished_one_keeps_its_outcome(
    redis: _Redis, clock: list[datetime]
) -> None:
    job = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    assert await svc.mark_running(job.job_id)
    assert not await svc.mark_running(job.job_id)
    assert await svc.mark_ready(job_id=job.job_id, bucket="exports", object_key="k", upload_started_at=_T0)
    await svc.mark_failed(job_id=job.job_id, error="late")
    after = await svc.get(job.job_id)
    assert after is not None
    assert after.status == ResearchExportJobStatus.READY


# ---- export_slot.claim races ---------------------------------------------


class _RacingRedis(_Redis):
    """Another create takes the slot between this claim's read and its takeover."""

    def __init__(self, rival: str | None) -> None:
        super().__init__()
        self.rival = rival

    async def eval(self, script: str, numkeys: int, key: str, expected: str, new: str, ttl: str) -> int:
        if self.rival is None:
            self.store.pop(key, None)
        else:
            self.store[key] = self.rival
        return 0


async def test_a_claim_that_loses_the_takeover_names_the_winner() -> None:
    from contexts.conversation.application import export_slot

    rival = uuid.uuid4()
    fake = _RacingRedis(str(rival))
    fake.store["slot"] = str(uuid.uuid4())

    async def _blocks(holder: uuid.UUID) -> bool:
        return holder == rival

    assert await export_slot.claim(fake, "slot", uuid.uuid4(), ttl_seconds=60, blocks=_blocks) == rival


async def test_a_claim_that_never_settles_raises_instead_of_naming_itself() -> None:
    from contexts.conversation.application import export_slot

    class _Flapping(_RacingRedis):
        async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
            self.store[key] = str(uuid.uuid4())  # someone else holds it at every attempt
            return None

    async def _never(holder: uuid.UUID) -> bool:
        return False

    with pytest.raises(export_slot.SlotContended):
        await export_slot.claim(_Flapping(None), "slot", uuid.uuid4(), ttl_seconds=60, blocks=_never)


# ---- Worker: AC-4, AC-5, AC-6 ---------------------------------------------


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "failed": [],
        "ready": [],
        "notified": [],
        "built": 0,
        "status": ResearchExportJobStatus.QUEUED,
        "entitled": True,
        "notify_raises": False,
        "build_blocks": False,
        "building": asyncio.Event(),
        "failing": asyncio.Event(),
        "ready_ok": True,
    }

    async def _get(job_id: uuid.UUID) -> ResearchExportJobState:
        return ResearchExportJobState(
            job_id=job_id,
            workspace_id=_WORKSPACE,
            owner_user_id=_OWNER,
            status=calls["status"],
            created_at=_T0,
        )

    async def _running(job_id: uuid.UUID) -> bool:
        return calls["status"] is ResearchExportJobStatus.QUEUED

    async def _failed(*, job_id: uuid.UUID, error: str) -> None:
        calls["failing"].set()
        await asyncio.sleep(0.05)  # must survive the cancellation it runs under
        calls["failed"].append(error)

    async def _ready(**kwargs: object) -> bool:
        calls["ready"].append(kwargs)
        return bool(calls["ready_ok"])

    async def _entitled(db: object, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        return bool(calls["entitled"])

    class _Builder:
        async def build_and_upload(self, **kwargs: object) -> tuple[str, str]:
            calls["building"].set()
            if calls["build_blocks"]:
                await asyncio.Event().wait()
            calls["built"] += 1
            return "exports", "k"

    class _Facade:
        def __init__(self, db: object) -> None:
            pass

        async def list_chatroom_ids_for_workspace(self, wid: uuid.UUID) -> list[uuid.UUID]:
            return [uuid.uuid4()]

        async def list_submissions_for_research_export(self, **kwargs: object) -> list[Any]:
            return []

        async def list_observations_for_rooms(self, **kwargs: object) -> list[Any]:
            return []

        async def list_alerts_for_rooms(self, **kwargs: object) -> list[Any]:
            return []

        async def list_messages_for_rooms(self, **kwargs: object) -> list[Any]:
            return []

        async def send(self, **kwargs: object) -> None:
            if calls["notify_raises"]:
                raise RuntimeError("notification insert failed")
            calls["notified"].append(kwargs)

    class _Session:
        def begin(self) -> Any:
            @asynccontextmanager
            async def _tx() -> Any:
                yield self

            return _tx()

    @asynccontextmanager
    async def _session() -> Any:
        yield _Session()

    monkeypatch.setattr(svc, "get", _get)
    monkeypatch.setattr(svc, "mark_running", _running)
    monkeypatch.setattr(svc, "mark_failed", _failed)
    monkeypatch.setattr(svc, "mark_ready", _ready)
    monkeypatch.setattr(task_mod, "get_sessionmaker", lambda: _session)
    monkeypatch.setattr("contexts.conversation.application.research_export_access.still_entitled", _entitled)
    monkeypatch.setattr(
        "contexts.conversation.application.research_export_builder.ResearchExportBuilder", _Builder
    )
    monkeypatch.setattr("contexts.conversation.interfaces.facade.ConversationFacade", _Facade)
    monkeypatch.setattr("contexts.activities.interfaces.facade.ActivitiesFacade", _Facade)
    monkeypatch.setattr("contexts.notification.interfaces.facade.NotificationFacade", _Facade)
    return calls


async def _run(job_id: uuid.UUID | None = None) -> str:
    return await task_mod.research_data_export({}, str(job_id or uuid.uuid4()), str(_WORKSPACE), str(_OWNER))


async def test_a_notification_failure_leaves_the_export_ready(worker: dict[str, Any]) -> None:
    worker["notify_raises"] = True
    assert await _run() == "ok"
    # One build, which writes the one audit row; nothing runs it again.
    assert worker["built"] == 1
    assert len(worker["ready"]) == 1
    assert worker["failed"] == []


async def test_ready_notification_carries_what_the_download_action_needs(worker: dict[str, Any]) -> None:
    job = uuid.uuid4()
    await _run(job)
    (sent,) = worker["notified"]
    assert sent["metadata"] == {"export": "research", "job_id": str(job), "workspace_id": str(_WORKSPACE)}


async def test_a_requester_no_longer_entitled_gets_a_failed_job_and_no_bundle(worker: dict[str, Any]) -> None:
    worker["entitled"] = False
    assert await _run() == "failed:forbidden"
    assert worker["built"] == 0
    assert worker["ready"] == []
    assert worker["failed"] == ["export authority revoked"]


@pytest.mark.parametrize(
    "status", [ResearchExportJobStatus.RUNNING, ResearchExportJobStatus.READY, ResearchExportJobStatus.FAILED]
)
async def test_a_job_not_queued_is_never_run_again(
    worker: dict[str, Any], status: ResearchExportJobStatus
) -> None:
    worker["status"] = status
    assert await _run() == "skipped:not_queued"
    assert worker["built"] == 0
    assert worker["ready"] == worker["failed"] == []


async def test_a_cancelled_export_is_marked_failed_and_still_cancels(worker: dict[str, Any]) -> None:
    worker["build_blocks"] = True
    job = asyncio.create_task(_run())
    building = asyncio.create_task(worker["building"].wait())
    done, _ = await asyncio.wait({job, building}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    assert building in done, f"the build never started: {job.exception() if job.done() else 'hung'}"
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert worker["failed"] == ["timed out"]
    assert worker["ready"] == []


async def test_the_timeout_write_survives_a_second_cancellation(worker: dict[str, Any]) -> None:
    worker["build_blocks"] = True
    job = asyncio.create_task(_run())
    await asyncio.wait_for(worker["building"].wait(), timeout=5)
    job.cancel()
    await asyncio.wait_for(worker["failing"].wait(), timeout=5)
    job.cancel()  # arrives while the failure write is in flight
    with pytest.raises(asyncio.CancelledError):
        await job
    await asyncio.sleep(0.1)
    assert worker["failed"] == ["timed out"]


async def test_a_run_whose_job_was_taken_over_announces_nothing(worker: dict[str, Any]) -> None:
    worker["ready_ok"] = False
    assert await _run() == "failed:not_ready"
    assert worker["notified"] == []
    assert worker["failed"] == []


def test_research_export_runs_once_with_its_own_timeout() -> None:
    from app.workers.main import WorkerSettings

    entry = next(f for f in WorkerSettings.functions if getattr(f, "name", None) == "research_data_export")
    assert entry.max_tries == 1
    assert entry.timeout_s == svc.RESEARCH_EXPORT_TIMEOUT_S


def test_in_progress_is_a_409_naming_the_job_and_expired_is_a_410() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from contexts.conversation.interfaces import error_mapping

    app = FastAPI()
    error_mapping.register(app)
    running = uuid.uuid4()

    @app.get("/busy")
    async def _busy() -> None:
        raise ResearchExportInProgress(job_id=running)

    @app.get("/gone")
    async def _gone() -> None:
        raise ResearchExportExpired("x")

    client = TestClient(app)
    busy, gone = client.get("/busy"), client.get("/gone")
    assert busy.status_code == 409
    assert busy.json()["type"].endswith("/conversation/research-export-in-progress")
    assert busy.json()["job_id"] == str(running)
    assert gone.status_code == 410
    assert gone.json()["type"].endswith("/conversation/export-expired")


async def test_latest_is_owner_only_and_404s_without_a_job(redis: _Redis, clock: list[datetime]) -> None:
    from contexts.conversation.domain.errors import ExportJobNotFound

    owner_check = AsyncMock()
    with patch.object(routes, "_assert_workspace_owner", owner_check), pytest.raises(ExportJobNotFound):
        await routes.get_latest_research_export(
            workspace_id=_WORKSPACE, principal=_principal(), db=AsyncMock()
        )
    owner_check.assert_awaited_once()

    job = await svc.create(workspace_id=_WORKSPACE, owner_user_id=_OWNER)
    with patch.object(routes, "_assert_workspace_owner", owner_check):
        out = await routes.get_latest_research_export(
            workspace_id=_WORKSPACE, principal=_principal(), db=AsyncMock()
        )
    assert (out.job_id, out.status) == (job.job_id, "queued")


# ---- AC-5: the GET re-checks authority -------------------------------------


async def test_a_demoted_owner_cannot_fetch_the_download(redis: _Redis, clock: list[datetime]) -> None:
    from fastapi import HTTPException

    ready = ResearchExportJobState(
        job_id=uuid.uuid4(),
        workspace_id=_WORKSPACE,
        owner_user_id=_OWNER,
        status=ResearchExportJobStatus.READY,
        created_at=_T0,
        bucket="exports",
        object_key="k",
    )
    with (
        patch.object(routes.research_export_service, "get", AsyncMock(return_value=ready)),
        patch.object(routes, "still_entitled", AsyncMock(return_value=False)),
        pytest.raises(HTTPException) as exc,
    ):
        await routes.get_research_export(job_id=ready.job_id, principal=_principal(), db=AsyncMock())
    assert exc.value.status_code == 403
