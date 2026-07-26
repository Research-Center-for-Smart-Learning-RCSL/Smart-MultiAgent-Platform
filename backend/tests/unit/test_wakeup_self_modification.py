from __future__ import annotations

import uuid
from types import SimpleNamespace

from contexts.orchestration.application.wakeup_service import WakeupService


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

    async def _emit(_db, event):
        audits.append(event)

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
