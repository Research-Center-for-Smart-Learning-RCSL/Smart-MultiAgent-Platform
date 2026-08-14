"""F-4 — the graphrag_silence_sweep arq cron wrapper.

The pure trigger logic is covered in ``test_graphrag_triggers.py``; this exercises
the sweep's orchestration: enqueue with the stable dedup job id, skip configs that
do not fire, and isolate a per-config failure so one bad config never aborts the
sweep (AC-5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

import app.workers.tasks.graphrag as graphrag_task
import contexts.knowledge.application.graphrag_triggers as trigger_mod
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


def _cfg(minutes: int) -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=None,
        builder_key_group_id=uuid.uuid4(),
        trigger_config={"silence_minutes": minutes},
        last_build_at=None,
        last_build_state=BuildState.IDLE,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
        owner_kind="chatroom",
    )


class _FakeRedis:
    def __init__(self, fail_config_id: uuid.UUID | None = None) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []
        self._fail_config_id = fail_config_id

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        if self._fail_config_id is not None and kwargs.get("config_id") == str(self._fail_config_id):
            raise RuntimeError("enqueue failed for this config")
        self.jobs.append((name, kwargs))


class _FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Session:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeRepo:
    configs: ClassVar[list[GraphRagConfig]] = []

    def __init__(self, db: object) -> None:
        pass

    async def list_silence_trigger_configs(self, *, limit: int, offset: int):
        # One bounded page; the sweep breaks when the page is short.
        return list(self.__class__.configs) if offset == 0 else []


@pytest.mark.asyncio
async def test_silence_sweep_enqueues_fired_configs_and_isolates_failures(monkeypatch) -> None:
    idle = _cfg(minutes=5)  # last activity 6 min ago -> fires, enqueues
    fresh = _cfg(minutes=5)  # last activity 1 min ago -> does not fire
    boom = _cfg(minutes=5)  # fires, but its enqueue raises -> isolated, sweep continues

    now = datetime.now(UTC)
    activity = {
        idle.id: now - timedelta(minutes=6),
        boom.id: now - timedelta(minutes=6),
        fresh.id: now - timedelta(minutes=1),
    }

    class _Clock:
        # F-4: the sweep reads a whole page of timestamps in one batched call.
        async def get_many(self, config_ids):
            return {cid: activity.get(cid) for cid in config_ids}

    _FakeRepo.configs = [boom, idle, fresh]
    db = _FakeDb()
    monkeypatch.setattr(graphrag_task, "get_sessionmaker", lambda: lambda: _Session(db))
    monkeypatch.setattr(graphrag_task, "GraphRagConfigRepository", _FakeRepo)
    monkeypatch.setattr(trigger_mod, "RedisGraphRagSilenceClock", _Clock)

    # boom fires but its enqueue raises: one bad config must not abort the sweep.
    redis = _FakeRedis(fail_config_id=boom.id)
    result = await graphrag_task.graphrag_silence_sweep({"redis": redis})

    # Only the idle config is enqueued, carrying the stable dedup job id.
    assert redis.jobs == [
        (
            "graphrag_build",
            {
                "config_id": str(idle.id),
                "triggered_by": "silence_minutes",
                "_job_id": trigger_mod.graphrag_build_job_id(
                    idle.id, last_build_state=BuildState.IDLE, last_build_at=None
                ),
            },
        )
    ]
    assert result == "fired=1"
    # The failing config was isolated (rollback), not fatal.
    assert db.rollbacks == 1
