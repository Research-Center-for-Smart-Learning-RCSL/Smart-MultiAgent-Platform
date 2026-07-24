"""G.10 — a room-level POST /compact folds once per compact-mode agent (R9.09).

`context_mode` is an Agent field and a summary applies only to its producing
agent's model-facing view, so the room-level action cannot stop at the first
bound agent: that would compact one arbitrary agent's view and leave every
other agent's untouched.
"""

from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace

import pytest

import app.workers.tasks.conversation as task_mod


def _agent(mode: str):
    return SimpleNamespace(id=uuid.uuid4(), context_mode=SimpleNamespace(value=mode))


def _wire(monkeypatch, *, room_exists=True, agents=(), fail_for=()):
    """Patch every collaborator `compact_chatroom` imports at call time.

    Returns the list that records `(agent_id, chatroom_id)` per compaction pass.
    """
    import app.config.settings as settings_mod
    import contexts.agents.application.runtime.turn_engine as te
    import contexts.agents.interfaces.facade as agents_facade_mod
    import contexts.conversation.infrastructure.repositories as repos
    import shared_kernel.db.session as session_mod

    passes: list[tuple] = []
    by_id = {a.id: a for a in agents}

    @contextlib.asynccontextmanager
    async def _session():
        yield object()

    monkeypatch.setattr(session_mod, "async_session", _session)

    class _RoomRepo:
        def __init__(self, _db) -> None:
            pass

        async def get(self, _rid):
            return object() if room_exists else None

    class _BindingRepo:
        def __init__(self, _db) -> None:
            pass

        async def list(self, _rid):
            return [SimpleNamespace(agent_id=a.id) for a in agents]

    class _AgentsFacade:
        def __init__(self, _db) -> None:
            pass

        async def get_agent(self, agent_id):
            return by_id.get(agent_id)

    class _Engine:
        def __init__(self, _db, **_kw) -> None:
            pass

        async def run_compaction(self, *, agent_id, chatroom_id):
            passes.append((agent_id, chatroom_id))
            return agent_id not in fail_for

    monkeypatch.setattr(repos, "ChatroomRepository", _RoomRepo)
    monkeypatch.setattr(repos, "ChatroomAgentRepository", _BindingRepo)
    monkeypatch.setattr(agents_facade_mod, "AgentsFacade", _AgentsFacade)
    monkeypatch.setattr(te, "TurnEngine", _Engine)
    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant=SimpleNamespace(url="", api_key=""),
            knowledge=SimpleNamespace(bge_reranker_url=""),
        ),
    )
    return passes


@pytest.mark.asyncio
async def test_compact_runs_a_pass_for_every_compact_mode_agent(monkeypatch) -> None:
    a, b = _agent("compact"), _agent("compact")
    passes = _wire(monkeypatch, agents=(a, b))
    room = uuid.uuid4()

    assert await task_mod.compact_chatroom({}, str(room)) == "completed"

    assert [p[0] for p in passes] == [a.id, b.id]


@pytest.mark.asyncio
async def test_compact_skips_general_mode_agents(monkeypatch) -> None:
    # A `general` agent must never be forced to fold its own history — R9.09
    # says it sends the entire chat history.
    compactor, general = _agent("compact"), _agent("general")
    passes = _wire(monkeypatch, agents=(general, compactor))

    assert await task_mod.compact_chatroom({}, str(uuid.uuid4())) == "completed"

    assert [p[0] for p in passes] == [compactor.id]


@pytest.mark.asyncio
async def test_compact_reports_a_room_with_no_compact_mode_agent(monkeypatch) -> None:
    # A room-level action that cannot do anything must report the no-op rather
    # than look like a successful compaction.
    passes = _wire(monkeypatch, agents=(_agent("general"),))

    assert await task_mod.compact_chatroom({}, str(uuid.uuid4())) == "skipped:no_compact_agents"

    assert passes == []


@pytest.mark.asyncio
async def test_one_agents_failure_does_not_deny_the_others_their_fold(monkeypatch) -> None:
    a, b = _agent("compact"), _agent("compact")
    passes = _wire(monkeypatch, agents=(a, b), fail_for=(a.id,))

    assert await task_mod.compact_chatroom({}, str(uuid.uuid4())) == "completed"

    assert [p[0] for p in passes] == [a.id, b.id]


@pytest.mark.asyncio
async def test_compact_reports_a_room_with_no_bindings(monkeypatch) -> None:
    _wire(monkeypatch, agents=())

    assert await task_mod.compact_chatroom({}, str(uuid.uuid4())) == "skipped:no_agents"
