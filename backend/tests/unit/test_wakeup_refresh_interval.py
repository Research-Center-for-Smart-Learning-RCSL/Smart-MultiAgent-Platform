from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contexts.orchestration.application.wakeup_service import WakeupService


def _agent(*, last_refreshed_at):
    return SimpleNamespace(
        id=uuid.uuid4(),
        deleted_at=None,
        version=1,
        wakeup_config={
            "triggers": {"every_n_messages": {"enabled": True, "n": 9}},
            "refresh_every_hours": 24,
        },
        wakeup_authored_snapshot={
            "triggers": {"every_n_messages": {"enabled": True, "n": 3}},
            "refresh_every_hours": 24,
        },
        wakeup_last_refreshed_at=last_refreshed_at,
    )


async def _service(monkeypatch, agent):
    patched = []

    class _Agents:
        async def get_agent(self, _agent_id):
            return agent

        async def patch_agent(self, **kwargs):
            patched.append(kwargs)
            return agent

    async def _emit(_db, _event):
        return None

    svc = WakeupService.__new__(WakeupService)
    svc._db = None
    svc._agents_facade = _Agents()
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.audit.emit",
        _emit,
    )
    return svc, patched


async def test_refresh_is_skipped_inside_the_configured_window(monkeypatch) -> None:
    agent = _agent(last_refreshed_at=datetime.now(UTC) - timedelta(hours=1))
    svc, patched = await _service(monkeypatch, agent)

    assert not await svc.refresh_wakeup_config(agent.id)
    assert patched == []


async def test_refresh_runs_outside_the_configured_window(monkeypatch) -> None:
    agent = _agent(last_refreshed_at=datetime.now(UTC) - timedelta(hours=25))
    svc, patched = await _service(monkeypatch, agent)

    assert await svc.refresh_wakeup_config(agent.id)
    assert len(patched) == 1


async def test_never_refreshed_agent_is_immediately_eligible(monkeypatch) -> None:
    agent = _agent(last_refreshed_at=None)
    svc, patched = await _service(monkeypatch, agent)

    assert await svc.refresh_wakeup_config(agent.id)
    assert len(patched) == 1
