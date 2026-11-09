"""Research export builder tests.

Bundle shape: docs/tasks/2026-11-08-research-data-integrity/spec.md section 5
(AC-7 to AC-9). Verifies CSV/JSON output structure and de-identification without
a database.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from contexts.activities.domain.subject_code import (
    group_subject_code,
    room_code,
    subject_code,
    workspace_code,
)
from contexts.conversation.application.research_export_builder import (
    MessageRow,
    ObservationRow,
    ResearchExportBuilder,
    SubmissionRow,
)

_ROOM_1 = uuid.uuid4()
_ROOM_2 = uuid.uuid4()
_USER_1 = uuid.uuid4()
_GUEST_1 = uuid.uuid4()
_GUEST_2 = uuid.uuid4()
_GROUP_1 = uuid.uuid4()
_AGENT_1 = uuid.uuid4()
_WORKSPACE = uuid.uuid4()
_NOW = datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _sub_row(
    chatroom_id: uuid.UUID = _ROOM_1,
    subject_user_id: uuid.UUID | None = _USER_1,
    subject_member_group_id: uuid.UUID | None = None,
    subject_kind: str = "user",
    activity_type_key: str = "six-thinking-hats",
    attempt_no: int = 1,
    is_valid: bool = True,
    error_class: str | None = None,
    latency_ms: int | None = 150,
    created_at: datetime = _NOW,
    payload: dict | None = None,
    sub_scores: dict | None = None,
    payload_fields: tuple[str, ...] = ("answer",),
) -> SubmissionRow:
    return SubmissionRow(
        chatroom_id=chatroom_id,
        subject_user_id=subject_user_id,
        subject_member_group_id=subject_member_group_id,
        activity_type_key=activity_type_key,
        attempt_no=attempt_no,
        is_valid=is_valid,
        error_class=error_class,
        latency_ms=latency_ms,
        created_at=created_at,
        payload=payload or {"answer": "red hat"},
        sub_scores=sub_scores or {"filled": 1},
        subject_kind=subject_kind,
        payload_fields=payload_fields,
    )


def _obs_row(chatroom_id: uuid.UUID = _ROOM_1, blocks: list | None = None) -> ObservationRow:
    return ObservationRow(
        chatroom_id=chatroom_id,
        agent_id=_AGENT_1,
        content_md="The participant showed divergent thinking.",
        blocks=blocks or [{"type": "prose", "text": "Good work"}],
        trigger="submission",
        created_at=_NOW,
    )


def _msg_row(
    chatroom_id: uuid.UUID = _ROOM_1,
    sender_type: str = "user",
    sender_id: uuid.UUID | None = _USER_1,
    content_md: str = "Hello",
) -> MessageRow:
    return MessageRow(
        chatroom_id=chatroom_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content_md=content_md,
        created_at=_NOW,
    )


def _files(
    submissions: list[SubmissionRow] | None = None,
    messages: list[MessageRow] | None = None,
    observations: list[ObservationRow] | None = None,
) -> dict[str, str]:
    return ResearchExportBuilder().build_files(
        workspace_id=_WORKSPACE,
        room_count=2,
        submissions=submissions if submissions is not None else [_sub_row()],
        observations=observations if observations is not None else [_obs_row()],
        messages=messages if messages is not None else [_msg_row()],
        agent_key_map={_AGENT_1: "TA-agent"},
    )


def _csv(content: str) -> list[dict[str, str]]:
    assert content.startswith("\ufeff")
    return list(csv.DictReader(io.StringIO(content.removeprefix("\ufeff"))))


def _header(content: str) -> list[str]:
    return next(csv.reader(io.StringIO(content.removeprefix("\ufeff"))))


class TestSubjects:
    """AC-7: every subject keeps its own code, the same in every file."""

    def test_guests_get_distinct_codes_that_join_transcripts_to_submissions(self) -> None:
        subs = [
            _sub_row(subject_user_id=_GUEST_1, subject_kind="guest"),
            _sub_row(subject_user_id=_GUEST_2, subject_kind="guest"),
            _sub_row(subject_user_id=_USER_1, subject_kind="user"),
            _sub_row(subject_user_id=None, subject_member_group_id=_GROUP_1),
        ]
        msgs = [
            _msg_row(sender_type="guest", sender_id=_GUEST_1),
            _msg_row(sender_type="guest", sender_id=_GUEST_2),
            _msg_row(sender_type="user", sender_id=_USER_1),
        ]
        files = _files(subs, msgs)

        rows = _csv(files["submissions/six-thinking-hats.csv"])
        assert [(r["subject_code"], r["subject_kind"]) for r in rows] == [
            (subject_code(_GUEST_1), "guest"),
            (subject_code(_GUEST_2), "guest"),
            (subject_code(_USER_1), "user"),
            (group_subject_code(_GROUP_1), "group"),
        ]
        said = json.loads(files["transcripts.json"])[room_code(_ROOM_1)]
        assert [(m["subject_code"], m["subject_kind"]) for m in said] == [
            (subject_code(_GUEST_1), "guest"),
            (subject_code(_GUEST_2), "guest"),
            (subject_code(_USER_1), "user"),
        ]
        assert subject_code(_GUEST_1) != subject_code(_GUEST_2)

    def test_agent_and_system_messages_are_labelled_by_kind(self) -> None:
        msgs = [
            _msg_row(sender_type="agent", sender_id=_AGENT_1),
            _msg_row(sender_type="system", sender_id=None),
        ]
        said = json.loads(_files(messages=msgs)["transcripts.json"])[room_code(_ROOM_1)]
        assert [(m["subject_code"], m["subject_kind"]) for m in said] == [
            ("TA-agent", "agent"),
            ("system", "system"),
        ]


class TestNoFullIds:
    """AC-8: no 36-character UUID in any file; rooms and the workspace are codes."""

    def test_no_uuid_anywhere_in_the_unpacked_zip(self) -> None:
        subs = [
            _sub_row(chatroom_id=_ROOM_1, subject_user_id=_GUEST_1, subject_kind="guest"),
            _sub_row(chatroom_id=_ROOM_2, subject_user_id=None, subject_member_group_id=_GROUP_1),
        ]
        msgs = [_msg_row(chatroom_id=_ROOM_2, sender_type="guest", sender_id=_GUEST_1)]
        archive = ResearchExportBuilder()._package_zip(_files(subs, msgs, [_obs_row(_ROOM_2)]))
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for name in zf.namelist():
                text = zf.read(name).decode("utf-8")
                assert not _UUID.search(text), f"{name} carries a full id"

    def test_rooms_sharing_a_code_prefix_stay_apart(self) -> None:
        a = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
        b = uuid.UUID("abcdef01-0000-4000-8000-000000000002")
        msgs = [_msg_row(chatroom_id=a), _msg_row(chatroom_id=b)]
        files = _files([_sub_row(chatroom_id=b)], msgs, [])
        assert sorted(json.loads(files["transcripts.json"])) == ["r:abcdef01~1", "r:abcdef01~2"]
        assert _csv(files["submissions/six-thinking-hats.csv"])[0]["room_code"] == "r:abcdef01~2"

    def test_rooms_and_workspace_read_as_codes(self) -> None:
        files = _files(observations=[_obs_row(_ROOM_2)])
        assert _csv(files["submissions/six-thinking-hats.csv"])[0]["room_code"] == room_code(_ROOM_1)
        assert json.loads(files["observations.json"])[0]["room_code"] == room_code(_ROOM_2)
        assert list(json.loads(files["transcripts.json"])) == [room_code(_ROOM_1)]
        manifest = json.loads(files["manifest.json"])
        assert manifest["schema_version"] == 3
        assert manifest["workspace_code"] == workspace_code(_WORKSPACE)
        assert "workspace_id" not in manifest
        assert room_code(_ROOM_1) == f"r:{str(_ROOM_1)[:8]}"


class TestSubmissionCsvs:
    """AC-9: one CSV per type, section 5's columns, readable CJK, a BOM."""

    def test_one_file_per_type_listed_in_the_manifest(self) -> None:
        files = _files([_sub_row(activity_type_key="RAT test"), _sub_row(activity_type_key="mandala")])
        assert {p for p in files if p.startswith("submissions/")} == {
            "submissions/rat-test.csv",
            "submissions/mandala.csv",
        }
        assert json.loads(files["manifest.json"])["submission_files"] == [
            {"type_key": "RAT test", "file": "submissions/rat-test.csv"},
            {"type_key": "mandala", "file": "submissions/mandala.csv"},
        ]

    def test_two_types_sharing_a_key_get_their_own_files(self) -> None:
        """Keys are unique only per project among live types (0076)."""
        platform, project = uuid.uuid4(), uuid.uuid4()
        rows = [
            replace(
                _sub_row(activity_type_key="rat", payload={"a": 1}, payload_fields=("a",)),
                activity_type_id=platform,
            ),
            replace(
                _sub_row(activity_type_key="rat", payload={"b": 2}, payload_fields=("b",)),
                activity_type_id=project,
            ),
        ]
        files = _files(rows)
        assert _header(files["submissions/rat.csv"])[9] == "a"
        assert _header(files["submissions/rat-2.csv"])[9] == "b"

    def test_type_keys_that_sanitise_alike_do_not_overwrite_each_other(self) -> None:
        files = _files([_sub_row(activity_type_key="a b"), _sub_row(activity_type_key="a.b")])
        assert {p for p in files if p.startswith("submissions/")} == {
            "submissions/a-b.csv",
            "submissions/a-b-2.csv",
        }

    def test_columns_match_spec(self) -> None:
        """Replaces the schema-v1 header test: section 5's order, payload fields in the middle."""
        row = _sub_row(payload={"answer": "x", "reason": "y"}, payload_fields=("answer", "reason"))
        assert _header(_files([row])["submissions/six-thinking-hats.csv"]) == [
            "subject_code",
            "subject_kind",
            "room_code",
            "type_version",
            "attempt_no",
            "is_valid",
            "error_class",
            "latency_ms",
            "created_at",
            "answer",
            "reason",
            "sub_scores_filled",
            "sub_scores_filled_fields",
            "judge_status",
            "fluency",
            "flexibility",
            "originality",
            "convergence",
            "judge_model",
            "payload_json",
            "sub_scores_json",
        ]

    def test_payload_fields_are_the_union_in_first_seen_order(self) -> None:
        rows = [
            _sub_row(payload={"b": 1}, payload_fields=("b", "a")),
            _sub_row(payload={"c": 2}, payload_fields=("a", "c")),
        ]
        header = _header(_files(rows)["submissions/six-thinking-hats.csv"])
        assert header[9:12] == ["b", "a", "c"]

    def test_chinese_stays_readable_and_the_file_starts_with_a_bom(self) -> None:
        content = _files([_sub_row(payload={"answer": "林"})])["submissions/six-thinking-hats.csv"]
        assert content.startswith("\ufeff")
        (row,) = _csv(content)
        assert row["answer"] == "林"
        assert row["payload_json"] == '{"answer": "林"}'
        assert "\\u" not in content

    def test_arrays_join_objects_stay_json_and_fill_counts_are_split_out(self) -> None:
        row = _sub_row(
            payload={"ideas": ["紅", "黃"], "meta": {"k": "值"}},
            payload_fields=("ideas", "meta"),
            sub_scores={"filled": 2, "filled_fields": ["ideas", "meta"]},
        )
        (out,) = _csv(_files([row])["submissions/six-thinking-hats.csv"])
        assert out["ideas"] == "紅; 黃"
        assert out["meta"] == '{"k": "值"}'
        assert (out["sub_scores_filled"], out["sub_scores_filled_fields"]) == ("2", "ideas; meta")

    def test_a_cell_that_would_be_a_spreadsheet_formula_is_neutralised(self) -> None:
        evil = '=HYPERLINK("http://x","y")'
        row = _sub_row(
            payload={"answer": evil, "+sum": "x"},
            payload_fields=("answer", "+sum"),
            error_class="@cmd",
            sub_scores={"filled": "-2+3", "filled_fields": ["=A1"]},
        )
        content = _files([row])["submissions/six-thinking-hats.csv"]
        assert "'+sum" in _header(content)
        (out,) = _csv(content)
        assert out["answer"] == "'" + evil
        assert out["error_class"] == "'@cmd"
        assert out["sub_scores_filled"] == "'-2+3"
        assert out["sub_scores_filled_fields"] == "'=A1"
        assert out["'+sum"] == "x"

    def test_submission_csv_golden(self) -> None:
        """Replaces the schema-v1 golden. The subject codes are unchanged from it
        (teacher dashboard redesign AC-14); the room is now a code."""
        u = uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001")
        g = uuid.UUID("5e6f7a8b-0000-4000-8000-000000000002")
        r = uuid.UUID("9c0d1e2f-0000-4000-8000-000000000003")
        at = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
        rows = [
            SubmissionRow(
                chatroom_id=r,
                subject_user_id=u,
                subject_member_group_id=None,
                activity_type_key="rat",
                attempt_no=1,
                is_valid=True,
                error_class=None,
                latency_ms=120,
                created_at=at,
                payload={"a": "x"},
                sub_scores={},
                payload_fields=("a",),
                type_version=3,
                judge_status="scored",
                judge_scores={
                    "fluency": 1,
                    "flexibility": 2,
                    "originality": 3,
                    "convergence": 0,
                    "rationales": {"originality": "not exported"},
                    "model": "judge-m",
                },
            ),
            SubmissionRow(
                chatroom_id=r,
                subject_user_id=None,
                subject_member_group_id=g,
                activity_type_key="rat",
                attempt_no=2,
                is_valid=False,
                error_class="schema",
                latency_ms=None,
                created_at=at,
                payload={},
                sub_scores={},
                payload_fields=("a",),
            ),
        ]
        assert _files(rows)["submissions/rat.csv"] == (
            "\ufeffsubject_code,subject_kind,room_code,type_version,attempt_no,is_valid,error_class,latency_ms,"
            "created_at,a,sub_scores_filled,sub_scores_filled_fields,judge_status,fluency,flexibility,"
            "originality,convergence,judge_model,payload_json,sub_scores_json\r\n"
            "u:1a2b3c4d,user,r:9c0d1e2f,3,1,True,,120,"
            '2026-10-19T09:30:00+00:00,x,,,scored,1,2,3,0,judge-m,"{""a"": ""x""}",{}\r\n'
            "g:5e6f7a8b,group,r:9c0d1e2f,unknown,2,False,schema,,"
            "2026-10-19T09:30:00+00:00,,,,,,,,,,{},{}\r\n"
        )

    @pytest.mark.parametrize("status", ["skipped", "error", "pending"])
    def test_an_unscored_row_has_its_status_and_no_numbers(self, status: str) -> None:
        """An empty score cell must not read as a zero, and a stale document is not trusted."""
        row = replace(
            _sub_row(), judge_status=status, judge_scores={"fluency": 4, "originality": 4, "model": "m"}
        )
        (out,) = _csv(_files([row])["submissions/six-thinking-hats.csv"])
        assert out["judge_status"] == status
        assert [out[c] for c in ("fluency", "flexibility", "originality", "convergence", "judge_model")] == [
            ""
        ] * 5

    def test_an_unproven_type_version_reads_unknown(self) -> None:
        """AC-12: a NULL version is exported as unknown, never as a guess."""
        known, unproven = _csv(
            _files([replace(_sub_row(), type_version=2), _sub_row()])["submissions/six-thinking-hats.csv"]
        )
        assert (known["type_version"], unproven["type_version"]) == ("2", "unknown")
        notes = " ".join(json.loads(_files()["manifest.json"])["notes"])
        assert "type_version" in notes

    def test_no_display_names_or_emails(self) -> None:
        content = _files()["submissions/six-thinking-hats.csv"]
        assert str(_USER_1) not in content
        assert "display_name" not in content.lower()
        assert "email" not in content.lower()


class TestObservationsJson:
    def test_blocks_preserved_as_structured_json(self) -> None:
        blocks = [{"type": "prose", "text": "Good"}, {"type": "key_points", "items": ["A", "B"]}]
        parsed = json.loads(_files(observations=[_obs_row(blocks=blocks)])["observations.json"])
        assert parsed[0]["blocks"] == blocks
        assert parsed[0]["agent_key"] == "TA-agent"


class TestZipPackage:
    def test_zip_holds_every_file(self) -> None:
        files = _files()
        archive = ResearchExportBuilder()._package_zip(files)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            assert set(zf.namelist()) == set(files)
            assert {"observations.json", "transcripts.json", "manifest.json"} <= set(zf.namelist())
