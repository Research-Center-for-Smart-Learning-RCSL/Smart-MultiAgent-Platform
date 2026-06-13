"""K.3 Pass 1 — trigger → turn wiring (links a + b).

Unit-level coverage of the glue that turns a user message / presence change into
an enqueued agent turn, and of the ``wakeup_agent`` task that runs the turn with
its guards. The full message→reply round trip is the compose-backed K.7 wiring
tier; here we pin the branch logic with fakes (no Postgres/Redis).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import app.workers.tasks.orchestration as orch_task
import contexts.conversation.application.triggers as triggers
import contexts.orchestration.interfaces.facade as facade_mod

# --------------------------------------------------------------------------- #
# evaluate_message_wakeups / evaluate_presence_change
# --------------------------------------------------------------------------- #


def _fake_agent_repo(agent_ids):
    class _Repo:
        def __init__(self, db) -> None:
            self._db = db

        async def list(self, chatroom_id):
            return [SimpleNamespace(agent_id=a) for a in agent_ids]

    return _Repo


@pytest.mark.asyncio
async def test_evaluate_message_wakeups_returns_wake_list(monkeypatch) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    room = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _fake_agent_repo([a1, a2]))

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_message_created(self, *, room_id, sender_is_user, agent_ids):
            captured.update(room_id=room_id, sender_is_user=sender_is_user, agent_ids=list(agent_ids))
            return [a1]  # only a1's every_n trigger fired

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)

    woken = await triggers.evaluate_message_wakeups(object(), chatroom_id=room, sender_is_user=True)

    assert woken == [a1]
    assert captured["room_id"] == room
    assert captured["sender_is_user"] is True
    assert captured["agent_ids"] == [a1, a2]


@pytest.mark.asyncio
async def test_evaluate_message_wakeups_no_agents_skips_facade(monkeypatch) -> None:
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _fake_agent_repo([]))

    class _Boom:
        def __init__(self, db) -> None:
            raise AssertionError("facade must not be built when no agents are bound")

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Boom)

    woken = await triggers.evaluate_message_wakeups(object(), chatroom_id=uuid.uuid4(), sender_is_user=True)
    assert woken == []


@pytest.mark.asyncio
async def test_evaluate_presence_change_forwards_flag(monkeypatch) -> None:
    a1 = uuid.uuid4()
    room = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _fake_agent_repo([a1]))

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_presence_changed(self, *, room_id, agent_ids, has_live_users):
            captured.update(room_id=room_id, agent_ids=list(agent_ids), has_live_users=has_live_users)

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)

    await triggers.evaluate_presence_change(object(), chatroom_id=room, has_live_users=False)
    assert captured == {"room_id": room, "agent_ids": [a1], "has_live_users": False}


# --------------------------------------------------------------------------- #
# wakeup_agent task
# --------------------------------------------------------------------------- #


class _FakeDB:
    async def commit(self) -> None:
        return None


def _patch_task_env(
    monkeypatch,
    *,
    room,
    agent,
    autostop_count=0,
    turn_status="completed",
):
    """Wire the function-local imports in ``wakeup_agent`` to fakes. Returns a
    dict of recorders the test asserts on."""
    rec: dict = {"run_turn": [], "on_agent_message_sent": [], "audit": []}

    @asynccontextmanager
    async def _fake_session():
        yield _FakeDB()

    monkeypatch.setattr(orch_task, "async_session", _fake_session)

    class _ChatroomRepo:
        def __init__(self, db) -> None:
            pass

        async def get(self, rid):
            return room

    monkeypatch.setattr(
        "contexts.conversation.infrastructure.repositories.ChatroomRepository",
        _ChatroomRepo,
    )

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

    monkeypatch.setattr("contexts.agents.interfaces.facade.AgentsFacade", _AgentsFacade)

    async def _get_autostop_count(aid, rid):
        return autostop_count

    monkeypatch.setattr(
        "contexts.orchestration.infrastructure.wakeup_state.get_autostop_count",
        _get_autostop_count,
    )

    class _TurnEngine:
        def __init__(self, db, *, qdrant_url=None, qdrant_api_key=None) -> None:
            pass

        async def run_turn(self, *, agent_id, chatroom_id, trigger):
            rec["run_turn"].append((agent_id, chatroom_id, trigger))
            return SimpleNamespace(status=turn_status, reason=None)

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _TurnEngine)

    class _OrchFacade:
        def __init__(self, db) -> None:
            pass

        async def on_agent_message_sent(self, *, agent_id, room_id):
            rec["on_agent_message_sent"].append((agent_id, room_id))
            return 1

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _OrchFacade)

    async def _emit(db, event):
        rec["audit"].append(event.action)

    monkeypatch.setattr("shared_kernel.audit.emit", _emit)

    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key=None)),
    )

    return rec


def _agent(autostop_rounds=5):
    return SimpleNamespace(
        id=uuid.uuid4(),
        wakeup_config={"triggers": {"silence_minutes": {"autostop_rounds": autostop_rounds}}},
    )


@pytest.mark.asyncio
async def test_wakeup_agent_skips_when_room_gone(monkeypatch) -> None:
    rec = _patch_task_env(monkeypatch, room=None, agent=_agent())
    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()))
    assert out == "skipped:room_gone"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_skips_when_agent_gone(monkeypatch) -> None:
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=uuid.uuid4()), agent=None)
    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()))
    assert out == "skipped:agent_gone"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_skips_when_autostop_tripped(monkeypatch) -> None:
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=uuid.uuid4()),
        agent=_agent(autostop_rounds=3),
        autostop_count=3,
    )
    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()))
    assert out == "skipped:autostop"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_runs_turn_and_counts_round(monkeypatch) -> None:
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=rid), agent=_agent(), turn_status="completed")
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "silence_minutes")

    assert out == "completed"
    assert rec["run_turn"] == [(aid, rid, "silence_minutes")]
    # autostop bumped exactly once, only because the turn completed.
    assert rec["on_agent_message_sent"] == [(aid, rid)]
    assert "wakeup.fired" in rec["audit"]


@pytest.mark.asyncio
async def test_wakeup_agent_skipped_turn_does_not_count_round(monkeypatch) -> None:
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=rid), agent=_agent(), turn_status="skipped")
    out = await orch_task.wakeup_agent({}, str(aid), str(rid))

    assert out == "skipped"
    assert rec["run_turn"] == [(aid, rid, "every_n_messages")]
    # A turn that did not produce a reply must not advance autostop.
    assert rec["on_agent_message_sent"] == []
