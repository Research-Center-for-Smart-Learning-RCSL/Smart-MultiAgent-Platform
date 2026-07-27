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


def _fake_agent_repo(agent_ids, roles=None):
    from contexts.conversation.domain.models import ChatroomAgentRole

    role_by_id = roles or {}

    class _Repo:
        def __init__(self, db) -> None:
            self._db = db

        async def list(self, chatroom_id):
            return [
                SimpleNamespace(agent_id=a, role=role_by_id.get(a, ChatroomAgentRole.NORMAL))
                for a in agent_ids
            ]

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

        async def on_message_created(
            self, *, room_id, sender_is_user, sender_agent_id=None, agent_ids, observer_agent_ids=frozenset()
        ):
            captured.update(
                room_id=room_id,
                sender_is_user=sender_is_user,
                sender_agent_id=sender_agent_id,
                agent_ids=list(agent_ids),
                observer_agent_ids=set(observer_agent_ids),
            )
            return [a1]  # only a1's every_n trigger fired

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)

    woken = await triggers.evaluate_message_wakeups(object(), chatroom_id=room, sender_is_user=True)

    assert woken == [a1]
    assert captured["room_id"] == room
    assert captured["sender_is_user"] is True
    assert captured["agent_ids"] == [a1, a2]
    assert captured["observer_agent_ids"] == set()


@pytest.mark.asyncio
async def test_evaluate_message_wakeups_passes_observer_ids(monkeypatch) -> None:
    """O-2 (F-2): the conversation edge owns roles — observer bindings must be
    identified to the orchestration facade so the presence gate can exempt them."""
    from contexts.conversation.domain.models import ChatroomAgentRole

    normal, observer = uuid.uuid4(), uuid.uuid4()
    room = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(
        triggers,
        "ChatroomAgentRepository",
        _fake_agent_repo([normal, observer], roles={observer: ChatroomAgentRole.OBSERVER}),
    )

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_message_created(self, *, agent_ids, observer_agent_ids=frozenset(), **kw):
            captured.update(agent_ids=list(agent_ids), observer_agent_ids=set(observer_agent_ids))
            return []

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)

    await triggers.evaluate_message_wakeups(object(), chatroom_id=room, sender_is_user=True)

    assert captured["agent_ids"] == [normal, observer]
    assert captured["observer_agent_ids"] == {observer}


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
async def test_filter_mentioned_bound_agents_keeps_only_bound(monkeypatch) -> None:
    a1, a2, unbound = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _fake_agent_repo([a1, a2]))

    out = await triggers.filter_mentioned_bound_agents(
        object(),
        chatroom_id=uuid.uuid4(),
        # a2 listed twice + an agent that is not bound to the room.
        mention_agent_ids=[a2, unbound, a2, a1],
    )
    # Unbound dropped, duplicates collapsed, mention order preserved.
    assert out == [a2, a1]


@pytest.mark.asyncio
async def test_filter_mentioned_bound_agents_drops_observers(monkeypatch) -> None:
    """R28.04 — a smuggled observer id gets the same silent drop as an unbound
    id, so mentions cannot be used as an observer-existence oracle."""
    from contexts.conversation.domain.models import ChatroomAgentRole

    normal, observer = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        triggers,
        "ChatroomAgentRepository",
        _fake_agent_repo([normal, observer], roles={observer: ChatroomAgentRole.OBSERVER}),
    )

    out = await triggers.filter_mentioned_bound_agents(
        object(),
        chatroom_id=uuid.uuid4(),
        mention_agent_ids=[observer, normal],
    )
    assert out == [normal]


@pytest.mark.asyncio
async def test_filter_mentioned_bound_agents_empty_input_skips_repo(monkeypatch) -> None:
    class _Boom:
        def __init__(self, db) -> None:
            raise AssertionError("repo must not be built for an empty mention list")

    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _Boom)
    out = await triggers.filter_mentioned_bound_agents(
        object(), chatroom_id=uuid.uuid4(), mention_agent_ids=[]
    )
    assert out == []


@pytest.mark.asyncio
async def test_evaluate_presence_change_forwards_to_on_users_present(monkeypatch) -> None:
    a1 = uuid.uuid4()
    room = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _fake_agent_repo([a1]))

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_users_present(self, *, room_id, agent_ids):
            captured.update(room_id=room_id, agent_ids=list(agent_ids))

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)

    await triggers.evaluate_presence_change(object(), chatroom_id=room)
    assert captured == {"room_id": room, "agent_ids": [a1]}


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
    role=None,
):
    """Wire the function-local imports in ``wakeup_agent`` to fakes. Returns a
    dict of recorders the test asserts on."""
    from contexts.conversation.domain.models import ChatroomAgentRole

    rec: dict = {"run_turn": [], "on_agent_message_sent": [], "audit": []}
    binding_role = role or ChatroomAgentRole.NORMAL

    class _BindingRepo:
        def __init__(self, db) -> None:
            pass

        async def role_of(self, *, chatroom_id, agent_id):
            return binding_role

    monkeypatch.setattr(
        "contexts.conversation.infrastructure.repositories.ChatroomAgentRepository",
        _BindingRepo,
    )

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
        def __init__(self, db, *, qdrant_url=None, qdrant_api_key=None, bge_reranker_url=None) -> None:
            pass

        async def run_turn(self, *, agent_id, chatroom_id, trigger, trigger_message_id=None):
            rec["run_turn"].append((agent_id, chatroom_id, trigger, trigger_message_id))
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
        lambda: SimpleNamespace(
            qdrant=SimpleNamespace(url="http://q", api_key=None),
            knowledge=SimpleNamespace(bge_reranker_url="http://bge:80"),
        ),
    )

    return rec


def _agent(autostop_rounds=5, *, observer_autostop_rounds=50):
    return SimpleNamespace(
        id=uuid.uuid4(),
        wakeup_config={
            "triggers": {
                "silence_minutes": {
                    "autostop_rounds": autostop_rounds,
                    "observer_autostop_rounds": observer_autostop_rounds,
                }
            }
        },
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
async def test_wakeup_agent_mention_agent_gone_emits_notice(monkeypatch) -> None:
    # An @mention to a now-unavailable agent (soft-deleted but still bound)
    # returns at the worker guard before the engine runs — surface a notice so
    # the explicit summon is not silently dropped.
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=uuid.uuid4()), agent=None)
    emitted: list[tuple[uuid.UUID, str]] = []

    async def _fake_emit(room_id, agent_id, reason) -> None:
        emitted.append((agent_id, reason))

    monkeypatch.setattr("contexts.conversation.interfaces.emit_agent_finished_error", _fake_emit)

    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()), "mention")
    assert out == "skipped:agent_gone"
    assert rec["run_turn"] == []
    # Keyed on `error` (not `reason`) inside emit_agent_finished_error: the client
    # only surfaces agent.finished under `error`.
    assert emitted[0][1] == "agent_gone"


@pytest.mark.asyncio
async def test_wakeup_agent_release_agent_gone_emits_notice(monkeypatch) -> None:
    # R28.07 — a creator's explicit release-wake is the same shape of explicit
    # call as a mention; it must not silently no-op when the target agent was
    # removed between the release commit and this job running.
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=uuid.uuid4()), agent=None)
    emitted: list[tuple[uuid.UUID, str]] = []

    async def _fake_emit(room_id, agent_id, reason) -> None:
        emitted.append((agent_id, reason))

    monkeypatch.setattr("contexts.conversation.interfaces.emit_agent_finished_error", _fake_emit)

    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()), "release")
    assert out == "skipped:agent_gone"
    assert rec["run_turn"] == []
    assert emitted[0][1] == "agent_gone"


@pytest.mark.asyncio
async def test_wakeup_agent_autonomous_agent_gone_is_silent(monkeypatch) -> None:
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=uuid.uuid4()), agent=None)

    async def _boom(*_a, **_k) -> None:
        raise AssertionError("autonomous agent_gone must not emit a notice")

    monkeypatch.setattr("contexts.conversation.interfaces.emit_agent_finished_error", _boom)

    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()), "every_n_messages")
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
async def test_wakeup_agent_zero_autostop_uses_the_parsed_default(monkeypatch) -> None:
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=uuid.uuid4()),
        agent=_agent(autostop_rounds=0),
        autostop_count=5,
    )

    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()))

    assert out == "skipped:autostop"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_zero_observer_autostop_uses_the_observer_default(monkeypatch) -> None:
    from contexts.conversation.domain.models import ChatroomAgentRole

    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=uuid.uuid4()),
        agent=_agent(observer_autostop_rounds=0),
        autostop_count=50,
        role=ChatroomAgentRole.OBSERVER,
    )

    out = await orch_task.wakeup_agent({}, str(uuid.uuid4()), str(uuid.uuid4()))

    assert out == "skipped:autostop"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_observer_survives_normal_autostop_cap(monkeypatch) -> None:
    """O-3 (P-1): observer bindings use observer_autostop_rounds (default 50),
    not the normal autostop_rounds, so a long agent-only exchange keeps being
    observed past the normal cap."""
    from contexts.conversation.domain.models import ChatroomAgentRole

    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=rid),
        agent=_agent(autostop_rounds=3),
        autostop_count=5,
        turn_status="completed",
        role=ChatroomAgentRole.OBSERVER,
    )
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "every_n_messages")

    assert out == "completed"
    assert rec["run_turn"] == [(aid, rid, "every_n_messages", None)]


@pytest.mark.asyncio
async def test_wakeup_agent_observer_skips_at_observer_cap(monkeypatch) -> None:
    from contexts.conversation.domain.models import ChatroomAgentRole

    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=rid),
        agent=_agent(autostop_rounds=3),
        autostop_count=50,
        role=ChatroomAgentRole.OBSERVER,
    )
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "every_n_messages")

    assert out == "skipped:autostop"
    assert rec["run_turn"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_runs_turn_and_counts_round(monkeypatch) -> None:
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=rid), agent=_agent(), turn_status="completed")
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "silence_minutes")

    assert out == "completed"
    # silence_minutes has no specific triggering message — falls back to None.
    assert rec["run_turn"] == [(aid, rid, "silence_minutes", None)]
    # autostop bumped exactly once, only because the turn completed.
    assert rec["on_agent_message_sent"] == [(aid, rid)]
    assert "wakeup.fired" in rec["audit"]


@pytest.mark.asyncio
async def test_wakeup_agent_mention_bypasses_autostop(monkeypatch) -> None:
    # A user @mention is an explicit call and must run even after autostop has
    # tripped for autonomous rounds.
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=rid),
        agent=_agent(autostop_rounds=3),
        autostop_count=5,
        turn_status="completed",
    )
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "mention")

    assert out == "completed"
    assert rec["run_turn"] == [(aid, rid, "mention", None)]


@pytest.mark.asyncio
async def test_wakeup_agent_release_bypasses_autostop(monkeypatch) -> None:
    # R28.07 — a creator-released observation with wake=true is an explicit
    # call, same shape as a mention: it must run even after autostop tripped.
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(
        monkeypatch,
        room=SimpleNamespace(id=rid),
        agent=_agent(autostop_rounds=3),
        autostop_count=5,
        turn_status="completed",
    )
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "release")

    assert out == "completed"
    assert rec["run_turn"] == [(aid, rid, "release", None)]


@pytest.mark.asyncio
async def test_wakeup_agent_skipped_turn_does_not_count_round(monkeypatch) -> None:
    aid, rid = uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=rid), agent=_agent(), turn_status="skipped")
    out = await orch_task.wakeup_agent({}, str(aid), str(rid))

    assert out == "skipped"
    assert rec["run_turn"] == [(aid, rid, "every_n_messages", None)]
    # A turn that did not produce a reply must not advance autostop.
    assert rec["on_agent_message_sent"] == []


@pytest.mark.asyncio
async def test_wakeup_agent_forwards_trigger_message_id(monkeypatch) -> None:
    aid, rid, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rec = _patch_task_env(monkeypatch, room=SimpleNamespace(id=rid), agent=_agent(), turn_status="completed")
    out = await orch_task.wakeup_agent({}, str(aid), str(rid), "every_n_messages", str(mid))

    assert out == "completed"
    assert rec["run_turn"] == [(aid, rid, "every_n_messages", mid)]
