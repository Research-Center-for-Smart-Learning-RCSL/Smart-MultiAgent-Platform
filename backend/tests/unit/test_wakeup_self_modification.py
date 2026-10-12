from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.orchestration.application.wakeup_service import WakeupService
from contexts.orchestration.domain.errors import WakeupToggleNotAllowed


async def test_self_modification_preserves_designer_config_and_bounds(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    initial = SimpleNamespace(
        id=agent_id,
        deleted_at=None,
        version=1,
        wakeup_config={
            "triggers": {"every_n_messages": {"enabled": True, "n": 7}},
            "soft_bounds": {"n_min": 5, "n_max": 10},
            "designer_note": "x",
        },
    )
    captured = []
    audits = []

    class _Agents:
        current = initial

        async def get_agent(self, requested_id):
            assert requested_id == agent_id
            return self.current

        async def patch_agent(self, *, agent_id, draft, expected_version, **_kwargs):
            assert agent_id == initial.id
            assert expected_version == self.current.version
            captured.append(draft.wakeup_config)
            self.current = SimpleNamespace(
                id=agent_id,
                deleted_at=None,
                version=expected_version + 1,
                wakeup_config=draft.wakeup_config,
            )
            return self.current

    async def _emit(_db, event, *, isolated: bool = False):
        # `isolated=True` on this path: the clamp audit rides the turn's own
        # session, which the reply commit depends on.
        audits.append(event)
        return True

    svc = WakeupService.__new__(WakeupService)
    svc._db = None
    svc._agents_facade = _Agents()
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.audit.emit",
        _emit,
    )

    await svc.update_wakeup(agent_id=agent_id, every_n_messages=1)
    await svc.update_wakeup(agent_id=agent_id, every_n_messages=1)

    assert len(captured) == 2
    for config in captured:
        assert config["soft_bounds"] == {"n_min": 5, "n_max": 10}
        assert config["designer_note"] == "x"
        assert config["triggers"]["every_n_messages"]["n"] == 5
    assert [event.action for event in audits] == [
        "agent.wakeup_clamped",
        "agent.wakeup_clamped",
    ]


# ---------------------------------------------------------------------------
# Designer-allowed trigger toggles ([R15.06], [R15.06a])
# ---------------------------------------------------------------------------


def _toggle_service(monkeypatch, wakeup_config: dict):
    agent_id = uuid.uuid4()
    state = {
        "agent": SimpleNamespace(id=agent_id, deleted_at=None, version=1, wakeup_config=wakeup_config),
        "patched": [],
        "audits": [],
    }

    class _Agents:
        async def get_agent(self, requested_id):
            return state["agent"]

        async def patch_agent(self, *, agent_id, draft, expected_version, **_kwargs):
            state["patched"].append(draft.wakeup_config)
            state["agent"] = SimpleNamespace(
                id=agent_id, deleted_at=None, version=expected_version + 1, wakeup_config=draft.wakeup_config
            )
            return state["agent"]

    async def _emit(_db, event, *, isolated: bool = False):
        state["audits"].append(event)
        return True

    svc = WakeupService.__new__(WakeupService)
    svc._db = None
    svc._agents_facade = _Agents()
    monkeypatch.setattr("contexts.orchestration.application.wakeup_service.audit.emit", _emit)
    return svc, agent_id, state


def _config(*, every_on: bool, silence_on: bool, self_toggle: list[str], call_only: bool = False) -> dict:
    return {
        "triggers": {
            "every_n_messages": {"enabled": every_on, "n": 2},
            "silence_minutes": {"enabled": silence_on, "t_minutes": 2},
            "call_only": {"enabled": call_only},
        },
        "self_toggle": self_toggle,
    }


async def test_an_allowed_toggle_is_written_and_audited(monkeypatch) -> None:
    """C AC-4."""
    svc, agent_id, state = _toggle_service(
        monkeypatch, _config(every_on=True, silence_on=False, self_toggle=["silence_minutes"])
    )
    actor = uuid.uuid4()

    cfg = await svc.update_wakeup(agent_id=agent_id, silence_minutes_enabled=True, actor_agent_id=actor)

    assert cfg.triggers.silence_minutes.enabled is True
    assert state["patched"][-1]["triggers"]["silence_minutes"]["enabled"] is True
    (toggled,) = [e for e in state["audits"] if e.action == "agent.wakeup_toggled"]
    assert toggled.metadata == {"trigger": "silence_minutes", "enabled": True, "actor_agent_id": str(actor)}


async def test_a_toggle_outside_self_toggle_is_refused(monkeypatch) -> None:
    """C AC-4: the default, an empty list, keeps [R15.06]'s old behaviour exactly."""
    svc, agent_id, state = _toggle_service(
        monkeypatch, _config(every_on=True, silence_on=False, self_toggle=[])
    )

    with pytest.raises(WakeupToggleNotAllowed, match="silence_minutes"):
        await svc.update_wakeup(agent_id=agent_id, silence_minutes_enabled=True)

    assert state["patched"] == []


async def test_toggles_are_refused_while_call_only(monkeypatch) -> None:
    """C AC-5."""
    svc, agent_id, state = _toggle_service(
        monkeypatch,
        _config(every_on=False, silence_on=False, self_toggle=["every_n_messages"], call_only=True),
    )

    with pytest.raises(WakeupToggleNotAllowed):
        await svc.update_wakeup(agent_id=agent_id, every_n_messages_enabled=True)
    assert state["patched"] == []


async def test_a_toggle_that_would_leave_every_trigger_off_is_refused(monkeypatch) -> None:
    """C AC-5: an injected "go quiet" must not make the agent permanently inert."""
    svc, agent_id, state = _toggle_service(
        monkeypatch,
        _config(every_on=True, silence_on=False, self_toggle=["every_n_messages", "silence_minutes"]),
    )

    with pytest.raises(WakeupToggleNotAllowed, match="turn off every trigger"):
        await svc.update_wakeup(agent_id=agent_id, every_n_messages_enabled=False)
    assert state["patched"] == []


async def test_the_tool_cannot_reach_the_grant_or_the_mode(monkeypatch) -> None:
    """C AC-6: only n, t_minutes and the two enabled flags are writable."""
    svc, agent_id, state = _toggle_service(
        monkeypatch, _config(every_on=True, silence_on=False, self_toggle=["silence_minutes"])
    )

    await svc.update_wakeup(agent_id=agent_id, silence_minutes_enabled=True, every_n_messages=4)

    written = state["patched"][-1]
    assert written["self_toggle"] == ["silence_minutes"]
    assert written["triggers"]["every_n_messages"]["count"] == "all"
    assert written["triggers"]["call_only"]["enabled"] is False
    with pytest.raises(TypeError):
        await svc.update_wakeup(agent_id=agent_id, self_toggle=["every_n_messages"])  # type: ignore[call-arg]
