"""Research data export builder (R33.10).

Queries submissions, observations and messages for every room in a workspace,
de-identifies all participant data using ``subject_code()``, and packages the
result as a ZIP file containing CSV + JSON files.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.subject_code import group_subject_code, subject_code
from contexts.activities.infrastructure import tables as at
from contexts.conversation.infrastructure import tables as ct
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.storage import export_key, get_minio_client

_EXPORT_PUT_TIMEOUT_SECONDS = 120


class ResearchExportBuilder:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def build_and_upload(
        self,
        *,
        job_id: uuid.UUID,
        workspace_id: uuid.UUID,
        room_ids: list[uuid.UUID],
        owner_user_id: uuid.UUID,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[str, str]:
        submissions_rows = await self._query_submissions(room_ids, created_after, created_before)
        observations_rows = await self._query_observations(room_ids, created_after, created_before)
        messages_rows = await self._query_messages(room_ids, created_after, created_before)
        agent_ids = self._collect_agent_ids(observations_rows, messages_rows)
        agent_key_map = await self._resolve_agent_keys(agent_ids)

        await self._db.flush()

        submissions_csv = self._build_submissions_csv(submissions_rows)
        observations_json = self._build_observations_json(observations_rows, agent_key_map)
        transcripts_json = self._build_transcripts_json(messages_rows, agent_key_map)
        manifest_json = self._build_manifest(
            workspace_id=workspace_id,
            room_count=len(room_ids),
            created_after=created_after,
            created_before=created_before,
            submission_count=len(submissions_rows),
            observation_count=len(observations_rows),
        )

        zip_bytes = self._package_zip(submissions_csv, observations_json, transcripts_json, manifest_json)

        client = get_minio_client()
        key = export_key(job_id=job_id, filename="research-data.zip")

        import asyncio

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
            self._db,
            audit.AuditEvent(
                action="research_data.exported",
                actor_user_id=owner_user_id,
                resource_type="workspace",
                resource_id=workspace_id,
                metadata={
                    "job_id": str(job_id),
                    "room_count": len(room_ids),
                    "submission_count": len(submissions_rows),
                    "observation_count": len(observations_rows),
                    "created_after": created_after.isoformat() if created_after else None,
                    "created_before": created_before.isoformat() if created_before else None,
                },
            ),
        )

        return client.exports_bucket, key

    # -- Queries ------------------------------------------------------------ #

    async def _query_submissions(
        self,
        room_ids: list[uuid.UUID],
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> list[Any]:
        clauses: list[sa.ColumnElement[bool]] = [
            at.activity_submissions.c.chatroom_id.in_(room_ids),
            at.activity_submissions.c.deleted_at.is_(None),
        ]
        if created_after:
            clauses.append(at.activity_submissions.c.created_at >= created_after)
        if created_before:
            clauses.append(at.activity_submissions.c.created_at < created_before)

        j = at.activity_submissions.join(
            at.activity_types,
            at.activity_submissions.c.activity_type_id == at.activity_types.c.id,
        ).join(
            at.activity_sessions,
            at.activity_submissions.c.session_id == at.activity_sessions.c.id,
        )

        stmt = (
            sa.select(
                at.activity_submissions.c.id,
                at.activity_submissions.c.chatroom_id,
                at.activity_submissions.c.producer_user_id,
                at.activity_submissions.c.payload,
                at.activity_submissions.c.sub_scores,
                at.activity_submissions.c.attempt_no,
                at.activity_submissions.c.is_valid,
                at.activity_submissions.c.error_class,
                at.activity_submissions.c.latency_ms,
                at.activity_submissions.c.created_at,
                at.activity_types.c.key.label("activity_type_key"),
                at.activity_sessions.c.subject_user_id,
                at.activity_sessions.c.subject_member_group_id,
            )
            .select_from(j)
            .where(sa.and_(*clauses))
            .order_by(at.activity_submissions.c.created_at)
        )
        result = await self._db.execute(stmt)
        return list(result.all())

    async def _query_observations(
        self,
        room_ids: list[uuid.UUID],
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> list[Any]:
        clauses: list[sa.ColumnElement[bool]] = [
            ct.agent_observations.c.chatroom_id.in_(room_ids),
            ct.agent_observations.c.deleted_at.is_(None),
        ]
        if created_after:
            clauses.append(ct.agent_observations.c.created_at >= created_after)
        if created_before:
            clauses.append(ct.agent_observations.c.created_at < created_before)

        stmt = (
            ct.agent_observations.select()
            .where(sa.and_(*clauses))
            .order_by(ct.agent_observations.c.created_at)
        )
        result = await self._db.execute(stmt)
        return list(result.all())

    async def _query_messages(
        self,
        room_ids: list[uuid.UUID],
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> list[Any]:
        clauses: list[sa.ColumnElement[bool]] = [
            ct.messages.c.chatroom_id.in_(room_ids),
            ct.messages.c.deleted_at.is_(None),
        ]
        if created_after:
            clauses.append(ct.messages.c.created_at >= created_after)
        if created_before:
            clauses.append(ct.messages.c.created_at < created_before)

        stmt = (
            sa.select(
                ct.messages.c.chatroom_id,
                ct.messages.c.sender_type,
                ct.messages.c.sender_id,
                ct.messages.c.content_md,
                ct.messages.c.created_at,
            )
            .where(sa.and_(*clauses))
            .order_by(ct.messages.c.chatroom_id, ct.messages.c.created_at)
        )
        result = await self._db.execute(stmt)
        return list(result.all())

    def _collect_agent_ids(self, observations: list[Any], messages: list[Any]) -> set[uuid.UUID]:
        ids: set[uuid.UUID] = set()
        for obs in observations:
            ids.add(obs.agent_id)
        for msg in messages:
            if msg.sender_type == "agent" and msg.sender_id:
                ids.add(msg.sender_id)
        return ids

    async def _resolve_agent_keys(self, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not agent_ids:
            return {}
        from contexts.agents.interfaces.facade import AgentsFacade

        facade = AgentsFacade(self._db)
        return await facade.agent_names(list(agent_ids))

    # -- Serialization ------------------------------------------------------ #

    def _build_submissions_csv(self, rows: list[Any]) -> str:
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
        self, rows: list[Any], agent_key_map: dict[uuid.UUID, str]
    ) -> str:
        observations = []
        for row in rows:
            observations.append({
                "room_id": str(row.chatroom_id),
                "agent_key": agent_key_map.get(row.agent_id, str(row.agent_id)[:8]),
                "blocks": list(row.blocks or []),
                "content_md": row.content_md,
                "trigger": row.trigger,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "released_at": row.released_at.isoformat() if row.released_at else None,
            })
        return json.dumps(observations, ensure_ascii=False, indent=2, default=str)

    def _build_transcripts_json(
        self, rows: list[Any], agent_key_map: dict[uuid.UUID, str]
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
                identifier = agent_key_map.get(row.sender_id, str(row.sender_id)[:8])
            elif sender_type_str in ("human", "user") and row.sender_id:
                identifier = subject_code(row.sender_id)
            else:
                identifier = sender_type_str or "system"

            rooms[room_key].append({
                "subject_code": identifier,
                "sender_type": sender_type_str,
                "content_md": row.content_md,
                "created_at": row.created_at.isoformat() if row.created_at else None,
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
                "created_after": created_after.isoformat() if created_after else None,
                "created_before": created_before.isoformat() if created_before else None,
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


__all__ = ["ResearchExportBuilder"]
