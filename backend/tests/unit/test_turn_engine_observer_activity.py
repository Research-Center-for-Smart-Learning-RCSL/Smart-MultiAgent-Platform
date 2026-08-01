"""Turn-engine activity delegation (AC-1, AC-2).

``TestActivityContextDelegation`` exercises the delegating ``_activity_context``
as an unbound method over a stub — proving it forwards to the provider and
returns ``None`` when the room has no activities (the coverage gate every turn
relies on).

``TestActivityBlockReachesNonObserverTurn`` is the agent-visibility follow-up
regression: the call site in ``turn_engine.py`` (``_run_locked``) no longer
gates ``_activity_context`` behind ``is_observer`` — every agent's turn gets
the block now, observer or not; per-row content gating lives entirely in the
provider (``ActivityContextProvider``/``RecentActivityRow.expose_payload_to_agent``,
covered in ``test_activity_context_provider.py``). This drives a full
``_run_locked`` pass for a ``NORMAL``-role binding (mirroring
``test_observer_agents.py``'s ``_wire_observer_engine`` harness, but for the
participant path) and asserts the activity block text actually reaches the
assembled system prompt handed to the model — so a future refactor that
reintroduces or inverts the ``is_observer`` gate fails this test, not just an
unenforced docstring claim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.turn_engine import TurnEngine
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.skills.application.binding_service import BoundSet

_NOW = datetime(2026, 7, 25, tzinfo=UTC)


class TestActivityContextDelegation:
    async def test_delegates_to_provider_and_returns_block(self) -> None:
        stub = SimpleNamespace(_activity_provider=SimpleNamespace())
        stub._activity_provider.query = AsyncMock(return_value="[Recent room activity]\n- x")
        room = uuid.uuid4()

        result = await TurnEngine._activity_context(stub, room)

        assert result == "[Recent room activity]\n- x"
        stub._activity_provider.query.assert_awaited_once_with(chatroom_id=room)

    async def test_returns_none_when_provider_gates_off(self) -> None:
        stub = SimpleNamespace(_activity_provider=SimpleNamespace())
        stub._activity_provider.query = AsyncMock(return_value=None)

        result = await TurnEngine._activity_context(stub, uuid.uuid4())

        assert result is None


class _FakeSavepoint:
    """Stands in for the SQLAlchemy AsyncSessionTransaction returned by
    ``begin_nested()``: an async context manager, not itself awaited."""

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False  # never swallow — matches real SAVEPOINT semantics


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()


class _PublisherSpy:
    """Records every (channel, event, payload) across all constructions."""

    emitted: ClassVar[list[tuple[str, str, dict]]] = []

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event: str, payload: dict) -> None:
        _PublisherSpy.emitted.append((self._channel, event, payload))


def _normal_agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        system_prompt="prompt",
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
        context_mode=SimpleNamespace(value="general"),
        context_token_cap=None,
    )


def _wire_normal_engine(monkeypatch, agent, *, activity_block: str | None):
    """Wire a full ``_run_locked`` pass for a NORMAL-role binding, with fakes at
    every external boundary — the participant-turn analog of
    ``test_observer_agents._wire_observer_engine``. ``activity_block`` is
    handed back verbatim by the stubbed ``_activity_context``, so the caller
    controls exactly what the (real, unconditional) call site has to work with."""
    _PublisherSpy.emitted = []
    monkeypatch.setattr(te, "Publisher", _PublisherSpy)

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

        async def list_agent_tools(self, aid):
            return []

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _BindingRepo:
        def __init__(self, db) -> None:
            pass

        async def role_of(self, *, chatroom_id, agent_id):
            return ChatroomAgentRole.NORMAL

    monkeypatch.setattr(te, "ChatroomAgentRepository", _BindingRepo)

    class _KeysFacade:
        def __init__(self, db) -> None:
            pass

        async def get_key_group(self, kgid):
            return SimpleNamespace(project_id=agent.project_id)

        async def has_carried_provider_in_group(self, kgid, provider):
            return True

    monkeypatch.setattr(te, "KeysFacade", _KeysFacade)

    sent_message = SimpleNamespace(id=uuid.uuid4(), created_at=_NOW)

    class _MessageService:
        def __init__(self, db) -> None:
            pass

        async def send_agent(self, **kw):
            return sent_message

    monkeypatch.setattr(te, "MessageService", _MessageService)

    class _SkillsFacade:
        def __init__(self, db) -> None:
            pass

        async def resolve_bound_set(self, *, agent_id, agent_project_id, enabled_tools):
            return BoundSet(skills=())

        @staticmethod
        def render_index(skills):
            return ""

    monkeypatch.setattr(te, "SkillsFacade", _SkillsFacade)
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: SimpleNamespace(specs=lambda: []))

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._compact_forced_rooms = {}  # type: ignore[attr-defined]

    async def _noop(*a, **k):
        return None

    async def _true(*a, **k):
        return True

    async def _history(
        agent_, chatroom_id, context_limit, provider, model, *, extra_projected_tokens=0, room=None
    ):
        return [
            SimpleNamespace(
                role="user", content="hello", sender_id=uuid.uuid4(), id=uuid.uuid4(), token_count=2
            )
        ]

    async def _labels(agent_, chatroom_id, history):
        return {}, {}

    async def _none(*a, **k):
        return None

    async def _activity_context_stub(*a, **k):
        return activity_block

    async def _no_staging(*a, **k):
        return None, []

    async def _empty_list(*a, **k):
        return []

    async def _pending(agent_, chatroom_id_):
        return None, [], [], set()

    stream_seen: dict = {}

    async def _stream(**kw):
        stream_seen.update(kw)
        return te.ToolLoopOutcome(text="final reply", rounds=1)

    engine._audit = _noop  # type: ignore[attr-defined]
    engine._turn_rate_allowed = _true  # type: ignore[attr-defined]
    engine._assemble_history = _history  # type: ignore[attr-defined]
    engine._participant_labels = _labels  # type: ignore[attr-defined]
    engine._rag_context = _none  # type: ignore[attr-defined]
    engine._graphrag_context = _none  # type: ignore[attr-defined]
    engine._knowmap_context = _none  # type: ignore[attr-defined]
    engine._activity_context = _activity_context_stub  # type: ignore[attr-defined]
    engine._pending_context_and_tools = _pending  # type: ignore[attr-defined]
    engine._builtin_tools = _empty_list  # type: ignore[attr-defined]
    engine._resolve_trigger_attachments = _none  # type: ignore[attr-defined]
    engine._stage_workspace_inputs = _no_staging  # type: ignore[attr-defined]
    engine._model_attachment_blocks = _empty_list  # type: ignore[attr-defined]
    engine._stream_with_tools = _stream  # type: ignore[attr-defined]
    engine._provider_message = (  # type: ignore[attr-defined]
        lambda hm, aid, an, un, attachment_blocks=None: {"role": "user", "content": hm.content}
    )
    engine._dispatch_agent_message_signal = _noop  # type: ignore[attr-defined]
    engine._dispatch_agent_reply_wakeups = _noop  # type: ignore[attr-defined]
    engine._persist_artifacts = _noop  # type: ignore[attr-defined]

    return engine, stream_seen


class TestActivityBlockReachesNonObserverTurn:
    async def test_normal_role_turn_carries_the_activity_block(self, monkeypatch) -> None:
        agent = _normal_agent()
        engine, stream_seen = _wire_normal_engine(
            monkeypatch, agent, activity_block="[Recent room activity]\n- sentinel-row"
        )

        result = await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=uuid.uuid4(),
            trigger="every_n_messages",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            trigger_message_id=None,
        )

        assert result.status == "completed"
        # The real regression pin: a NORMAL (non-observer) turn's assembled
        # system prompt contains the activity block. Before the is_observer
        # gate was removed from turn_engine.py, this would be absent here.
        assert "[Recent room activity]\n- sentinel-row" in stream_seen["system_text"]

    async def test_normal_role_turn_with_no_activity_carries_no_block(self, monkeypatch) -> None:
        """Coverage-gate symmetry: when the provider has nothing to say (the
        room has no activities), the call site still runs unconditionally but
        contributes nothing — matches ActivityContextProvider's own None-on-
        empty behavior (test_activity_context_provider.py)."""
        agent = _normal_agent()
        engine, stream_seen = _wire_normal_engine(monkeypatch, agent, activity_block=None)

        result = await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=uuid.uuid4(),
            trigger="every_n_messages",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            trigger_message_id=None,
        )

        assert result.status == "completed"
        assert "[Recent room activity]" not in stream_seen["system_text"]
