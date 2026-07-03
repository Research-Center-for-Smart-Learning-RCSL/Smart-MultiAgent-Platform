"""SRS §28 Observer Agents — creator access rule, release semantics, and the
turn-engine leak pin.

The load-bearing guarantee (R28.01/R28.03) is structural: observer output
never becomes a `messages` row and never touches the room WS channel. The
turn-engine test here spies every Publisher construction and asserts zero
room-channel traffic across a full observer turn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

import contexts.agents.application.runtime.turn_engine as te
import contexts.conversation.application.observation_service as obs_svc
from contexts.conversation.application.access import (
    RoomAccess,
    ensure_room_creator,
    is_room_creator,
)
from contexts.conversation.domain.errors import (
    InvalidReleaseTarget,
    NotRoomCreator,
    ObservationAlreadyReleased,
    ObservationNotFound,
)
from contexts.conversation.domain.models import (
    AgentObservation,
    ChatroomAgentRole,
    SenderType,
)
from contexts.conversation.infrastructure.repositories.observation_repo import ObservationRepository
from shared_kernel.auth.permissions import Principal, Role

# --------------------------------------------------------------------------- #
# is_room_creator / ensure_room_creator (R28.02)
# --------------------------------------------------------------------------- #


def _principal(user_id=None, *, is_admin=False):
    return Principal(user_id=user_id or uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _access(*, created_by=None, roles=frozenset(), is_guest=False):
    room = SimpleNamespace(created_by_user_id=created_by)
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


def test_creator_match_allows() -> None:
    uid = uuid.uuid4()
    assert is_room_creator(_access(created_by=uid), principal=_principal(uid)) is True


def test_non_creator_member_denied() -> None:
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_MEMBER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_moderator_denied_when_creator_exists() -> None:
    # A live creator makes observer surfaces exclusive to them — a project
    # owner does NOT inherit access.
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_null_creator_falls_back_to_moderator() -> None:
    access = _access(created_by=None, roles=frozenset({Role.PROJECT_OWNER}))
    assert is_room_creator(access, principal=_principal()) is True


def test_null_creator_plain_member_denied() -> None:
    access = _access(created_by=None, roles=frozenset({Role.PROJECT_MEMBER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_admin_bypasses() -> None:
    access = _access(created_by=uuid.uuid4())
    assert is_room_creator(access, principal=_principal(is_admin=True)) is True


def test_pure_guest_always_denied() -> None:
    # Guests satisfy ensure_can_read when guest links are on — the explicit
    # guest branch is what keeps them out of observer surfaces.
    access = _access(created_by=None, roles=frozenset(), is_guest=True)
    assert is_room_creator(access, principal=_principal()) is False


def test_guest_who_is_also_creator_allowed() -> None:
    # A registered user can be both a guest row and the creator (edge case);
    # roles being non-empty routes past the pure-guest branch.
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER}), is_guest=True)
    assert is_room_creator(access, principal=_principal(uid)) is True


def test_ensure_room_creator_raises() -> None:
    with pytest.raises(NotRoomCreator):
        ensure_room_creator(_access(created_by=uuid.uuid4()), principal=_principal())


# --------------------------------------------------------------------------- #
# ObservationService.release (R28.06/R28.07/R28.08/R28.11)
# --------------------------------------------------------------------------- #


def _observation(chatroom_id, agent_id, *, released=False):
    return AgentObservation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content_md="the analysis",
        trigger="every_n_messages",
        released_at=None if not released else object(),  # only truthiness is read
    )


class _FakeObsRepo:
    def __init__(self, observation, *, cas_wins=True):
        self.observation = observation
        self.cas_wins = cas_wins
        self.released_with: dict | None = None
        self.message_target: dict | None = None

    async def get(self, *, chatroom_id, observation_id):
        o = self.observation
        if o is None or o.id != observation_id or o.chatroom_id != chatroom_id:
            return None
        return o

    async def mark_released(self, *, chatroom_id, observation_id, released_by_user_id, release_target):
        if not self.cas_wins:
            return False
        self.released_with = {
            "released_by_user_id": released_by_user_id,
            "release_target": release_target,
        }
        return True

    async def mark_release_message(self, *, chatroom_id, observation_id, release_target):
        self.message_target = release_target


class _FakeBindings:
    def __init__(self, normal=(), observer=()):
        self._rows = [SimpleNamespace(agent_id=a, role=ChatroomAgentRole.NORMAL) for a in normal] + [
            SimpleNamespace(agent_id=a, role=ChatroomAgentRole.OBSERVER) for a in observer
        ]

    async def list(self, chatroom_id):
        return self._rows


class _FakeRooms:
    def __init__(self, *, disclose=True, created_by=None):
        self._room = SimpleNamespace(disclose_observers=disclose, created_by_user_id=created_by)

    async def get(self, chatroom_id):
        return self._room


class _FakeMessages:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, *, chatroom_id, sender_type, sender_id, content_md, metadata=None):
        self.created.append(
            {
                "sender_type": sender_type,
                "sender_id": sender_id,
                "content_md": content_md,
                "metadata": metadata or {},
            }
        )
        return SimpleNamespace(
            id=uuid.uuid4(),
            chatroom_id=chatroom_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content_md=content_md,
            metadata=metadata or {},
            created_at=None,
        )


def _wire_service(
    monkeypatch,
    *,
    observation,
    normal_agents=(),
    observer_agents=(),
    disclose=True,
    cas_wins=True,
):
    room_id = observation.chatroom_id if observation else uuid.uuid4()
    obs_repo = _FakeObsRepo(observation, cas_wins=cas_wins)
    bindings = _FakeBindings(normal=normal_agents, observer=observer_agents)
    rooms = _FakeRooms(disclose=disclose)
    messages = _FakeMessages()
    audits: list = []
    pushes: list = []

    monkeypatch.setattr(obs_svc, "ObservationRepository", lambda db: obs_repo)
    monkeypatch.setattr(obs_svc, "ChatroomAgentRepository", lambda db: bindings)
    monkeypatch.setattr(obs_svc, "ChatroomRepository", lambda db: rooms)
    monkeypatch.setattr(obs_svc, "MessageRepository", lambda db: messages)

    async def _emit(db, event):
        audits.append(event)

    monkeypatch.setattr(obs_svc.audit, "emit", _emit)

    async def _push(agent_id, note):
        pushes.append((agent_id, note))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)

    service = obs_svc.ObservationService(object())
    return service, room_id, obs_repo, messages, audits, pushes


@pytest.mark.asyncio
async def test_release_to_room_creates_system_message(monkeypatch) -> None:
    observer_id = uuid.uuid4()
    obs = _observation(uuid.uuid4(), observer_id)
    service, room_id, obs_repo, messages, audits, pushes = _wire_service(
        monkeypatch, observation=obs, disclose=True
    )

    result = await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
    )

    assert len(messages.created) == 1
    created = messages.created[0]
    assert created["sender_type"] is SenderType.SYSTEM
    assert created["sender_id"] is None
    assert created["content_md"] == "the analysis"
    assert created["metadata"]["type"] == "released_observation"
    # Disclosure on → observer identity travels (R28.09).
    assert created["metadata"]["observer_agent_id"] == str(observer_id)
    assert obs_repo.message_target is not None
    assert obs_repo.message_target["kind"] == "room"
    assert "message_id" in obs_repo.message_target
    assert result.message is not None
    assert pushes == []


@pytest.mark.asyncio
async def test_release_to_room_undisclosed_hides_observer_identity(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, disclose=False
    )

    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
    )

    assert "observer_agent_id" not in messages.created[0]["metadata"]


@pytest.mark.asyncio
async def test_release_to_agents_defers_push_and_resolves_content(monkeypatch) -> None:
    """O-1 (F-1): release() must NOT touch Redis pre-commit — the push happens
    post-commit in _dispatch_release. The service resolves the override into
    ReleaseResult.content so the dispatcher can build the note."""
    target = uuid.uuid4()
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, pushes = _wire_service(
        monkeypatch, observation=obs, normal_agents=(target,)
    )

    result = await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=False,
        target_agent_ids=[target],
        wake=True,
        content_override="edited analysis",
    )

    # R28.07: never a room message; R28.08: no delivery before the commit.
    assert messages.created == []
    assert pushes == []
    assert result.content == "edited analysis"
    assert result.wake is True
    assert result.target_agent_ids == (target,)


def test_release_in_rejects_whitespace_only_override() -> None:
    """O-9 (P-9): a content_override that is empty after strip is a 422, and a
    padded override is stored stripped."""
    from pydantic import ValidationError

    from app.api.v1.observations import ReleaseIn

    with pytest.raises(ValidationError):
        ReleaseIn(target="room", content_override="   ")
    assert ReleaseIn(target="room", content_override=" x ").content_override == "x"


@pytest.mark.asyncio
async def test_dispatch_release_pushes_per_target_best_effort(monkeypatch) -> None:
    """O-1 (F-1): the post-commit dispatcher pushes once per target with the
    resolved content; one target's Redis failure neither raises nor blocks the
    remaining targets (mirrors the room path's best-effort discipline)."""
    from app.api.v1 import observations as obs_api

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pushes: list = []

    async def _push(agent_id, note):
        if agent_id == b:
            raise RuntimeError("redis down")
        pushes.append((agent_id, note))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)

    result = obs_svc.ReleaseResult(
        observation=SimpleNamespace(id=uuid.uuid4(), release_target={"kind": "agents"}),
        message=None,
        target_agent_ids=(a, b, c),
        wake=False,
        content="the analysis",
    )

    class _Svc:
        async def recipient_user_id(self, chatroom_id):
            return None

    room_id = uuid.uuid4()
    await obs_api._dispatch_release(object(), chatroom_id=room_id, service=_Svc(), result=result)

    assert [agent for agent, _ in pushes] == [a, c]
    for _, note in pushes:
        assert note["kind"] == "released_observation"
        assert note["chatroom_id"] == str(room_id)
        assert note["content"] == "the analysis"


@pytest.mark.asyncio
async def test_release_rejects_observer_and_unbound_targets(monkeypatch) -> None:
    normal, observer, unbound = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    obs = _observation(uuid.uuid4(), observer)
    service, room_id, *_rest = _wire_service(
        monkeypatch, observation=obs, normal_agents=(normal,), observer_agents=(observer,)
    )

    for bad in (observer, unbound):
        with pytest.raises(InvalidReleaseTarget):
            await service.release(
                chatroom_id=room_id,
                observation_id=obs.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                target_room=False,
                target_agent_ids=[normal, bad],
            )


@pytest.mark.asyncio
async def test_release_requires_targets_for_agents(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, *_rest = _wire_service(monkeypatch, observation=obs)
    with pytest.raises(InvalidReleaseTarget):
        await service.release(
            chatroom_id=room_id,
            observation_id=obs.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=False,
            target_agent_ids=[],
        )


@pytest.mark.asyncio
async def test_double_release_loses_cas(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, cas_wins=False
    )
    with pytest.raises(ObservationAlreadyReleased):
        await service.release(
            chatroom_id=room_id,
            observation_id=obs.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=True,
        )
    # The CAS loser must not have inserted a message (R28.08).
    assert messages.created == []


@pytest.mark.asyncio
async def test_release_unknown_observation_404(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, *_rest = _wire_service(monkeypatch, observation=obs)
    with pytest.raises(ObservationNotFound):
        await service.release(
            chatroom_id=room_id,
            observation_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=True,
        )


@pytest.mark.asyncio
async def test_release_audit_never_contains_content(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, _messages, audits, _pushes = _wire_service(monkeypatch, observation=obs)
    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
        content_override="SECRET-OVERRIDE",
    )
    released = [e for e in audits if e.action == "observation.released"]
    assert len(released) == 1
    flat = repr(released[0].metadata)
    assert "the analysis" not in flat
    assert "SECRET-OVERRIDE" not in flat
    assert released[0].metadata["content_overridden"] is True


# --------------------------------------------------------------------------- #
# Turn engine — observer leak pin (R28.01/R28.03/R28.05/R28.13)
# --------------------------------------------------------------------------- #


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


def _observer_agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        system_prompt="prompt",
        prompt_strategy=SimpleNamespace(value="full"),
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
    )


def _wire_observer_engine(monkeypatch, agent, *, creator_id):
    """Wire a full `_run_locked` pass for an observer-role binding, with fakes
    at every external boundary and a global Publisher spy."""
    _PublisherSpy.emitted = []
    monkeypatch.setattr(te, "Publisher", _PublisherSpy)

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _BindingRepo:
        def __init__(self, db) -> None:
            pass

        async def role_of(self, *, chatroom_id, agent_id):
            return ChatroomAgentRole.OBSERVER

    monkeypatch.setattr(te, "ChatroomAgentRepository", _BindingRepo)

    class _KeyGroupRepo:
        def __init__(self, db) -> None:
            pass

        async def get_active(self, kgid):
            return SimpleNamespace(project_id=agent.project_id)

    monkeypatch.setattr(te, "KeyGroupRepository", _KeyGroupRepo)

    class _BoomMessageService:
        def __init__(self, db) -> None:
            raise AssertionError("observer turns must never construct MessageService")

    monkeypatch.setattr(te, "MessageService", _BoomMessageService)

    recorded: dict = {}

    class _ObsService:
        def __init__(self, db) -> None:
            pass

        async def record(self, **kw):
            recorded.update(kw)
            return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        async def recipient_user_id(self, chatroom_id):
            return creator_id

    monkeypatch.setattr(te, "ObservationService", _ObsService)
    monkeypatch.setattr(te, "assemble", lambda sp, **k: SimpleNamespace(text="SYS"))
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: SimpleNamespace())

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._compact_forced_rooms = set()  # type: ignore[attr-defined]

    async def _noop(*a, **k):
        return None

    async def _true(*a, **k):
        return True

    async def _history(agent_, chatroom_id, context_limit, models):
        return [SimpleNamespace(role="user", content="hello", sender_id=uuid.uuid4(), id=uuid.uuid4())]

    async def _labels(agent_, chatroom_id, history):
        return {}, {}

    async def _none(*a, **k):
        return None

    async def _empty_list(*a, **k):
        return []

    async def _pending(agent_, chatroom_id_):
        return None, [], []

    stream_seen: dict = {}

    async def _stream(**kw):
        stream_seen.update(kw)
        return ("private analysis", 1)

    async def _memory(agent_, chatroom_id):
        return "[Your previous observations]\n- earlier"

    engine._audit = _noop  # type: ignore[attr-defined]
    engine._turn_rate_allowed = _true  # type: ignore[attr-defined]
    engine._assemble_history = _history  # type: ignore[attr-defined]
    engine._participant_labels = _labels  # type: ignore[attr-defined]
    engine._rag_context = _none  # type: ignore[attr-defined]
    engine._graphrag_context = _none  # type: ignore[attr-defined]
    engine._pending_context_and_tools = _pending  # type: ignore[attr-defined]
    engine._builtin_tools = _empty_list  # type: ignore[attr-defined]
    engine._resolve_trigger_attachments = _none  # type: ignore[attr-defined]
    engine._stage_workspace_inputs = _none  # type: ignore[attr-defined]
    engine._model_attachment_blocks = _empty_list  # type: ignore[attr-defined]
    engine._observer_memory_block = _memory  # type: ignore[attr-defined]
    engine._stream_with_tools = _stream  # type: ignore[attr-defined]
    engine._provider_message = (  # type: ignore[attr-defined]
        lambda hm, aid, an, un, attachment_blocks=None: {"role": "user", "content": hm.content}
    )

    async def _boom(*a, **k):
        raise AssertionError("observer turns must not dispatch message signals / reply wakeups")

    engine._dispatch_agent_message_signal = _boom  # type: ignore[attr-defined]
    engine._dispatch_agent_reply_wakeups = _boom  # type: ignore[attr-defined]
    engine._persist_artifacts = _boom  # type: ignore[attr-defined]

    return engine, recorded, stream_seen


@pytest.mark.asyncio
async def test_observer_turn_emits_nothing_on_room_channel(monkeypatch) -> None:
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    room_id = uuid.uuid4()

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=room_id,
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert result.message_id is None
    assert result.text == "private analysis"

    # The leak pin: zero events on any room channel, across the whole turn.
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    # Creator channel got exactly started + created, ids only (R28.13).
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.created"]
    for _ch, _ev, payload in user_events:
        assert "private analysis" not in repr(payload)

    # Token streaming was suppressed via the headless mechanism.
    assert stream_seen["room"] is None

    # Output persisted as an observation with the turn's trigger.
    assert recorded["content_md"] == "private analysis"
    assert recorded["trigger"] == "every_n_messages"
    assert recorded["chatroom_id"] == room_id


@pytest.mark.asyncio
async def test_observer_turn_folds_memory_and_framing_into_system(monkeypatch) -> None:
    agent = _observer_agent()
    engine, _recorded, stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=uuid.uuid4())

    await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    system_text = stream_seen["system_text"]
    assert "[Observer role]" in system_text
    assert "[Your previous observations]" in system_text


@pytest.mark.asyncio
async def test_observer_turn_no_input_emits_observation_failed(monkeypatch) -> None:
    """Benign skip paths must still clear the creator's 'analyzing' indicator
    (R28.13) — previously only key_group_scope/rate_limited/exception did."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _empty_history(agent_, chatroom_id, context_limit, models):
        return []

    engine._assemble_history = _empty_history  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "no_input"
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.failed"]
    assert user_events[-1][2]["kind"] == "no_input"


@pytest.mark.asyncio
async def test_observer_turn_empty_reply_emits_observation_failed(monkeypatch) -> None:
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _blank_stream(**kw):
        return ("   ", 1)

    engine._stream_with_tools = _blank_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "empty_reply"
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.failed"]
    assert user_events[-1][2]["kind"] == "empty_reply"


# --------------------------------------------------------------------------- #
# pending_notify renderer branch (R28.07)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pending_context_renders_released_observation(monkeypatch) -> None:
    room_id = uuid.uuid4()
    notes = [{"kind": "released_observation", "chatroom_id": str(room_id), "content": "the brief"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, drained = await engine._pending_context_and_tools(_observer_agent(), room_id)

    assert block is not None
    assert "The room owner shared an analysis with you:" in block
    assert "the brief" in block
    assert tools == []
    assert drained == notes


@pytest.mark.asyncio
async def test_pending_context_requeues_released_observation_for_a_different_room(monkeypatch) -> None:
    """R28.07 leak fix — pending_notify is keyed only by agent id, not room, so a
    note released into room A must never render into a turn running in room B."""
    room_a, room_b = uuid.uuid4(), uuid.uuid4()
    notes = [{"kind": "released_observation", "chatroom_id": str(room_a), "content": "room A's secret"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)
    requeued: list[tuple[uuid.UUID, list]] = []

    async def _requeue(agent_id, requeued_notes):
        requeued.append((agent_id, requeued_notes))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.requeue", _requeue)

    agent = _observer_agent()
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, drained = await engine._pending_context_and_tools(agent, room_b)

    assert block is None
    assert tools == []
    assert drained == []
    # Put back for room A's next turn — never rendered, never dropped.
    assert requeued == [(agent.id, notes)]


@pytest.mark.asyncio
async def test_pending_context_requeues_released_observation_for_headless_turn(monkeypatch) -> None:
    """A headless A2A turn has no room context at all, so any released
    observation note must be put back rather than rendered."""
    notes = [{"kind": "released_observation", "chatroom_id": str(uuid.uuid4()), "content": "secret"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)
    requeued: list[tuple[uuid.UUID, list]] = []

    async def _requeue(agent_id, requeued_notes):
        requeued.append((agent_id, requeued_notes))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.requeue", _requeue)

    agent = _observer_agent()
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, drained = await engine._pending_context_and_tools(agent, None)

    assert block is None
    assert drained == []
    assert requeued == [(agent.id, notes)]


# --------------------------------------------------------------------------- #
# best-effort helpers must not roll back the whole turn transaction
# --------------------------------------------------------------------------- #


class _RollbackAssertingDB(_FakeDB):
    def __init__(self) -> None:
        self.rollback_called = False

    async def rollback(self) -> None:
        self.rollback_called = True


@pytest.mark.asyncio
async def test_observer_memory_block_failure_does_not_rollback_whole_transaction(monkeypatch) -> None:
    """A DB hiccup fetching self-memory must not wipe the turn's already-
    pending agent.turn_started audit insert — only the SAVEPOINT rolls back,
    never self._db.rollback()."""

    class _BoomRepo:
        def __init__(self, db) -> None:
            pass

        async def list_recent_for_agent(self, **kw):
            raise RuntimeError("transient db error")

    monkeypatch.setattr(te, "ObservationRepository", _BoomRepo)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    db = _RollbackAssertingDB()
    engine._db = db  # type: ignore[attr-defined]

    result = await engine._observer_memory_block(_observer_agent(), uuid.uuid4())

    assert result is None
    assert db.rollback_called is False


@pytest.mark.asyncio
async def test_emit_observation_event_recipient_lookup_failure_does_not_rollback(monkeypatch) -> None:
    class _BoomObsService:
        def __init__(self, db) -> None:
            pass

        async def recipient_user_id(self, chatroom_id):
            raise RuntimeError("transient db error")

    monkeypatch.setattr(te, "ObservationService", _BoomObsService)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    db = _RollbackAssertingDB()
    engine._db = db  # type: ignore[attr-defined]

    await engine._emit_observation_event(uuid.uuid4(), uuid.uuid4(), "observation.failed", {"kind": "x"})

    assert db.rollback_called is False


@pytest.mark.asyncio
async def test_observer_memory_block_succeeds_normally(monkeypatch) -> None:
    """Confirms the SAVEPOINT wrapping doesn't break the happy path."""

    class _OkRepo:
        def __init__(self, db) -> None:
            pass

        async def list_recent_for_agent(self, **kw):
            return [SimpleNamespace(created_at=None, content_md="earlier note")]

    monkeypatch.setattr(te, "ObservationRepository", _OkRepo)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]

    result = await engine._observer_memory_block(_observer_agent(), uuid.uuid4())

    assert result is not None
    assert "earlier note" in result


# --------------------------------------------------------------------------- #
# ObservationRepository.list — before-cursor cross-room scoping
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_observation_list_before_anchor_scoped_by_chatroom_id() -> None:
    """The before-cursor anchor lookup must not resolve a different room's
    observation id; it must be scoped by chatroom_id exactly like the page
    query is — otherwise a creator could page room A using the timestamp of
    an observation id borrowed from room B."""
    room_id = uuid.uuid4()
    before_id = uuid.uuid4()

    db = AsyncMock()
    anchor_result = MagicMock()
    anchor_result.first.return_value = SimpleNamespace(
        created_at=datetime(2026, 1, 1, tzinfo=UTC), id=before_id
    )
    page_result = MagicMock()
    page_result.all.return_value = []
    db.execute.side_effect = [anchor_result, page_result]

    repo = ObservationRepository(db)
    await repo.list(chatroom_id=room_id, before=before_id, limit=10)

    anchor_stmt = db.execute.await_args_list[0].args[0]
    compiled = str(anchor_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert str(room_id) in compiled
    assert str(before_id) in compiled
