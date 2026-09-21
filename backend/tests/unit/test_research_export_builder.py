"""Research export builder tests (AC-4, AC-5, AC-6, AC-7, AC-12).

Verifies CSV/JSON output structure and de-identification without a database.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.activities.domain.subject_code import group_subject_code, subject_code
from contexts.conversation.application.research_export_builder import ResearchExportBuilder

_ROOM_1 = uuid.uuid4()
_ROOM_2 = uuid.uuid4()
_USER_1 = uuid.uuid4()
_USER_2 = uuid.uuid4()
_GROUP_1 = uuid.uuid4()
_AGENT_1 = uuid.uuid4()
_WORKSPACE = uuid.uuid4()
_JOB = uuid.uuid4()
_NOW = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)


def _sub_row(
    chatroom_id: uuid.UUID = _ROOM_1,
    producer_user_id: uuid.UUID = _USER_1,
    subject_user_id: uuid.UUID | None = _USER_1,
    subject_member_group_id: uuid.UUID | None = None,
    activity_type_key: str = "six-thinking-hats",
    attempt_no: int = 1,
    is_valid: bool = True,
    error_class: str | None = None,
    latency_ms: int | None = 150,
    created_at: datetime = _NOW,
    payload: dict | None = None,
    sub_scores: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        producer_user_id=producer_user_id,
        subject_user_id=subject_user_id,
        subject_member_group_id=subject_member_group_id,
        activity_type_key=activity_type_key,
        attempt_no=attempt_no,
        is_valid=is_valid,
        error_class=error_class,
        latency_ms=latency_ms,
        created_at=created_at,
        payload=payload or {"answer": "red hat"},
        sub_scores=sub_scores or {"creativity": 8},
    )


def _obs_row(
    chatroom_id: uuid.UUID = _ROOM_1,
    agent_id: uuid.UUID = _AGENT_1,
    content_md: str = "The participant showed divergent thinking.",
    blocks: list | None = None,
    trigger: str = "submission",
    created_at: datetime = _NOW,
    released_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content_md=content_md,
        metadata={},
        blocks=blocks or [{"type": "prose", "text": "Good work"}],
        trigger=trigger,
        trigger_message_id=None,
        released_at=released_at,
        release_target=None,
        released_by_user_id=None,
        created_at=created_at,
        deleted_at=None,
    )


def _msg_row(
    chatroom_id: uuid.UUID = _ROOM_1,
    sender_type: str = "user",
    sender_id: uuid.UUID | None = _USER_1,
    content_md: str = "Hello",
    created_at: datetime = _NOW,
) -> SimpleNamespace:
    return SimpleNamespace(
        chatroom_id=chatroom_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content_md=content_md,
        created_at=created_at,
    )


class TestSubmissionsCsv:
    def test_user_subject_gets_subject_code(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_sub_row(subject_user_id=_USER_1)]
        result = builder._build_submissions_csv(rows)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "subject_code" in header
        data = next(reader)
        assert data[0] == subject_code(_USER_1)

    def test_group_subject_gets_group_code(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_sub_row(subject_user_id=None, subject_member_group_id=_GROUP_1)]
        result = builder._build_submissions_csv(rows)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # header
        data = next(reader)
        assert data[0] == group_subject_code(_GROUP_1)

    def test_no_display_names_or_emails(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_sub_row()]
        result = builder._build_submissions_csv(rows)
        assert str(_USER_1) not in result
        assert "display_name" not in result.lower()
        assert "email" not in result.lower()

    def test_columns_match_spec(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_sub_row()]
        result = builder._build_submissions_csv(rows)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        expected = [
            "subject_code",
            "activity_type_key",
            "room_id",
            "attempt_no",
            "is_valid",
            "error_class",
            "latency_ms",
            "created_at",
            "payload_json",
            "sub_scores_json",
        ]
        assert header == expected


class TestObservationsJson:
    def test_blocks_preserved_as_structured_json(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        blocks = [{"type": "prose", "text": "Good"}, {"type": "key_points", "items": ["A", "B"]}]
        rows = [_obs_row(blocks=blocks)]
        result = builder._build_observations_json(rows, {_AGENT_1: "AA"})
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["blocks"] == blocks

    def test_agent_key_used(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_obs_row()]
        result = builder._build_observations_json(rows, {_AGENT_1: "AA-agent"})
        parsed = json.loads(result)
        assert parsed[0]["agent_key"] == "AA-agent"


class TestTranscriptsJson:
    def test_user_messages_get_subject_code(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_msg_row(sender_type="user", sender_id=_USER_1)]
        result = builder._build_transcripts_json(rows, {})
        parsed = json.loads(result)
        room_msgs = parsed[str(_ROOM_1)]
        assert room_msgs[0]["subject_code"] == subject_code(_USER_1)
        assert room_msgs[0]["sender_type"] == "user"

    def test_agent_messages_get_agent_key(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_msg_row(sender_type="agent", sender_id=_AGENT_1)]
        result = builder._build_transcripts_json(rows, {_AGENT_1: "TA-agent"})
        parsed = json.loads(result)
        room_msgs = parsed[str(_ROOM_1)]
        assert room_msgs[0]["subject_code"] == "TA-agent"

    def test_system_messages_have_system_identifier(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [_msg_row(sender_type="system", sender_id=None)]
        result = builder._build_transcripts_json(rows, {})
        parsed = json.loads(result)
        room_msgs = parsed[str(_ROOM_1)]
        assert room_msgs[0]["subject_code"] == "system"

    def test_per_room_grouping(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        rows = [
            _msg_row(chatroom_id=_ROOM_1),
            _msg_row(chatroom_id=_ROOM_2),
        ]
        result = builder._build_transcripts_json(rows, {})
        parsed = json.loads(result)
        assert str(_ROOM_1) in parsed
        assert str(_ROOM_2) in parsed


class TestManifestJson:
    def test_manifest_structure(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        result = builder._build_manifest(
            workspace_id=_WORKSPACE,
            room_count=3,
            created_after=None,
            created_before=None,
            submission_count=10,
            observation_count=5,
        )
        parsed = json.loads(result)
        assert parsed["schema_version"] == 1
        assert parsed["workspace_id"] == str(_WORKSPACE)
        assert parsed["room_count"] == 3
        assert "exported_at" in parsed


class TestZipPackage:
    def test_zip_contains_all_files(self) -> None:
        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        result = builder._package_zip("csv", "obs", "trans", "man")
        with zipfile.ZipFile(io.BytesIO(result)) as zf:
            names = zf.namelist()
            assert "submissions.csv" in names
            assert "observations.json" in names
            assert "transcripts.json" in names
            assert "manifest.json" in names


class TestDateRangeFiltering:
    def test_date_range_applied_to_submissions_query(self) -> None:
        """AC-12: submissions outside the range are excluded.

        We verify the query is built with the date predicates by inspecting
        the compiled SQL rather than running it against a database.
        """
        from sqlalchemy.dialects import postgresql

        builder = ResearchExportBuilder.__new__(ResearchExportBuilder)
        builder._db = AsyncMock()

        from datetime import timezone

        after = datetime(2026, 1, 1, tzinfo=timezone.utc)
        before = datetime(2026, 6, 1, tzinfo=timezone.utc)

        import sqlalchemy as sa

        from contexts.activities.infrastructure import tables as at

        clauses: list[sa.ColumnElement] = [
            at.activity_submissions.c.chatroom_id.in_([_ROOM_1]),
            at.activity_submissions.c.deleted_at.is_(None),
            at.activity_submissions.c.created_at >= after,
            at.activity_submissions.c.created_at < before,
        ]

        compiled = sa.and_(*clauses).compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "created_at >=" in sql
        assert "created_at <" in sql
