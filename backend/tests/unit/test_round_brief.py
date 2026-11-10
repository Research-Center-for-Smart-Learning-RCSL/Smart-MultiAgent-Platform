"""The round brief of an activation ([R30.21]).

AC-2 and AC-4 of docs/tasks/2026-11-10-crat-lab-trial-fixes: the normaliser, and
the service seam both start paths share.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import activation_service
from contexts.activities.application.activation_service import ActivationService
from contexts.activities.domain.errors import RoundBriefInvalid
from contexts.activities.domain.models import (
    ActivationNotice,
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ValidatorKind,
)
from contexts.activities.domain.round_brief import normalize_round_brief

_NOW = dt.datetime(2026, 11, 10, tzinfo=dt.UTC)


class TestNormalize:
    @pytest.mark.parametrize("raw", [None, "", "   ", "\n \r\n"])
    def test_blank_is_no_brief(self, raw: str | None) -> None:
        assert normalize_round_brief(raw) is None

    def test_trims_lines_and_unifies_breaks(self) -> None:
        assert normalize_round_brief("  水 火 山 \r\n\r\n 提示 \n") == "水 火 山\n\n提示"

    def test_two_hundred_characters_is_the_limit(self) -> None:
        assert normalize_round_brief("字" * 200) == "字" * 200
        with pytest.raises(RoundBriefInvalid, match="200"):
            normalize_round_brief("字" * 201)

    def test_three_lines_is_the_limit(self) -> None:
        assert normalize_round_brief("a\nb\nc") == "a\nb\nc"
        with pytest.raises(RoundBriefInvalid, match="3 lines"):
            normalize_round_brief("a\nb\nc\nd")

    @pytest.mark.parametrize("ch", ["\x00", "\x1b", "\t", "\u202e", "\u200b", "\u2028"])
    def test_control_and_format_characters_are_refused(self, ch: str) -> None:
        with pytest.raises(RoundBriefInvalid, match="control"):
            normalize_round_brief(f"水{ch}火")


def _type(project_id: uuid.UUID, type_id: uuid.UUID) -> ActivityType:
    return ActivityType(
        id=type_id,
        project_id=project_id,
        key="crat",
        name="CRAT",
        payload_schema={},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "quiz"},
        retention_days=None,
        version=1,
        created_at=_NOW,
    )


def _activation(room_id: uuid.UUID, type_id: uuid.UUID, brief: str | None = None) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=room_id,
        activity_type_id=type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
        brief=brief,
    )


def _service(repo: MagicMock, project_id: uuid.UUID, type_id: uuid.UUID) -> ActivationService:
    svc = ActivationService(
        MagicMock(),
        time_limits=MagicMock(for_room=AsyncMock(return_value={})),
        activation_repo=repo,
        type_repo=MagicMock(get=AsyncMock(return_value=_type(project_id, type_id))),
    )
    svc._policy._repo = MagicMock()
    svc._policy._repo.get_platform = AsyncMock(return_value=None)

    async def _write(**kw: object) -> ActivationNotice:
        return ActivationNotice(message_id=uuid.uuid4(), chatroom_id=kw["chatroom_id"], created_at=_NOW)  # type: ignore[arg-type]

    svc._notices = MagicMock(write=AsyncMock(side_effect=_write))
    return svc


class TestServiceStart:
    async def test_the_normalised_brief_is_stored_and_audited_by_length_only(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        created = _activation(room_id, type_id, brief="水 火 山")
        repo = MagicMock(
            create_active=AsyncMock(return_value=created.id), get=AsyncMock(return_value=created)
        )
        svc = _service(repo, project_id, type_id)

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            result = await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
                brief="  水 火 山  ",
            )

        assert repo.create_active.await_args.kwargs["brief"] == "水 火 山"
        assert result.already_running is False
        metadata = emit.await_args.args[1].metadata
        assert metadata["brief_chars"] == "5"
        assert "水" not in str(metadata)

    async def test_a_round_without_a_brief_audits_no_brief_key(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        created = _activation(room_id, type_id)
        repo = MagicMock(
            create_active=AsyncMock(return_value=created.id), get=AsyncMock(return_value=created)
        )
        svc = _service(repo, project_id, type_id)

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert repo.create_active.await_args.kwargs["brief"] is None
        assert "brief_chars" not in emit.await_args.args[1].metadata

    async def test_a_refused_brief_starts_nothing(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = MagicMock(create_active=AsyncMock())
        svc = _service(repo, project_id, type_id)

        with pytest.raises(RoundBriefInvalid):
            await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
                brief="x" * 201,
            )
        repo.create_active.assert_not_awaited()

    async def test_a_repeat_start_keeps_the_original_brief_and_says_so(self) -> None:
        """AC-4: the brief is immutable; the result tells the caller nothing began."""
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        running = _activation(room_id, type_id, brief="first")
        repo = MagicMock(
            create_active=AsyncMock(return_value=None), get_active=AsyncMock(return_value=running)
        )
        svc = _service(repo, project_id, type_id)

        result = await svc.start(
            project_id=project_id,
            chatroom_id=room_id,
            activity_type_id=type_id,
            started_by_user_id=uuid.uuid4(),
            actor_ip=None,
            brief="second",
        )

        assert result.already_running is True
        assert result.notice is None
        assert result.activation.brief == "first"
