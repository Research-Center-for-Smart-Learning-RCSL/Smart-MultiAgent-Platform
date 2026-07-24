"""Report attachments whose stored object size disagrees with `size_bytes`.

`docs/tasks/2026-07-22-attachment-lifecycle-and-rendering` F-14. A TUS chunk
write that failed part-way used to leave the flushed prefix on the staging
file; the client's retry appended on top of it, and the resulting file -- longer
than declared, with duplicated bytes mid-stream -- was uploaded and recorded
with the client-declared length as if valid. That has been fixed prospectively.
This reports what the old behaviour may already have written.

**Read-only, and deliberately so.** It performs no writes of any kind: no
delete, no re-derive, no status change. A size mismatch has causes other than
this defect, and deleting a user's attachment on a heuristic is a worse outcome
than a corrupt download. Escalate the report for manual review.

**Detection is partial and the report must be read as such.** Chat attachments
store no content digest, so a corrupt file whose length happens to equal the
declared length is undetectable by any means available after the fact. The
sha256 on the RAG and knowmap paths does not close that gap either: it is
computed over the already-staged file, so it certifies nothing about integrity.
A clean report therefore means "no length disagreement found", not "no
corruption exists".

**Expect it to find nothing.** The defect requires a partial-write disk fault
on a staging volume. With no ENOSPC or I/O-error history there, the expected
size of the affected set is zero. Run once, keep the script, do not schedule it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from loguru import logger

from contexts.conversation.domain.models import AttachmentStatus
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import as_utc, now
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.storage import get_minio_client

_PAGE = 500


@dataclass(frozen=True)
class SizeMismatch:
    attachment_id: uuid.UUID
    minio_path: str
    recorded_bytes: int
    stored_bytes: int


@dataclass
class ReconcileReport:
    """What one pass observed. Counts are rows, not bytes."""

    examined: int = 0
    mismatched: list[SizeMismatch] = field(default_factory=list)
    # Object gone while the row still claims it is live. Expected for rows past
    # their horizon (the bucket lifecycle and the nightly sweep are independent
    # clocks), so only counted for rows that should still have their bytes.
    missing_unexpectedly: list[uuid.UUID] = field(default_factory=list)
    # stat() raised for a reason other than a missing key. Counted rather than
    # raised so one unreachable object cannot hide the rest of the report.
    unreadable: int = 0


async def _reconcile() -> ReconcileReport:
    minio = get_minio_client()
    sm = get_sessionmaker()
    report = ReconcileReport()
    horizon = now()
    last_id: uuid.UUID | None = None

    while True:
        # Keyset pagination on the primary key: a plain OFFSET would re-read or
        # skip rows if anything is inserted between pages during a long run.
        async with sm() as session:
            query = sa.select(
                t.message_attachments.c.id,
                t.message_attachments.c.minio_path,
                t.message_attachments.c.size_bytes,
                t.message_attachments.c.status,
                t.message_attachments.c.expires_at,
            ).order_by(t.message_attachments.c.id)
            if last_id is not None:
                query = query.where(t.message_attachments.c.id > last_id)
            rows = (await session.execute(query.limit(_PAGE))).all()

        if not rows:
            break
        last_id = rows[-1].id

        for row in rows:
            report.examined += 1
            bucket, _, key = row.minio_path.partition("/")
            try:
                stat = await minio.stat(bucket=bucket, key=key)
            except Exception as exc:
                report.unreadable += 1
                logger.warning("reconcile: could not stat {}: {}", row.minio_path, exc)
                continue

            if stat is None:
                still_live = row.status == AttachmentStatus.ACTIVE.value and (
                    row.expires_at is None or as_utc(row.expires_at) > horizon
                )
                if still_live:
                    report.missing_unexpectedly.append(row.id)
                continue

            if stat.size != row.size_bytes:
                report.mismatched.append(
                    SizeMismatch(
                        attachment_id=row.id,
                        minio_path=row.minio_path,
                        recorded_bytes=row.size_bytes,
                        stored_bytes=stat.size,
                    )
                )

    return report


def run() -> ReconcileReport:
    """Walk every attachment row and compare its recorded size to the object."""
    return asyncio.run(_reconcile())
