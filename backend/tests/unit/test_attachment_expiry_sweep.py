"""The application-side expiry sweep and the read-path guard behind it (R13.11a).

The `chat-uploads` bucket carries a bucket-wide 3-day lifecycle rule, so an
attachment's bytes are deleted on day four. These tests pin the two halves of
the application-side counterpart: the nightly sweep that makes the row say so,
and the download guard that refuses a row whose horizon has passed regardless
of what the row's status currently claims (the sweep and the bucket run on
independent clocks, so the row can lag the deletion by up to a night).

Same fake-collaborator construction as test_attachment_download_disposition.py
-- no database.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.domain.errors import (
    AttachmentExpired,
    AttachmentQuarantined,
)
from contexts.conversation.domain.models import (
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)
from shared_kernel.auth.clients import now


class _FakeMinio:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def presigned_get(
        self,
        *,
        bucket: str,
        key: str,
        expires: timedelta = timedelta(minutes=15),
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        self.calls.append({"bucket": bucket, "key": key})
        return f"https://minio.local/{bucket}/{key}?signed=1"


class _FakeRepo:
    """Applies `list_expired`'s real predicate over an in-memory row set so the
    sweep's batching loop terminates the same way it does against Postgres."""

    def __init__(self, rows: Sequence[MessageAttachment]) -> None:
        self.rows = list(rows)
        self.marked: list[uuid.UUID] = []

    async def get(self, attachment_id: uuid.UUID) -> MessageAttachment | None:
        return next((r for r in self.rows if r.id == attachment_id), None)

    async def list_expired(
        self,
        *,
        horizon: datetime,
        limit: int = 500,
    ) -> Sequence[MessageAttachment]:
        due = [
            r
            for r in self.rows
            if r.expires_at is not None
            and r.expires_at < horizon
            and r.status is AttachmentStatus.ACTIVE
            and r.message_id is not None
        ]
        return due[:limit]

    async def mark_expired(self, attachment_id: uuid.UUID) -> None:
        self.marked.append(attachment_id)
        self.rows = [
            replace(r, status=AttachmentStatus.EXPIRED) if r.id == attachment_id else r for r in self.rows
        ]


def _row(
    *,
    expires_at: datetime | None,
    status: AttachmentStatus = AttachmentStatus.ACTIVE,
    bound: bool = True,
    chatroom_id: uuid.UUID | None = None,
    uploaded_by: uuid.UUID | None = None,
) -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=uuid.uuid4() if bound else None,
        uploaded_by_user_id=uploaded_by,
        filename="file.bin",
        mime="application/pdf",
        size_bytes=10,
        minio_path="chat-uploads/proj/room/att/file.bin",
        status=status,
        scan_status=ScanStatus.CLEAN,
        scan_at=None,
        expires_at=expires_at,
        chatroom_id=chatroom_id if chatroom_id is not None else uuid.uuid4(),
    )


def _service(rows: Sequence[MessageAttachment]) -> tuple[AttachmentService, _FakeRepo, _FakeMinio]:
    minio = _FakeMinio()
    svc = AttachmentService(db=None, minio=minio)  # type: ignore[arg-type]
    repo = _FakeRepo(rows)
    svc._repo = repo  # type: ignore[assignment]
    return svc, repo, minio


# ---- the sweep ------------------------------------------------------------ #


@pytest.mark.asyncio
@pytest.mark.parametrize("uploader", [uuid.uuid4(), None], ids=["user_upload", "agent_artifact"])
async def test_sweep_marks_rows_past_their_horizon_expired(uploader: uuid.UUID | None) -> None:
    # Both producers stamp the same ATTACHMENT_TTL and land in the same bucket
    # under the same lifecycle rule, so the sweep must be producer-agnostic --
    # an agent-produced chart dies on day four exactly as a user upload does.
    past = _row(expires_at=now() - timedelta(hours=1), uploaded_by=uploader)
    future = _row(expires_at=now() + timedelta(days=1), uploaded_by=uploader)
    svc, repo, _ = _service([past, future])

    with patch("contexts.conversation.application.attachment_service.audit.emit", new=AsyncMock()):
        count = await svc.expire_due()

    assert count == 1
    assert repo.marked == [past.id]


@pytest.mark.asyncio
async def test_sweep_emits_an_attachment_expired_audit_per_row() -> None:
    chatroom_id = uuid.uuid4()
    row = _row(expires_at=now() - timedelta(hours=1), chatroom_id=chatroom_id)
    svc, _, _ = _service([row])

    emit = AsyncMock()
    with patch("contexts.conversation.application.attachment_service.audit.emit", new=emit):
        await svc.expire_due()

    assert emit.await_count == 1
    event = emit.await_args.args[1]
    assert event.action == "attachment.expired"
    assert event.resource_id == row.id
    assert event.metadata["chatroom_id"] == str(chatroom_id)


@pytest.mark.asyncio
async def test_sweep_leaves_rows_that_are_already_expired_alone() -> None:
    # Idempotence across consecutive nights: without it the first run after
    # deploy would re-emit an audit row for the entire backlog every night.
    row = _row(expires_at=now() - timedelta(hours=1))
    svc, repo, _ = _service([row])

    emit = AsyncMock()
    with patch("contexts.conversation.application.attachment_service.audit.emit", new=emit):
        first = await svc.expire_due()
        second = await svc.expire_due()

    assert (first, second) == (1, 0)
    assert repo.marked == [row.id]
    assert emit.await_count == 1


@pytest.mark.asyncio
async def test_sweep_leaves_quarantined_rows_alone() -> None:
    # Quarantine is a security verdict and carries more information than
    # "expired"; letting the horizon overwrite it would downgrade the UI from
    # "this file was quarantined" to "this file expired" (D-2).
    row = _row(
        expires_at=now() - timedelta(days=2),
        status=AttachmentStatus.QUARANTINED,
    )
    svc, repo, _ = _service([row])

    with patch("contexts.conversation.application.attachment_service.audit.emit", new=AsyncMock()):
        count = await svc.expire_due()

    assert count == 0
    assert repo.marked == []


@pytest.mark.asyncio
async def test_sweep_ignores_rows_never_bound_to_a_message() -> None:
    # Unbound rows are `purge_old_attachments`'s territory -- it deletes them
    # outright. Expiring them first would emit an `attachment.expired` audit for
    # a row no message ever pointed at, which is not what R13.11 describes (D-1).
    orphan = _row(expires_at=now() - timedelta(hours=1), bound=False)
    svc, repo, _ = _service([orphan])

    with patch("contexts.conversation.application.attachment_service.audit.emit", new=AsyncMock()):
        count = await svc.expire_due()

    assert count == 0
    assert repo.marked == []


# ---- the read-path guard -------------------------------------------------- #


@pytest.mark.asyncio
async def test_download_of_an_expired_attachment_is_refused() -> None:
    # The lag window: MinIO has already deleted the bytes but the nightly sweep
    # has not run yet, so the row still reads ACTIVE. Presigning here hands the
    # client a URL that resolves to a NoSuchKey body.
    row = _row(expires_at=now() - timedelta(hours=1))
    svc, _, minio = _service([row])

    with pytest.raises(AttachmentExpired):
        await svc.get_for_download(attachment_id=row.id)

    assert minio.calls == []


@pytest.mark.asyncio
async def test_download_of_a_row_marked_expired_is_refused() -> None:
    row = _row(expires_at=None, status=AttachmentStatus.EXPIRED)
    svc, _, minio = _service([row])

    with pytest.raises(AttachmentExpired):
        await svc.get_for_download(attachment_id=row.id)

    assert minio.calls == []


@pytest.mark.asyncio
async def test_download_of_a_live_attachment_still_presigns() -> None:
    # Guard against an over-broad horizon comparison taking working downloads
    # away -- the highest-consequence risk in the dossier (section 9).
    row = _row(expires_at=now() + timedelta(days=1))
    svc, _, minio = _service([row])

    ptr = await svc.get_for_download(attachment_id=row.id)

    assert ptr.url.startswith("https://minio.local/")
    assert len(minio.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("naive", [False, True], ids=["aware", "naive"])
async def test_horizon_comparison_tolerates_a_naive_timestamp(naive: bool) -> None:
    # Comparing a naive datetime against an aware one raises TypeError rather
    # than returning a wrong answer, and on this path that is a 500 on every
    # download. The schema stores TIMESTAMP(timezone=True) so the real read path
    # is always aware, but the guard costs nothing and the failure mode is loud.
    expires = now() - timedelta(hours=1)
    row = _row(expires_at=expires.replace(tzinfo=None) if naive else expires)
    svc, _, minio = _service([row])

    with pytest.raises(AttachmentExpired):
        await svc.get_for_download(attachment_id=row.id)

    assert minio.calls == []


@pytest.mark.asyncio
async def test_quarantine_is_refused_ahead_of_expiry() -> None:
    # A row that is both quarantined and past its horizon must still report the
    # quarantine: 403 with the scan verdict, not 410 with "it aged out".
    row = _row(
        expires_at=now() - timedelta(days=2),
        status=AttachmentStatus.QUARANTINED,
    )
    svc, _, minio = _service([row])

    with pytest.raises(AttachmentQuarantined):
        await svc.get_for_download(attachment_id=row.id)

    assert minio.calls == []
