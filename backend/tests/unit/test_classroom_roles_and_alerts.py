"""Classroom roles and analyst alerts, milestone 1 of
docs/tasks/2026-11-09-classroom-agent-collaboration ([R34.01]-[R34.03]).

The constraints and write-once timestamps are exercised against PostgreSQL in
tests/integration/test_classroom_roles_and_alerts_db.py; this file covers the
gates, the tool surface and the export shape.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from contexts.agents.application.runtime import observer_tools
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.research_export_builder import AlertRow, ResearchExportBuilder
from contexts.conversation.domain.errors import NotRoomCreator
from contexts.conversation.domain.models import (
    AlertActionKind,
    AlertViewSurface,
    ChatroomAgentRole,
    ClassroomRole,
)
from shared_kernel.auth.permissions import Principal, Role

_CTX = SimpleNamespace(actor_ip=None, request_id=None)


def _principal(user_id: uuid.UUID | None = None, *, is_admin: bool = False) -> Principal:
    return Principal(user_id=user_id or uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _access(created_by: uuid.UUID | None) -> RoomAccess:
    room = SimpleNamespace(
        created_by_user_id=created_by,
        disclose_observers=True,
        disclose_drafts=True,
        allow_project_owners_only=False,
        allow_project_members=True,
        allow_member_groups=False,
        allow_org_members=False,
        allow_guest_links=False,
    )
    return RoomAccess(
        chatroom=room, project_id=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}), is_guest=False
    )


class TestRoleFit:
    @pytest.mark.parametrize(
        ("classroom", "binding", "fits"),
        [
            (ClassroomRole.NONE, ChatroomAgentRole.NORMAL, True),
            (ClassroomRole.NONE, ChatroomAgentRole.OBSERVER, True),
            (ClassroomRole.TEACHER, ChatroomAgentRole.NORMAL, True),
            (ClassroomRole.TEACHER, ChatroomAgentRole.OBSERVER, False),
            (ClassroomRole.PEER, ChatroomAgentRole.NORMAL, True),
            (ClassroomRole.PEER, ChatroomAgentRole.OBSERVER, False),
            (ClassroomRole.ANALYST, ChatroomAgentRole.OBSERVER, True),
            (ClassroomRole.ANALYST, ChatroomAgentRole.NORMAL, False),
        ],
    )
    def test_matrix(self, classroom: ClassroomRole, binding: ChatroomAgentRole, fits: bool) -> None:
        assert classroom.fits(binding) is fits


class TestClassroomRoleRoute:
    """AC-1: the route is creator-gated and maps each refusal to its own status."""

    def _wire(
        self, monkeypatch: pytest.MonkeyPatch, *, creator: uuid.UUID, result: str
    ) -> list[dict[str, Any]]:
        import app.api.v1.chatrooms as mod

        calls: list[dict[str, Any]] = []

        async def _resolve(db: Any, *, principal: Any, chatroom_id: Any) -> RoomAccess:
            return _access(creator)

        class _Facade:
            def __init__(self, db: Any) -> None:
                pass

            async def set_agent_classroom_role(self, **kw: Any) -> str:
                calls.append(kw)
                return result

        monkeypatch.setattr(mod, "resolve_room_access", _resolve)
        monkeypatch.setattr(mod, "ConversationFacade", _Facade)
        monkeypatch.setattr(mod, "_emit_chatroom_updated", AsyncMock())
        return calls

    async def _call(self, user: uuid.UUID, role: str = "teacher") -> None:
        import app.api.v1.chatrooms as mod

        await mod.patch_chatroom_agent_classroom_role(
            body=mod.AgentClassroomRoleIn(classroom_role=role),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(user),
            db=SimpleNamespace(),
        )

    async def test_the_creator_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid.uuid4()
        calls = self._wire(monkeypatch, creator=uid, result="written")
        await self._call(uid, "peer")
        assert calls[0]["classroom_role"] is ClassroomRole.PEER

    async def test_a_non_creator_is_refused_before_any_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._wire(monkeypatch, creator=uuid.uuid4(), result="written")
        with pytest.raises(NotRoomCreator):
            await self._call(uuid.uuid4())
        assert calls == []

    @pytest.mark.parametrize(
        ("result", "status"), [("not_bound", 404), ("mismatch", 422), ("teacher_taken", 409)]
    )
    async def test_refusals(self, monkeypatch: pytest.MonkeyPatch, result: str, status: int) -> None:
        uid = uuid.uuid4()
        self._wire(monkeypatch, creator=uid, result=result)
        with pytest.raises(HTTPException) as exc:
            await self._call(uid)
        assert exc.value.status_code == status


class TestServiceTransitions:
    def _service(self, roles: tuple[ChatroomAgentRole, ClassroomRole] | None, *, wins: bool = True) -> Any:
        from contexts.conversation.application import chatroom_service as svc_mod

        service = svc_mod.ChatroomService.__new__(svc_mod.ChatroomService)
        service._db = SimpleNamespace()
        written: list[Any] = []

        class _Repo:
            async def locked_roles(self, **_: Any) -> Any:
                return roles

            async def set_classroom_role(self, **kw: Any) -> bool:
                written.append(kw)
                return wins

        service._agents = _Repo()
        service.written = written
        return service

    async def _set(self, service: Any, role: ClassroomRole) -> str:
        return await service.set_agent_classroom_role(
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            classroom_role=role,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

    async def test_each_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from contexts.conversation.application import chatroom_service as svc_mod

        emitted: list[Any] = []
        monkeypatch.setattr(svc_mod.audit, "emit", AsyncMock(side_effect=lambda db, ev: emitted.append(ev)))

        assert await self._set(self._service(None), ClassroomRole.TEACHER) == "not_bound"
        same = self._service((ChatroomAgentRole.NORMAL, ClassroomRole.PEER))
        assert await self._set(same, ClassroomRole.PEER) == "unchanged"
        misfit = self._service((ChatroomAgentRole.NORMAL, ClassroomRole.NONE))
        assert await self._set(misfit, ClassroomRole.ANALYST) == "mismatch"
        assert misfit.written == []
        taken = self._service((ChatroomAgentRole.NORMAL, ClassroomRole.NONE), wins=False)
        assert await self._set(taken, ClassroomRole.TEACHER) == "teacher_taken"
        assert emitted == []
        ok = self._service((ChatroomAgentRole.OBSERVER, ClassroomRole.NONE))
        assert await self._set(ok, ClassroomRole.ANALYST) == "written"
        assert ok.written[0]["expected_role"] is ChatroomAgentRole.OBSERVER
        assert [e.action for e in emitted] == ["chatroom.classroom_role_changed"]
        assert emitted[0].metadata["new_classroom_role"] == "analyst"


def _presentation(*, is_analyst: bool) -> observer_tools.ObservationPresentationContext:
    return observer_tools.ObservationPresentationContext(
        chatroom_id=uuid.uuid4(), project_id=uuid.uuid4(), types_by_key={}, is_analyst=is_analyst
    )


_PROSE = [{"kind": "prose", "text": "Group B has not submitted in ten minutes."}]


class TestAlertArguments:
    """AC-2: only an analyst is offered the alert flag, and the last call wins."""

    def test_a_plain_observer_is_not_offered_the_flag(self) -> None:
        tool = observer_tools.build_present_observation_tool(
            SimpleNamespace(), presentation=_presentation(is_analyst=False)
        )
        assert "alert" not in tool.input_schema["properties"]

    def test_an_analyst_is(self) -> None:
        tool = observer_tools.build_present_observation_tool(
            SimpleNamespace(), presentation=_presentation(is_analyst=True)
        )
        assert tool.input_schema["properties"]["alert"]["type"] == "boolean"
        assert tool.input_schema["properties"]["alert_reason"]["maxLength"] == 200

    async def test_the_flag_follows_the_last_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _materialise(db: Any, *, chatroom_id: Any, blocks: Any, types_by_key: Any) -> Any:
            return list(blocks), []

        monkeypatch.setattr(observer_tools, "materialise", _materialise)
        blocks: list[dict[str, Any]] = []
        alert: dict[str, Any] = {}
        tool = observer_tools.build_present_observation_tool(
            SimpleNamespace(),
            presentation=_presentation(is_analyst=True),
            block_sink=blocks,
            alert_sink=alert,
        )
        await tool.invoke({"blocks": _PROSE, "alert": True, "alert_reason": "  stuck group  "})
        assert alert == {"alert": True, "reason": "stuck group"}
        await tool.invoke({"blocks": _PROSE})
        assert alert == {}

    async def test_a_non_analyst_flag_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _materialise(db: Any, *, chatroom_id: Any, blocks: Any, types_by_key: Any) -> Any:
            return list(blocks), []

        monkeypatch.setattr(observer_tools, "materialise", _materialise)
        alert: dict[str, Any] = {}
        tool = observer_tools.build_present_observation_tool(
            SimpleNamespace(), presentation=_presentation(is_analyst=False), block_sink=[], alert_sink=alert
        )
        await tool.invoke({"blocks": _PROSE, "alert": True})
        assert alert == {}


class TestRoomActionGate:
    """AC-4: only the room creator's own action can be a reaction to an alert."""

    async def test_only_the_creator_closes_alerts(self) -> None:
        from contexts.conversation.application.observation_service import ObservationService

        creator = uuid.uuid4()
        closed: list[Any] = []

        class _Rooms:
            async def get(self, chatroom_id: Any) -> Any:
                return SimpleNamespace(created_by_user_id=creator)

        class _Obs:
            async def mark_first_action(self, **kw: Any) -> int:
                closed.append(kw)
                return 2

        service = ObservationService.__new__(ObservationService)
        service._rooms = _Rooms()
        service._observations = _Obs()
        room = uuid.uuid4()
        assert (
            await service.record_room_action(
                chatroom_id=room, actor_user_id=uuid.uuid4(), kind=AlertActionKind.ROOM_MESSAGE
            )
            == 0
        )
        assert closed == []
        assert (
            await service.record_room_action(
                chatroom_id=room, actor_user_id=creator, kind=AlertActionKind.ACTIVITY_ENDED
            )
            == 2
        )
        assert closed == [{"chatroom_id": room, "kind": AlertActionKind.ACTIVITY_ENDED}]


class TestViewedRoute:
    async def test_creator_gated_and_passes_the_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.api.v1.observations as mod

        gated: list[Any] = []
        recorded: list[Any] = []

        async def _require(db: Any, *, principal: Any, chatroom_id: Any) -> None:
            gated.append(chatroom_id)

        class _Facade:
            def __init__(self, db: Any) -> None:
                pass

            async def record_alert_views(self, **kw: Any) -> int:
                recorded.append(kw)
                return 1

        monkeypatch.setattr(mod, "_require_creator", _require)
        monkeypatch.setattr(mod, "ConversationFacade", _Facade)
        room, obs, caller = uuid.uuid4(), uuid.uuid4(), _principal()
        await mod.record_alert_views(
            body=mod.AlertViewsIn(observation_ids=[obs], via="dashboard"),
            chatroom_id=room,
            principal=caller,
            db=SimpleNamespace(),
        )
        assert gated == [room]
        assert recorded[0]["via"] is AlertViewSurface.DASHBOARD
        assert recorded[0]["actor_user_id"] == caller.user_id

    def test_the_batch_is_bounded(self) -> None:
        import app.api.v1.observations as mod

        with pytest.raises(ValueError, match="at most 50"):
            mod.AlertViewsIn(observation_ids=[uuid.uuid4() for _ in range(51)], via="panel")


class TestAlertsCsv:
    """AC-6."""

    def test_columns_latencies_and_codes(self) -> None:
        room, agent = uuid.uuid4(), uuid.uuid4()
        raised = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
        files = ResearchExportBuilder().build_files(
            workspace_id=uuid.uuid4(),
            room_count=1,
            submissions=[],
            observations=[],
            messages=[],
            agent_key_map={agent: "AA analyst"},
            alerts=[
                AlertRow(
                    chatroom_id=room,
                    agent_id=agent,
                    alert_reason="=stuck group",
                    created_at=raised,
                    first_viewed_at=raised + timedelta(seconds=42),
                    first_viewed_via="panel",
                    first_action_at=raised + timedelta(seconds=90),
                    first_action_kind="room_message",
                )
            ],
        )
        content = files["alerts.csv"]
        assert content.startswith("﻿")
        rows = list(csv.DictReader(io.StringIO(content.lstrip("﻿"))))
        assert rows[0]["room_code"] == f"r:{str(room)[:8]}"
        assert rows[0]["agent_key"] == "AA analyst"
        assert rows[0]["alert_reason"] == "'=stuck group"
        assert rows[0]["view_latency_s"] == "42.0"
        assert rows[0]["action_latency_s"] == "90.0"
        assert rows[0]["first_action_kind"] == "room_message"
        assert str(room) not in content
        assert '"alert_count": 1' in files["manifest.json"]

    def test_an_export_without_alerts_still_has_the_header(self) -> None:
        files = ResearchExportBuilder().build_files(
            workspace_id=uuid.uuid4(),
            room_count=0,
            submissions=[],
            observations=[],
            messages=[],
            agent_key_map={},
        )
        assert files["alerts.csv"].lstrip("﻿").startswith("room_code,agent_key,alert_reason,raised_at")


class TestDashboardAlerts:
    """AC-5: alerts only from rooms the caller created."""

    async def test_only_own_rooms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.api.v1.dashboard as mod

        me = uuid.uuid4()
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        asked: list[Any] = []

        class _Conv:
            def __init__(self, db: Any) -> None:
                pass

            async def get_workspace(self, wid: Any) -> Any:
                return SimpleNamespace(project_id=uuid.uuid4(), deleted_at=None)

            async def visible_rooms_in_workspace(self, **_: Any) -> Any:
                return [
                    SimpleNamespace(id=mine, name="mine", created_by_user_id=me),
                    SimpleNamespace(id=theirs, name="theirs", created_by_user_id=uuid.uuid4()),
                ]

            async def list_alerts_for_rooms(self, **kw: Any) -> Any:
                asked.append(kw["chatroom_ids"])
                return [
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        chatroom_id=mine,
                        agent_id=uuid.uuid4(),
                        alert_reason="stuck",
                        created_at=None,
                        first_viewed_at=None,
                        first_action_at=None,
                        first_action_kind=None,
                    )
                ]

        class _Tenancy:
            def __init__(self, db: Any) -> None:
                pass

            async def get_project(self, pid: Any) -> Any:
                return object()

        class _Agents:
            def __init__(self, db: Any) -> None:
                pass

            async def agent_names(self, ids: Any) -> dict[Any, str]:
                return {}

        monkeypatch.setattr(mod, "ConversationFacade", _Conv)
        monkeypatch.setattr(mod, "TenancyFacade", _Tenancy)
        monkeypatch.setattr(mod, "AgentsFacade", _Agents)
        monkeypatch.setattr(mod, "assert_project_role", AsyncMock())
        out = await mod.dashboard_alerts(
            workspace_id=uuid.uuid4(), limit=50, offset=0, principal=_principal(me), db=SimpleNamespace()
        )
        assert asked == [[mine]]
        assert [a.room_name for a in out.alerts] == ["mine"]
