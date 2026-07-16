"""AC-7 — the turn-time skills tap drops per skill, never per turn (§31).

`_resolve_skills` is the third AuthZ tap and the one shared by both turn paths.
Building a full TurnEngine needs settings/router/qdrant wiring, so these exercise
it as an unbound method over a stub — the same shape as
`test_turn_engine_observer_activity.py` — with the skills facade and the room
publisher stubbed.

What is proven here is the *engine's* half: that the tap runs, that a drop costs
the skill and not the turn, and that the audit and the room warning happen. Which
bindings the tap should drop is `test_skill_binding.py`'s matrix.
"""

from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contexts.agents.application.runtime import turn_engine as te
from contexts.agents.application.runtime.turn_engine import TurnEngine
from contexts.skills.application.binding_service import BoundSet, DroppedSkill
from tests.unit.skill_fakes import make_skill


@pytest.fixture
def emitted() -> list[tuple[str, dict]]:
    return []


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, emitted: list[tuple[str, dict]]) -> SimpleNamespace:
    class _Publisher:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        async def emit(self, event_type: str, data: dict) -> int:
            emitted.append((event_type, data))
            return 1

    monkeypatch.setattr(te, "Publisher", _Publisher)
    stub = SimpleNamespace(_db=object())
    stub._audit = AsyncMock()
    return stub


def _facade(monkeypatch: pytest.MonkeyPatch, bound: BoundSet) -> AsyncMock:
    resolve = AsyncMock(return_value=bound)
    monkeypatch.setattr(te, "SkillsFacade", lambda _db: SimpleNamespace(resolve_bound_set=resolve))
    return resolve


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


async def test_the_tap_asks_for_the_agents_own_scope(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _agent()
    resolve = _facade(monkeypatch, BoundSet(skills=(make_skill(),)))

    await TurnEngine._resolve_skills(engine, agent, uuid.uuid4(), "ws:room:x")

    resolve.assert_awaited_once_with(agent_id=agent.id, agent_project_id=agent.project_id)


async def test_a_clean_snapshot_is_returned_untouched_and_says_nothing(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, emitted: list
) -> None:
    skill = make_skill(name="pdf-fill")
    _facade(monkeypatch, BoundSet(skills=(skill,)))

    bound = await TurnEngine._resolve_skills(engine, _agent(), uuid.uuid4(), "ws:room:x")

    assert bound.skills == (skill,)
    assert emitted == []
    engine._audit.assert_not_awaited()


async def test_a_dropped_skill_is_audited_and_the_survivors_still_run(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = make_skill(name="pdf-fill")
    gone = DroppedSkill(skill_id=uuid.uuid4(), name="moved-away", reason="scope")
    _facade(monkeypatch, BoundSet(skills=(kept,), dropped=(gone,)))
    room_id = uuid.uuid4()

    bound = await TurnEngine._resolve_skills(engine, _agent(), room_id, "ws:room:x")

    # The turn is not skipped and the surviving skill is not collateral: an agent
    # cannot run without a key, but it runs fine without one of twenty skills.
    assert bound.skills == (kept,)
    action = engine._audit.await_args.args[2]
    meta = engine._audit.await_args.args[3]
    assert action == "skill.resolution_failed"
    assert meta == {"skill_id": str(gone.skill_id), "name": "moved-away", "reason": "scope"}


async def test_every_dropped_skill_is_audited_but_the_room_hears_one_warning(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, emitted: list
) -> None:
    # A project move drops every skill it owned at once; twenty toasts describe one
    # event, so the warning aggregates while the audit trail stays per skill.
    dropped = tuple(DroppedSkill(skill_id=uuid.uuid4(), name=n, reason="scope") for n in ("a", "b", "c"))
    _facade(monkeypatch, BoundSet(skills=(), dropped=dropped))
    agent = _agent()

    await TurnEngine._resolve_skills(engine, agent, uuid.uuid4(), "ws:room:x")

    assert engine._audit.await_count == 3
    assert len(emitted) == 1
    event, data = emitted[0]
    assert event == "agent.warning"
    assert data == {
        "agent_id": str(agent.id),
        "kind": "skills_unavailable",
        "dropped": 3,
    }


async def test_the_room_warning_never_names_the_dropped_skills(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, emitted: list
) -> None:
    # The room channel is a blind relay and `can_read` admits a chatroom guest who is not
    # a project member, while GET /skill-bindings is gated on membership. Naming them here
    # would route around that: a guest could watch code_exec get disabled and harvest the
    # names of the agent's org- and platform-scoped skills.
    secret = "acme-merger-due-diligence"
    _facade(
        monkeypatch,
        BoundSet(skills=(), dropped=(DroppedSkill(skill_id=uuid.uuid4(), name=secret, reason="scope"),)),
    )

    await TurnEngine._resolve_skills(engine, _agent(), uuid.uuid4(), "ws:room:x")

    assert secret not in json.dumps(emitted)
    # The audit trail is the surface that may name it — it is not guest-readable.
    assert engine._audit.await_args.args[3]["name"] == secret


async def test_a_failed_warning_is_logged_rather_than_swallowed(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # AC-7 makes the drop invisible in the reply by design, so this emit is the only live
    # signal that skills left the turn. A signal that can vanish without trace is not one.
    class _Broken:
        def __init__(self, _channel: str) -> None:
            pass

        async def emit(self, *_args, **_kwargs) -> int:
            raise RuntimeError("redis down")

    monkeypatch.setattr(te, "Publisher", _Broken)
    _facade(
        monkeypatch,
        BoundSet(skills=(), dropped=(DroppedSkill(skill_id=uuid.uuid4(), name="a", reason="scope"),)),
    )

    with caplog.at_level(logging.WARNING):
        await TurnEngine._resolve_skills(engine, _agent(), uuid.uuid4(), "ws:room:x")

    assert any("skills warning emit failed" in r.message for r in caplog.records)


async def test_a_headless_turn_audits_the_drop_and_emits_nothing(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, emitted: list
) -> None:
    # An A2A turn has no room and an observer turn deliberately has no room channel
    # (R28.01). Both reach here with room=None and must not emit.
    _facade(
        monkeypatch,
        BoundSet(skills=(), dropped=(DroppedSkill(skill_id=uuid.uuid4(), name="a", reason="scope"),)),
    )

    await TurnEngine._resolve_skills(engine, _agent(), None, None)

    engine._audit.assert_awaited_once()
    assert emitted == []


async def test_a_broken_room_channel_never_costs_the_turn_its_skills(
    engine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The warning is best-effort: a Redis hiccup must not turn a degraded turn into
    # a failed one.
    class _Broken:
        def __init__(self, _channel: str) -> None:
            pass

        async def emit(self, *_args, **_kwargs) -> int:
            raise RuntimeError("redis down")

    monkeypatch.setattr(te, "Publisher", _Broken)
    kept = make_skill(name="pdf-fill")
    _facade(
        monkeypatch,
        BoundSet(
            skills=(kept,),
            dropped=(DroppedSkill(skill_id=uuid.uuid4(), name="a", reason="scope"),),
        ),
    )

    bound = await TurnEngine._resolve_skills(engine, _agent(), uuid.uuid4(), "ws:room:x")

    assert bound.skills == (kept,)
