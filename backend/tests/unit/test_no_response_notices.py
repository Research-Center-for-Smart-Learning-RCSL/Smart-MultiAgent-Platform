"""'Why no response' surfacing — skip notices + presence-gate notification.

When an agent turn is skipped, the room otherwise just goes quiet. These tests
pin the two surfacing paths added for that trap:

- ``TurnEngine._run_locked`` emits ``agent.finished{error}`` for actionable
  skips (key_group_scope / rate_limited, any trigger) and for unavailability
  (agent_gone / not_bound) only on an explicit @mention.
- ``WakeupService._notify_wakeup_gated`` tells a room's project owners that an
  ``every_n_messages`` wake-up was suppressed by the presence gate, debounced.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.orchestration.application.wakeup_service import WakeupService
from contexts.tenancy.domain.models import ProjectMemberRole

# --------------------------------------------------------------------------- #
# TurnEngine skip notices
# --------------------------------------------------------------------------- #


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _locked_agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        model_hint=SimpleNamespace(value="claude"),
    )


def _wire_locked(
    monkeypatch,
    *,
    agent,
    bound: bool = True,
    group: str = "match",
    rate_ok: bool = True,
    hint_serviceable: bool = True,
):
    """Wire ``_run_locked``'s early guards to fakes and capture WS emits.

    ``group`` is one of 'match' (key group OK), 'mismatch' (wrong project),
    'none' (deleted). Returns ``(engine, emitted)`` where ``emitted`` is a list
    of ``(event, payload)`` tuples.
    """
    emitted: list[tuple[str, dict]] = []

    async def _fake_emit(chatroom_id, agent_id, reason) -> None:
        emitted.append(("agent.finished", {"error": reason, "agent_id": str(agent_id)}))

    monkeypatch.setattr(te, "emit_agent_finished_error", _fake_emit)

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _ChatroomAgentRepo:
        def __init__(self, db) -> None:
            pass

        async def role_of(self, *, chatroom_id, agent_id):
            from contexts.conversation.domain.models import ChatroomAgentRole

            return ChatroomAgentRole.NORMAL if bound else None

    monkeypatch.setattr(te, "ChatroomAgentRepository", _ChatroomAgentRepo)

    grp = None
    if group == "match" and agent is not None:
        grp = SimpleNamespace(project_id=agent.project_id)
    elif group == "mismatch":
        grp = SimpleNamespace(project_id=uuid.uuid4())

    class _KeysFacade:
        def __init__(self, db) -> None:
            pass

        async def get_key_group(self, kgid):
            return grp

        async def has_carried_provider_in_group(self, kgid, provider):
            return hint_serviceable

    monkeypatch.setattr(te, "KeysFacade", _KeysFacade)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]

    async def _noop_audit(*a, **k):
        return None

    async def _rate(aid, rid):
        return rate_ok

    engine._audit = _noop_audit  # type: ignore[attr-defined]
    engine._turn_rate_allowed = _rate  # type: ignore[attr-defined]
    return engine, emitted


async def _run_locked(engine, *, trigger):
    return await engine._run_locked(
        agent_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        trigger=trigger,
        parent_agent_id=None,
        input_text=None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_agent_gone_emits_on_mention(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=None)
    result = await _run_locked(engine, trigger="mention")
    assert result.reason == "agent_gone"
    assert len(emitted) == 1
    event, payload = emitted[0]
    assert event == "agent.finished"
    # Surfaced under `error` (not `reason`): the client toasts `error` and
    # treats `reason` as a benign silent skip (e.g. empty_reply).
    assert payload["error"] == "agent_gone"
    assert "agent_id" in payload


@pytest.mark.asyncio
async def test_agent_gone_silent_on_autonomous_trigger(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=None)
    result = await _run_locked(engine, trigger="every_n_messages")
    assert result.reason == "agent_gone"
    assert emitted == []


@pytest.mark.asyncio
async def test_not_bound_emits_on_mention(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), bound=False)
    result = await _run_locked(engine, trigger="mention")
    assert result.reason == "not_bound"
    assert emitted[0][1]["error"] == "not_bound"


@pytest.mark.asyncio
async def test_not_bound_silent_on_autonomous_trigger(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), bound=False)
    result = await _run_locked(engine, trigger="silence_minutes")
    assert result.reason == "not_bound"
    assert emitted == []


@pytest.mark.asyncio
async def test_not_bound_emits_on_release(monkeypatch) -> None:
    # R28.07: a creator's explicit release-wake is the same shape of explicit
    # call as a mention; it must not silently no-op against an unbound agent.
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), bound=False)
    result = await _run_locked(engine, trigger="release")
    assert result.reason == "not_bound"
    assert emitted[0][1]["error"] == "not_bound"


@pytest.mark.asyncio
async def test_agent_gone_emits_on_release(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=None)
    result = await _run_locked(engine, trigger="release")
    assert result.reason == "agent_gone"
    assert emitted[0][1]["error"] == "agent_gone"


@pytest.mark.asyncio
async def test_explicit_triggers_all_emit_and_autonomous_stay_silent(monkeypatch) -> None:
    """Systemic guard (F-21): table-driven over the shared `EXPLICIT_TRIGGERS`
    constant so a future trigger kind added to that set without a matching
    engine update fails this test automatically."""
    from contexts.orchestration.domain.models import EXPLICIT_TRIGGERS

    for trigger in EXPLICIT_TRIGGERS:
        engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), bound=False)
        result = await _run_locked(engine, trigger=trigger)
        assert result.reason == "not_bound"
        assert emitted[0][1]["error"] == "not_bound"

        engine, emitted = _wire_locked(monkeypatch, agent=None)
        result = await _run_locked(engine, trigger=trigger)
        assert result.reason == "agent_gone"
        assert emitted[0][1]["error"] == "agent_gone"

    for trigger in ("every_n_messages", "silence_minutes"):
        engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), bound=False)
        result = await _run_locked(engine, trigger=trigger)
        assert result.reason == "not_bound"
        assert emitted == []

        engine, emitted = _wire_locked(monkeypatch, agent=None)
        result = await _run_locked(engine, trigger=trigger)
        assert result.reason == "agent_gone"
        assert emitted == []


@pytest.mark.asyncio
async def test_key_group_scope_emits_on_any_trigger(monkeypatch) -> None:
    # Actionable: a present user sees the agent go quiet — surface regardless of
    # how the turn was triggered (not just @mention).
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), group="mismatch")
    result = await _run_locked(engine, trigger="every_n_messages")
    assert result.reason == "key_group_scope"
    assert emitted[0][1]["error"] == "key_group_scope"


@pytest.mark.asyncio
async def test_model_hint_unserviceable_emits_on_any_trigger(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), hint_serviceable=False)

    result = await _run_locked(engine, trigger="every_n_messages")

    assert result.reason == "model_hint_unserviceable"
    assert emitted[0][1]["error"] == "model_hint_unserviceable"


@pytest.mark.asyncio
async def test_rate_limited_emits_on_any_trigger(monkeypatch) -> None:
    engine, emitted = _wire_locked(monkeypatch, agent=_locked_agent(), rate_ok=False)
    result = await _run_locked(engine, trigger="silence_minutes")
    assert result.reason == "rate_limited"
    assert emitted[0][1]["error"] == "rate_limited"


# --------------------------------------------------------------------------- #
# WakeupService._notify_wakeup_gated
# --------------------------------------------------------------------------- #


def _svc() -> WakeupService:
    svc = WakeupService.__new__(WakeupService)
    svc._db = _FakeDB()  # type: ignore[attr-defined]
    return svc


def _member(role):
    return SimpleNamespace(user_id=uuid.uuid4(), role=role)


def _wire_notify(monkeypatch, *, claim: bool, members):
    sends: list[dict] = []

    async def _claim(agent_id, room_id, cooldown_s=3600):
        return claim

    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.claim_gated_notice", _claim)

    class _Tenancy:
        def __init__(self, db) -> None:
            pass

        async def project_members(self, project_id):
            return members

    monkeypatch.setattr("contexts.tenancy.interfaces.facade.TenancyFacade", _Tenancy)

    class _Notif:
        def __init__(self, db) -> None:
            pass

        async def send(self, **kwargs):
            sends.append(kwargs)
            return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr("contexts.notification.interfaces.facade.NotificationFacade", _Notif)
    return sends


@pytest.mark.asyncio
async def test_gated_notice_targets_only_owners(monkeypatch) -> None:
    owner_a, owner_b = _member(ProjectMemberRole.OWNER), _member(ProjectMemberRole.OWNER)
    plain = _member(ProjectMemberRole.MEMBER)
    sends = _wire_notify(monkeypatch, claim=True, members=[owner_a, plain, owner_b])

    agent = SimpleNamespace(id=uuid.uuid4(), name="Nova", project_id=uuid.uuid4())
    await _svc()._notify_wakeup_gated(agent, uuid.uuid4())

    recipients = {s["user_id"] for s in sends}
    assert recipients == {owner_a.user_id, owner_b.user_id}
    assert all(s["kind"].value == "agent.wakeup_gated" for s in sends)
    assert all("Nova" in s["body"] for s in sends)


@pytest.mark.asyncio
async def test_gated_notice_debounced_when_claim_fails(monkeypatch) -> None:
    sends = _wire_notify(monkeypatch, claim=False, members=[_member(ProjectMemberRole.OWNER)])
    agent = SimpleNamespace(id=uuid.uuid4(), name="Nova", project_id=uuid.uuid4())
    await _svc()._notify_wakeup_gated(agent, uuid.uuid4())
    assert sends == []


@pytest.mark.asyncio
async def test_gated_notice_noop_without_owners(monkeypatch) -> None:
    sends = _wire_notify(monkeypatch, claim=True, members=[_member(ProjectMemberRole.MEMBER)])
    agent = SimpleNamespace(id=uuid.uuid4(), name="Nova", project_id=uuid.uuid4())
    await _svc()._notify_wakeup_gated(agent, uuid.uuid4())
    assert sends == []


@pytest.mark.asyncio
async def test_gated_notice_releases_token_when_delivery_fails(monkeypatch) -> None:
    # A claimed-but-undelivered notice must free the debounce token so the next
    # gated message can retry, rather than going dark for the whole cooldown.
    released: list = []

    async def _claim(agent_id, room_id, cooldown_s=3600):
        return True

    async def _release(agent_id, room_id):
        released.append((agent_id, room_id))

    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.claim_gated_notice", _claim)
    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.release_gated_notice", _release)

    class _Tenancy:
        def __init__(self, db) -> None:
            pass

        async def project_members(self, project_id):
            return [_member(ProjectMemberRole.OWNER)]

    monkeypatch.setattr("contexts.tenancy.interfaces.facade.TenancyFacade", _Tenancy)

    class _Notif:
        def __init__(self, db) -> None:
            pass

        async def send(self, **kwargs):
            raise RuntimeError("provider down")

    monkeypatch.setattr("contexts.notification.interfaces.facade.NotificationFacade", _Notif)

    agent = SimpleNamespace(id=uuid.uuid4(), name="Nova", project_id=uuid.uuid4())
    # Best-effort: must swallow the failure, not propagate into wake-up dispatch.
    await _svc()._notify_wakeup_gated(agent, uuid.uuid4())
    assert len(released) == 1


# --------------------------------------------------------------------------- #
# on_message_created presence gate → notifier wiring
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_every_n_presence_gate_notifies_and_skips(monkeypatch) -> None:
    # The exact trap: every_n fires on an AGENT-authored message, but
    # allow_self_open=false and nobody is in the room → the agent is gated out of
    # the wake list AND the owners are told. A user-authored message is the
    # opposite case and must not be gated (see the test below).
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        name="Nova",
        project_id=uuid.uuid4(),
        deleted_at=None,
        wakeup_config={
            "triggers": {"every_n_messages": {"enabled": True, "n": 1}},
            "allow_self_open": False,
        },
    )

    async def _noop(*a, **k):
        return None

    async def _count(aid, rid):
        return 1  # count % n == 0 → trigger fires

    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.touch_silence_timestamp", _noop)
    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.reset_autostop", _noop)
    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.increment_message_count", _count)

    svc = WakeupService.__new__(WakeupService)
    svc._db = _FakeDB()  # type: ignore[attr-defined]
    svc._agents_facade = SimpleNamespace(get_agent=_async_return(agent))  # type: ignore[attr-defined]
    svc._presence = SimpleNamespace(list_room=_async_return([]))  # type: ignore[attr-defined]

    notified: list = []

    async def _notify(a, rid):
        notified.append((a, rid))

    svc._notify_wakeup_gated = _notify  # type: ignore[attr-defined]

    room = uuid.uuid4()
    wake = await svc.on_message_created(
        room_id=room,
        sender_is_user=False,
        sender_agent_id=uuid.uuid4(),
        agent_ids=[agent.id],
    )

    assert wake == []  # gated out
    assert notified == [(agent, room)]


@pytest.mark.asyncio
async def test_every_n_does_not_gate_a_user_authored_message(monkeypatch) -> None:
    # APP-3. The roster is the WS roster, and a message posted over REST never
    # touches it: a user whose socket is still handshaking (the first message in
    # a freshly created room) or mid-reconnect reads as "nobody present". The
    # triggering message is itself evidence of presence, so the gate must not run
    # for a user-authored message -- otherwise the agent stays silent and the
    # owners get a bell claiming the room was empty while the sender was in it.
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        name="Nova",
        project_id=uuid.uuid4(),
        deleted_at=None,
        wakeup_config={
            "triggers": {"every_n_messages": {"enabled": True, "n": 1}},
            "allow_self_open": False,
        },
    )

    async def _noop(*a, **k):
        return None

    async def _count(aid, rid):
        return 1

    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.touch_silence_timestamp", _noop)
    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.reset_autostop", _noop)
    monkeypatch.setattr("contexts.orchestration.infrastructure.wakeup_state.increment_message_count", _count)

    roster_reads: list = []

    async def _list_room(rid):
        roster_reads.append(rid)
        return []

    svc = WakeupService.__new__(WakeupService)
    svc._db = _FakeDB()  # type: ignore[attr-defined]
    svc._agents_facade = SimpleNamespace(get_agent=_async_return(agent))  # type: ignore[attr-defined]
    svc._presence = SimpleNamespace(list_room=_list_room)  # type: ignore[attr-defined]

    notified: list = []

    async def _notify(a, rid):
        notified.append((a, rid))

    svc._notify_wakeup_gated = _notify  # type: ignore[attr-defined]

    room = uuid.uuid4()
    wake = await svc.on_message_created(room_id=room, sender_is_user=True, agent_ids=[agent.id])

    assert wake == [agent.id]
    assert notified == []
    # Not merely "the empty roster was tolerated" -- the read is skipped outright,
    # so a Redis round-trip is spared on the hot send path too.
    assert roster_reads == []


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f
