"""Research data export builder (R33.10).

Serializes pre-queried submissions, observations and messages into a
de-identified ZIP file (CSV + JSON) and uploads it to MinIO.

Data is received as typed dicts from the worker task, which queries through
the proper facades. This module never imports from another context's
infrastructure layer.

Bundle layout (manifest ``schema_version`` 2,
docs/tasks/2026-11-08-research-data-integrity/spec.md section 5):
``submissions/<type_key>.csv`` per activity type, ``observations.json``,
``transcripts.json`` and ``manifest.json``. People, groups, rooms and the
workspace appear only as short codes, never as full ids.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from contexts.activities.domain.models import FILLED_FIELDS_SUB_SCORE
from contexts.activities.domain.subject_code import (
    group_subject_code,
    room_code,
    subject_code,
    workspace_code,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.storage import export_key, get_minio_client

_EXPORT_PUT_TIMEOUT_SECONDS = 120
SCHEMA_VERSION: Final = 2
# Excel reads a CSV as the system code page unless it starts with a BOM.
_BOM: Final = "\ufeff"
_LEADING_COLUMNS: Final = (
    "subject_code",
    "subject_kind",
    "room_code",
    "type_version",
    "attempt_no",
    "is_valid",
    "error_class",
    "latency_ms",
    "created_at",
)
_SCORE_COLUMNS: Final = ("fluency", "flexibility", "originality", "convergence")
_TRAILING_COLUMNS: Final = (
    "sub_scores_filled",
    "sub_scores_filled_fields",
    "judge_status",
    *_SCORE_COLUMNS,
    "judge_model",
    "payload_json",
    "sub_scores_json",
)
# A cell starting with one of these is a formula to a spreadsheet. Participant
# text reaches these cells, and the files are meant to be opened in Excel.
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
_UNSAFE_FILE_CHARS: Final = re.compile(r"[^a-z0-9_-]+")
_MANIFEST_NOTES: Final = (
    "People, groups, rooms and the workspace appear only as codes. Free text (answers, "
    "messages, observations) is exported as written and is not scrubbed of names.",
    "A CSV text cell starting with =, +, -, @, a tab or a carriage return is prefixed "
    "with an apostrophe so a spreadsheet does not run it as a formula; strip it before "
    "parsing such a value as a number.",
    "type_version is the activity type's version when the submission was made. It reads "
    "unknown for a submission made before versions were recorded, under a type that had "
    "already been edited.",
    "judge_status is empty for a type without creativity scoring. The four scores and "
    "judge_model are filled only where judge_status is scored. fluency counts the answered "
    "fields; flexibility, originality and convergence are 0 to 4 from the judge model "
    "named in judge_model.",
)
_UNKNOWN_VERSION: Final = "unknown"


@dataclass(frozen=True, slots=True)
class SubmissionRow:
    chatroom_id: uuid.UUID
    subject_user_id: uuid.UUID | None
    subject_member_group_id: uuid.UUID | None
    activity_type_key: str
    attempt_no: int
    is_valid: bool | None
    error_class: str | None
    latency_ms: int | None
    created_at: datetime | None
    payload: dict[str, Any] = field(default_factory=dict)
    sub_scores: dict[str, Any] = field(default_factory=dict)
    subject_kind: str = "user"
    payload_fields: tuple[str, ...] = ()
    activity_type_id: uuid.UUID | None = None
    type_version: int | None = None
    judge_status: str | None = None
    judge_scores: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ObservationRow:
    chatroom_id: uuid.UUID
    agent_id: uuid.UUID
    content_md: str
    trigger: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    released_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MessageRow:
    chatroom_id: uuid.UUID
    sender_type: str
    sender_id: uuid.UUID | None
    content_md: str
    created_at: datetime | None = None


class ResearchExportBuilder:
    async def build_and_upload(
        self,
        *,
        job_id: uuid.UUID,
        workspace_id: uuid.UUID,
        room_ids: list[uuid.UUID],
        owner_user_id: uuid.UUID,
        submissions: list[SubmissionRow],
        observations: list[ObservationRow],
        messages: list[MessageRow],
        agent_key_map: dict[uuid.UUID, str],
        db: Any,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        actor_ip: str | None = None,
    ) -> tuple[str, str]:
        files = self.build_files(
            workspace_id=workspace_id,
            room_count=len(room_ids),
            submissions=submissions,
            observations=observations,
            messages=messages,
            agent_key_map=agent_key_map,
            created_after=created_after,
            created_before=created_before,
        )
        zip_bytes = self._package_zip(files)

        client = get_minio_client()
        key = export_key(job_id=job_id, filename="research-data.zip")

        try:
            await asyncio.wait_for(
                client.put_object(
                    bucket=client.exports_bucket,
                    key=key,
                    data=zip_bytes,
                    content_type="application/zip",
                ),
                timeout=_EXPORT_PUT_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"research export MinIO put timed out after {_EXPORT_PUT_TIMEOUT_SECONDS}s (job {job_id})"
            ) from exc

        await audit.emit(
            db,
            audit.AuditEvent(
                action="research_data.exported",
                actor_user_id=owner_user_id,
                actor_ip=actor_ip,
                resource_type="workspace",
                resource_id=workspace_id,
                metadata={
                    "job_id": str(job_id),
                    "room_count": len(room_ids),
                    "submission_count": len(submissions),
                    "observation_count": len(observations),
                    "created_after": (created_after.isoformat() if created_after else None),
                    "created_before": (created_before.isoformat() if created_before else None),
                },
            ),
        )

        return client.exports_bucket, key

    def build_files(
        self,
        *,
        workspace_id: uuid.UUID,
        room_count: int,
        submissions: list[SubmissionRow],
        observations: list[ObservationRow],
        messages: list[MessageRow],
        agent_key_map: dict[uuid.UUID, str],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, str]:
        """Every file of the bundle by its path inside the ZIP."""
        rooms = _room_codes(
            [r.chatroom_id for r in submissions]
            + [o.chatroom_id for o in observations]
            + [m.chatroom_id for m in messages]
        )
        csvs = self._build_submission_csvs(submissions, rooms)
        return {
            **{path: content for path, (_, content) in csvs.items()},
            "observations.json": self._build_observations_json(observations, agent_key_map, rooms),
            "transcripts.json": self._build_transcripts_json(messages, agent_key_map, rooms),
            "manifest.json": self._build_manifest(
                workspace_id=workspace_id,
                room_count=room_count,
                created_after=created_after,
                created_before=created_before,
                submission_count=len(submissions),
                observation_count=len(observations),
                submission_files=[{"type_key": key, "file": path} for path, (key, _) in csvs.items()],
            ),
        }

    # -- Serialization ------------------------------------------------------ #

    def _build_submission_csvs(
        self, rows: list[SubmissionRow], rooms: dict[uuid.UUID, str]
    ) -> dict[str, tuple[str, str]]:
        """One CSV per activity type: ``{path: (type_key, content)}``."""
        by_type: dict[uuid.UUID | str, list[SubmissionRow]] = {}
        for row in rows:
            by_type.setdefault(row.activity_type_id or row.activity_type_key, []).append(row)

        out: dict[str, tuple[str, str]] = {}
        for type_rows in by_type.values():
            type_key = type_rows[0].activity_type_key
            path = _unique_path(type_key, out)
            out[path] = (type_key, self._build_type_csv(type_rows, rooms))
        return out

    def _build_type_csv(self, rows: list[SubmissionRow], rooms: dict[uuid.UUID, str]) -> str:
        fields = _payload_columns(rows)
        buf = io.StringIO()
        buf.write(_BOM)
        writer = csv.writer(buf)
        # Property names are the type author's text, so they are cells like any other.
        writer.writerow([*_LEADING_COLUMNS, *map(_cell, fields), *_TRAILING_COLUMNS])
        for row in rows:
            code, kind = _subject(row)
            filled_fields = row.sub_scores.get(FILLED_FIELDS_SUB_SCORE)
            writer.writerow(
                [
                    _cell(code),
                    kind,
                    rooms[row.chatroom_id],
                    _UNKNOWN_VERSION if row.type_version is None else row.type_version,
                    row.attempt_no,
                    row.is_valid,
                    _cell(row.error_class),
                    row.latency_ms,
                    row.created_at.isoformat() if row.created_at else None,
                    *(_cell(_flatten(row.payload.get(name))) for name in fields),
                    # An MCP or webhook validator writes sub_scores, so any value may be text.
                    _cell(row.sub_scores.get("filled")),
                    _cell("; ".join(map(str, filled_fields))) if isinstance(filled_fields, list) else None,
                    *_judge_cells(row),
                    _json(row.payload or {}),
                    _json(row.sub_scores or {}),
                ]
            )
        return buf.getvalue()

    def _build_observations_json(
        self,
        rows: list[ObservationRow],
        agent_key_map: dict[uuid.UUID, str],
        rooms: dict[uuid.UUID, str],
    ) -> str:
        observations = []
        for row in rows:
            observations.append(
                {
                    "room_code": rooms[row.chatroom_id],
                    "agent_key": agent_key_map.get(row.agent_id, str(row.agent_id)[:8]),
                    "blocks": list(row.blocks or []),
                    "content_md": row.content_md,
                    "trigger": row.trigger,
                    "created_at": (row.created_at.isoformat() if row.created_at else None),
                    "released_at": (row.released_at.isoformat() if row.released_at else None),
                }
            )
        return json.dumps(observations, ensure_ascii=False, indent=2, default=str)

    def _build_transcripts_json(
        self,
        rows: list[MessageRow],
        agent_key_map: dict[uuid.UUID, str],
        room_codes: dict[uuid.UUID, str],
    ) -> str:
        rooms: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            sender_type = row.sender_type
            if hasattr(sender_type, "value"):
                sender_type = sender_type.value

            if sender_type == "agent" and row.sender_id:
                identifier, kind = agent_key_map.get(row.sender_id, str(row.sender_id)[:8]), "agent"
            elif sender_type in ("human", "user") and row.sender_id:
                identifier, kind = subject_code(row.sender_id), "user"
            elif sender_type == "guest" and row.sender_id:
                # The guest session id, which the guest's activity sessions hold
                # as their subject: one code joins the transcript to the CSVs.
                identifier, kind = subject_code(row.sender_id), "guest"
            else:
                identifier, kind = sender_type or "system", sender_type or "system"

            rooms.setdefault(room_codes[row.chatroom_id], []).append(
                {
                    "subject_code": identifier,
                    "subject_kind": kind,
                    "sender_type": sender_type,
                    "content_md": row.content_md,
                    "created_at": (row.created_at.isoformat() if row.created_at else None),
                }
            )
        return json.dumps(rooms, ensure_ascii=False, indent=2, default=str)

    def _build_manifest(
        self,
        *,
        workspace_id: uuid.UUID,
        room_count: int,
        created_after: datetime | None,
        created_before: datetime | None,
        submission_count: int,
        observation_count: int,
        submission_files: list[dict[str, str]],
    ) -> str:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "workspace_code": workspace_code(workspace_id),
            "room_count": room_count,
            "date_range": {
                "created_after": (created_after.isoformat() if created_after else None),
                "created_before": (created_before.isoformat() if created_before else None),
            },
            "submission_count": submission_count,
            "observation_count": observation_count,
            "submission_files": submission_files,
            "notes": list(_MANIFEST_NOTES),
            "exported_at": now().isoformat(),
        }
        return json.dumps(manifest, ensure_ascii=False, indent=2, default=str)

    def _package_zip(self, files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr(path, content)
        return buf.getvalue()


def _judge_cells(row: SubmissionRow) -> list[Any]:
    """``judge_status``, the four scores and ``judge_model``.

    Scores are written only for ``scored``: a ``skipped`` or ``error`` row has none,
    and an empty cell must not read as a zero.
    """
    scores = row.judge_scores if row.judge_status == "scored" and row.judge_scores else None
    if scores is None:
        return [row.judge_status, *([None] * len(_SCORE_COLUMNS)), None]
    return [
        row.judge_status,
        *(_score(scores.get(name)) for name in _SCORE_COLUMNS),
        _cell(scores.get("model")),
    ]


def _score(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _subject(row: SubmissionRow) -> tuple[str, str]:
    if row.subject_member_group_id:
        return group_subject_code(row.subject_member_group_id), "group"
    if row.subject_user_id:
        return subject_code(row.subject_user_id), row.subject_kind
    return "unknown", "unknown"


def _room_codes(room_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """``r:<8>`` per room, suffixed where two rooms of the export share the prefix.

    A shared code would merge two rooms' transcripts under one key.
    """
    by_code: dict[str, list[uuid.UUID]] = {}
    for room_id in sorted(set(room_ids)):
        by_code.setdefault(room_code(room_id), []).append(room_id)
    codes: dict[uuid.UUID, str] = {}
    for code, ids in by_code.items():
        for n, room_id in enumerate(ids, start=1):
            codes[room_id] = code if len(ids) == 1 else f"{code}~{n}"
    return codes


def _payload_columns(rows: list[SubmissionRow]) -> list[str]:
    """The declared properties across the rows, in first-seen order.

    Fixed columns are never repeated: a property that shares a name with one
    stays in ``payload_json`` only.
    """
    reserved = {*_LEADING_COLUMNS, *_TRAILING_COLUMNS}
    seen: dict[str, None] = {}
    for row in rows:
        for name in row.payload_fields:
            if name not in reserved:
                seen.setdefault(name, None)
    return list(seen)


def _unique_path(type_key: str, taken: dict[str, Any]) -> str:
    stem = _UNSAFE_FILE_CHARS.sub("-", type_key.lower()).strip("-") or "type"
    path, n = f"submissions/{stem}.csv", 1
    while path in taken:
        n += 1
        path = f"submissions/{stem}-{n}.csv"
    return path


def _flatten(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(v if isinstance(v, str) else _json(v) for v in value)
    if isinstance(value, dict):
        return _json(value)
    return value


def _json(value: Any) -> str:
    # ensure_ascii=False keeps CJK answers readable instead of \u escapes.
    return json.dumps(value, ensure_ascii=False, default=str)


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


__all__ = [
    "SCHEMA_VERSION",
    "MessageRow",
    "ObservationRow",
    "ResearchExportBuilder",
    "SubmissionRow",
]
