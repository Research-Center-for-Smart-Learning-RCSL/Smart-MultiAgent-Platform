from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contexts.orchestration.application.wakeup_service import WakeupService


def _agent(*, last_refreshed_at, refresh_every_hours: int = 24):
    return SimpleNamespace(
        id=uuid.uuid4(),
        deleted_at=None,
        version=1,
        wakeup_config={
            "triggers": {"every_n_messages": {"enabled": True, "n": 9}},
            "refresh_every_hours": refresh_every_hours,
        },
        wakeup_authored_snapshot={
            "triggers": {"every_n_messages": {"enabled": True, "n": 3}},
            "refresh_every_hours": refresh_every_hours,
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


async def test_extreme_refresh_interval_does_not_overflow(monkeypatch) -> None:
    agent = _agent(
        last_refreshed_at=datetime.now(UTC) - timedelta(hours=1),
        refresh_every_hours=24_000_000_000,
    )
    svc, patched = await _service(monkeypatch, agent)

    assert not await svc.refresh_wakeup_config(agent.id)
    assert patched == []


async def test_refresh_runs_outside_the_configured_window(monkeypatch) -> None:
    agent = _agent(last_refreshed_at=datetime.now(UTC) - timedelta(hours=25))
    svc, patched = await _service(monkeypatch, agent)

    assert await svc.refresh_wakeup_config(agent.id)
    assert len(patched) == 1
    assert patched[0]["draft"].wakeup_last_refreshed_at is not None


async def test_refresh_asks_for_a_replacing_write_not_a_merge(monkeypatch) -> None:
    """R15.09 is a restore. An additive write would keep drifted keys the authored
    snapshot never had, so `current == authored` would never hold again and the
    sweep would refresh and audit the same agent every interval, forever."""
    agent = _agent(last_refreshed_at=datetime.now(UTC) - timedelta(hours=25))
    svc, patched = await _service(monkeypatch, agent)

    assert await svc.refresh_wakeup_config(agent.id)
    assert patched[0]["draft"].replace_wakeup_config is True


async def test_refresh_retry_after_a_version_conflict_still_replaces(monkeypatch) -> None:
    """Version conflicts on this row are routine — wake-up workers and the hourly
    sweep race on the same agent — so a retry that merged instead of replacing would
    reintroduce the never-converging refresh on exactly the common path."""
    from contexts.agents.interfaces.facade import AgentVersionMismatch

    agent = _agent(last_refreshed_at=datetime.now(UTC) - timedelta(hours=25))
    patched: list[dict] = []

    class _Agents:
        async def get_agent(self, _agent_id):
            return agent

        async def patch_agent(self, **kwargs):
            patched.append(kwargs)
            if len(patched) == 1:
                raise AgentVersionMismatch("conflict")
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

    assert await svc.refresh_wakeup_config(agent.id)
    assert len(patched) == 2
    assert all(call["draft"].replace_wakeup_config is True for call in patched)


async def test_never_refreshed_agent_is_immediately_eligible(monkeypatch) -> None:
    agent = _agent(last_refreshed_at=None)
    svc, patched = await _service(monkeypatch, agent)

    assert await svc.refresh_wakeup_config(agent.id)
    assert len(patched) == 1
    assert patched[0]["draft"].wakeup_last_refreshed_at is not None
