"""Research data export builder (R33.10).

Serializes pre-queried submissions, observations and messages into a
de-identified ZIP file (CSV + JSON) and uploads it to MinIO.

Data is received as typed dicts from the worker task, which queries through
the proper facades. This module never imports from another context's
infrastructure layer.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contexts.activities.domain.subject_code import group_subject_code, subject_code
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.storage import export_key, get_minio_client

_EXPORT_PUT_TIMEOUT_SECONDS = 120


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
        submissions_csv = self._build_submissions_csv(submissions)
        observations_json = self._build_observations_json(observations, agent_key_map)
        transcripts_json = self._build_transcripts_json(messages, agent_key_map)
        manifest_json = self._build_manifest(
            workspace_id=workspace_id,
            room_count=len(room_ids),
            created_after=created_after,
            created_before=created_before,
            submission_count=len(submissions),
            observation_count=len(observations),
        )

        zip_bytes = self._package_zip(
            submissions_csv, observations_json, transcripts_json, manifest_json
        )

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
                f"research export MinIO put timed out after "
                f"{_EXPORT_PUT_TIMEOUT_SECONDS}s (job {job_id})"
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
                    "created_after": (
                        created_after.isoformat() if created_after else None
                    ),
                    "created_before": (
                        created_before.isoformat() if created_before else None
                    ),
                },
            ),
        )

        return client.exports_bucket, key

    # -- Serialization ------------------------------------------------------ #

    def _build_submissions_csv(self, rows: list[SubmissionRow]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
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
        ])
        for row in rows:
            if row.subject_member_group_id:
                code = group_subject_code(row.subject_member_group_id)
            elif row.subject_user_id:
                code = subject_code(row.subject_user_id)
            else:
                code = "unknown"

            writer.writerow([
                code,
                row.activity_type_key,
                str(row.chatroom_id),
                row.attempt_no,
                row.is_valid,
                row.error_class,
                row.latency_ms,
                row.created_at.isoformat() if row.created_at else None,
                json.dumps(row.payload, default=str) if row.payload else "{}",
                json.dumps(row.sub_scores, default=str) if row.sub_scores else "{}",
            ])
        return buf.getvalue()

    def _build_observations_json(
        self,
        rows: list[ObservationRow],
        agent_key_map: dict[uuid.UUID, str],
    ) -> str:
        observations = []
        for row in rows:
            observations.append({
                "room_id": str(row.chatroom_id),
                "agent_key": agent_key_map.get(
                    row.agent_id, str(row.agent_id)[:8]
                ),
                "blocks": list(row.blocks or []),
                "content_md": row.content_md,
                "trigger": row.trigger,
                "created_at": (
                    row.created_at.isoformat() if row.created_at else None
                ),
                "released_at": (
                    row.released_at.isoformat() if row.released_at else None
                ),
            })
        return json.dumps(observations, ensure_ascii=False, indent=2, default=str)

    def _build_transcripts_json(
        self,
        rows: list[MessageRow],
        agent_key_map: dict[uuid.UUID, str],
    ) -> str:
        rooms: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            room_key = str(row.chatroom_id)
            if room_key not in rooms:
                rooms[room_key] = []

            sender_type_str = row.sender_type
            if hasattr(sender_type_str, "value"):
                sender_type_str = sender_type_str.value

            if sender_type_str == "agent" and row.sender_id:
                identifier = agent_key_map.get(
                    row.sender_id, str(row.sender_id)[:8]
                )
            elif sender_type_str in ("human", "user") and row.sender_id:
                identifier = subject_code(row.sender_id)
            else:
                identifier = sender_type_str or "system"

            rooms[room_key].append({
                "subject_code": identifier,
                "sender_type": sender_type_str,
                "content_md": row.content_md,
                "created_at": (
                    row.created_at.isoformat() if row.created_at else None
                ),
            })
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
    ) -> str:
        manifest = {
            "schema_version": 1,
            "workspace_id": str(workspace_id),
            "room_count": room_count,
            "date_range": {
                "created_after": (
                    created_after.isoformat() if created_after else None
                ),
                "created_before": (
                    created_before.isoformat() if created_before else None
                ),
            },
            "submission_count": submission_count,
            "observation_count": observation_count,
            "exported_at": now().isoformat(),
        }
        return json.dumps(manifest, ensure_ascii=False, indent=2, default=str)

    def _package_zip(
        self,
        submissions_csv: str,
        observations_json: str,
        transcripts_json: str,
        manifest_json: str,
    ) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("submissions.csv", submissions_csv)
            zf.writestr("observations.json", observations_json)
            zf.writestr("transcripts.json", transcripts_json)
            zf.writestr("manifest.json", manifest_json)
        return buf.getvalue()


__all__ = [
    "MessageRow",
    "ObservationRow",
    "ResearchExportBuilder",
    "SubmissionRow",
]
