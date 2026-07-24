"""Repair compaction summary rows written before per-agent scoping.

`docs/tasks/2026-07-22-compaction-scoping-and-durability`. Three defects wrote
bad summary rows: an empty provider response was accepted as a summary and
permanently elided its range (B); the room lock was released before the summary
committed, so two agents could fold overlapping ranges (C); and a summary
carried no producer, so one agent's fold truncated every other agent's history
(A). All three are fixed prospectively. This repairs what they already wrote.

**Nothing was destroyed, and that bounds the whole job.**
`replace_range_with_summary` only ever INSERTed — it never deleted a folded row
and never set `deleted_at`. The "replacement" is entirely a read-time
projection. So every repair here is an edit to a *summary row's* metadata, and
no conversation content is at risk.

**Most of A's damage is already undone by the loader.** A summary with no
`producer_agent_id` now belongs to no one: it is neither applied nor injected
(Q-7), so every agent in a legacy room already sees its full history again.
Those rows need no surgery and are only counted here, because deleting them
would destroy summary text that users can still read in the feed.

**What still needs an edit:**

- *Empty summaries.* Already inert as a projection, but the row is user-visible
  — it renders as a blank system divider in the room and is included in exports.
  Voiding removes a divider users can see and cannot read.
- *Overlapping folds by the same producer.* Legitimate compaction never
  overlaps: `choose_range_to_compact` starts at the first un-compacted message
  and stops at any prior summary, so an intersection within one producer is
  evidence of the C race. The later summary is voided, the earlier kept.

**What is reported and deliberately not repaired:** summaries whose
`compacted_ids` name messages that no longer exist. Under R13.26 a user
deletion legitimately leaves that trace and must not cost the room its summary,
and this pass cannot tell a user deletion from an old retention purge after the
fact. The retention purge now removes such summaries itself, prospectively.

**Dry-run by default.** Reports what it would void and writes nothing until
`SMAP_REPAIR_COMPACTION_SUMMARIES_ARMED` is set. That is an environment
variable rather than a CLI flag for the reason recorded in
`purge_session_dirs._ARMED_ENV`: typer 0.12.5 against the installed click
mis-converts flag defaults, which would invert exactly this decision.

**Every void is reversible.** `compacted_ids` is moved to
`original_compacted_ids` rather than dropped, so a void can be undone by
renaming the type back and moving the key back.

**One seq scan.** `messages.metadata` has no GIN index (0017 and 0034 index
only `(chatroom_id, created_at)` and `(chatroom_id, sender_id)`), so the summary
lookup scans. Run it in a maintenance window; it is a one-off.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.agent_fs_gc import _TRUTHY
from contexts.conversation.infrastructure import tables as t
from contexts.conversation.interfaces import (
    COMPACT_SUMMARY_TYPE,
    COMPACTED_IDS_KEY,
    ORIGINAL_COMPACTED_IDS_KEY,
    VOIDED_SUMMARY_TYPE,
    compacted_ids,
    summary_producer,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.db.session import get_sessionmaker

_ARMED_ENV = "SMAP_REPAIR_COMPACTION_SUMMARIES_ARMED"
_PAGE = 500


def is_armed() -> bool:
    """True only for an explicit, recognised truthy value. Anything else is a dry run."""
    return os.environ.get(_ARMED_ENV, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Void:
    message_id: uuid.UUID
    chatroom_id: uuid.UUID
    reason: str


@dataclass
class RepairReport:
    """What one pass saw and did, in summary rows."""

    examined: int = 0
    empty: list[Void] = field(default_factory=list)
    overlapping: list[Void] = field(default_factory=list)
    # No producer: already inert for every reader (Q-7). Counted so an operator
    # can see how many rooms regained full history, not repaired.
    producerless: int = 0
    # compacted_ids naming rows that are gone. Reported for manual review; see
    # the module docstring for why this is not repaired.
    orphaned: list[Void] = field(default_factory=list)
    dry_run: bool = True

    @property
    def would_void(self) -> int:
        return len(self.empty) + len(self.overlapping)


async def _iter_summary_pages(session: AsyncSession) -> AsyncIterator[Sequence[Any]]:
    """Yield live compaction summaries a page at a time, oldest-first per room.

    Ordered by `(chatroom_id, created_at)` so the overlap pass can keep the
    *earlier* summary of an overlapping pair without a second sort. Paged by
    that same ordering as a keyset, not by OFFSET, so the cost stays linear in
    the population rather than quadratic in the page count.

    Yields pages rather than returning the whole population because the rows
    carry `content_md`: accumulating them would put every summary's full text in
    memory at once, which is the thing the paging exists to avoid.
    """
    cursor: tuple[Any, Any, Any] | None = None
    order = (t.messages.c.chatroom_id, t.messages.c.created_at, t.messages.c.id)
    while True:
        query = sa.select(
            t.messages.c.id,
            t.messages.c.chatroom_id,
            t.messages.c.content_md,
            t.messages.c.metadata,
            t.messages.c.created_at,
        ).where(
            t.messages.c.metadata["type"].astext == COMPACT_SUMMARY_TYPE,
            t.messages.c.deleted_at.is_(None),
        )
        if cursor is not None:
            # Compared against a plain tuple, not `sa.tuple_(*cursor)`: the
            # plain form propagates each column's own type to its bind param
            # (pg.UUID, TIMESTAMP), while `tuple_` infers generic Uuid/DateTime
            # from the values — the same shape of type mismatch asyncpg refuses
            # to bind elsewhere in this codebase.
            query = query.where(sa.tuple_(*order) > cursor)
        page = (await session.execute(query.order_by(*order).limit(_PAGE))).all()
        if not page:
            return
        yield page
        last = page[-1]
        cursor = (last.chatroom_id, last.created_at, last.id)
        if len(page) < _PAGE:
            return


async def _live_message_ids(session: AsyncSession, ids: set[str]) -> set[str]:
    """Which of ``ids`` still exist. Chunked to keep the IN list bounded."""
    if not ids:
        return set()
    as_uuid: list[uuid.UUID] = []
    for i in ids:
        try:
            as_uuid.append(uuid.UUID(i))
        except ValueError:
            continue  # a malformed id cannot name a live row
    live: set[str] = set()
    for start in range(0, len(as_uuid), _PAGE):
        chunk = as_uuid[start : start + _PAGE]
        found = (await session.execute(sa.select(t.messages.c.id).where(t.messages.c.id.in_(chunk)))).all()
        live.update(str(r.id) for r in found)
    return live


# (chatroom, producer) -> ids already claimed by an earlier summary. Overlap is
# only meaningful within one producer's view: two producers folding the same
# range is normal under per-agent scoping, not a race.
_Claimed = dict[tuple[uuid.UUID, str], set[str]]


def _classify_page(rows: Sequence[Any], report: RepairReport, claimed: _Claimed) -> None:
    """Fold one page into ``report``. ``claimed`` carries across pages.

    Safe to run per page because the scan is ordered by `(chatroom_id,
    created_at)`, so a producer's summaries arrive oldest-first and contiguous.
    """
    report.examined += len(rows)
    for row in rows:
        producer = summary_producer(row.metadata)
        covered = compacted_ids(row.metadata)

        if not (row.content_md or "").strip():
            report.empty.append(Void(row.id, row.chatroom_id, "empty_summary"))
            # Deliberately does NOT register `covered`. This row is voided in
            # the same pass, so a later summary covering the same range is the
            # only one left holding it — flagging that one as overlapping too
            # would void both and lose the range entirely.
            continue
        if not producer:
            # Same reasoning, different cause: a producerless row is already
            # applied to no reader, so its range is not claimed by anyone.
            report.producerless += 1
            continue

        key = (row.chatroom_id, producer)
        seen = claimed.setdefault(key, set())
        if seen.intersection(covered):
            report.overlapping.append(Void(row.id, row.chatroom_id, "overlapping_fold"))
            continue
        seen.update(covered)


def _classify(rows: Sequence[Any]) -> RepairReport:
    """Whole-population convenience wrapper over :func:`_classify_page`."""
    report = RepairReport(dry_run=not is_armed())
    _classify_page(rows, report, {})
    return report


async def _void(session: AsyncSession, voids: list[Void]) -> None:
    """Rename the type and move `compacted_ids` aside, one row at a time.

    Soft-deletes the row as well: a voided summary is dead as a projection, and
    leaving it in the feed would keep showing users a divider that no longer
    means anything. `deleted_at` rather than a hard delete, so the text is still
    recoverable if a void turns out to be wrong.
    """
    stamp = now()
    for v in voids:
        row = (
            await session.execute(sa.select(t.messages.c.metadata).where(t.messages.c.id == v.message_id))
        ).first()
        if row is None:
            continue
        meta = dict(row.metadata or {})
        meta[ORIGINAL_COMPACTED_IDS_KEY] = meta.pop(COMPACTED_IDS_KEY, [])
        meta["type"] = VOIDED_SUMMARY_TYPE
        meta["voided_reason"] = v.reason
        await session.execute(
            t.messages.update().where(t.messages.c.id == v.message_id).values(metadata=meta, deleted_at=stamp)
        )
        await audit.emit(
            session,
            audit.AuditEvent(
                action="message.compaction_summary_voided",
                resource_type="message",
                resource_id=v.message_id,
                metadata={"chatroom_id": str(v.chatroom_id), "reason": v.reason},
            ),
        )


async def _repair() -> RepairReport:
    sm = get_sessionmaker()
    report = RepairReport(dry_run=not is_armed())
    claimed: _Claimed = {}
    async with sm() as session:
        # Classify and probe per page, so nothing accumulates across the scan
        # except the findings themselves — which are the output, and small.
        async for page in _iter_summary_pages(session):
            _classify_page(page, report, claimed)

            wanted: set[str] = set()
            for row in page:
                wanted.update(compacted_ids(row.metadata))
            live = await _live_message_ids(session, wanted)
            for row in page:
                missing = [c for c in compacted_ids(row.metadata) if c not in live]
                if missing:
                    report.orphaned.append(Void(row.id, row.chatroom_id, f"missing:{len(missing)}"))

        if not report.dry_run and report.would_void:
            await _void(session, report.empty + report.overlapping)
            await session.commit()
        return report


def run() -> RepairReport:
    """Classify every compaction summary and, when armed, void the bad ones."""
    report = asyncio.run(_repair())
    logger.info(
        "repair-compaction-summaries dry_run={} examined={} empty={} overlapping={} "
        "producerless={} orphaned={}",
        report.dry_run,
        report.examined,
        len(report.empty),
        len(report.overlapping),
        report.producerless,
        len(report.orphaned),
    )
    for v in report.empty + report.overlapping:
        logger.warning("would void summary={} room={} reason={}", v.message_id, v.chatroom_id, v.reason)
    for v in report.orphaned:
        # Not repaired: R13.26 accepts that a user deletion leaves this trace,
        # and after the fact a user deletion is indistinguishable from an old
        # retention purge.
        logger.info("summary covers deleted messages summary={} {}", v.message_id, v.reason)
    return report
