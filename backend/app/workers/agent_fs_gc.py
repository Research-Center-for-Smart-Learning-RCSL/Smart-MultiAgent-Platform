"""Nightly GC for per-agent file-system volumes and workspace objects.

Policy:
- The ``file`` built-in tool stores its state in a Docker named volume
  ``smap-agent-fs-{agent_id}``.
- Designer-uploaded workspace files live in MinIO bucket ``agent-workspace``
  under the ``{agent_id}/`` prefix.
- When an agent is soft-deleted, both are retained for **60 days** so
  admins can recover data before it is irreversibly purged.
- Every night this worker enumerates the volumes and workspace prefixes that
  actually exist, diffs them against the ``agents`` table, and removes the
  artifacts of agents that are gone or whose recovery window has closed.

**Reclaim by enumeration, not by inference.** This worker used to select
``agents`` rows with ``deleted_at`` past the cutoff — the exact rows
``retention_sweep`` hard-deletes 90 minutes earlier (03:30 vs 05:00) against the
exact same 60-day cutoff, and rows a project cascade never stamps at all. It
therefore found nothing, every night, while logging success. Docker is the
authority on which artifacts exist; the DB is the authority on which ones
should. Neither alone is enough.

**Load-bearing invariant — the absence of an ``agents`` row proves the 60-day
window elapsed.** ``_purge_soft_deleted_tenancy`` is the only path that removes
one within the retention policy, and every route through it requires
``deleted_at < now - 60d``: directly on the agent, or by FK cascade from a
project or org whose own ``deleted_at`` was already past the same cutoff. That
is why an orphan needs no timestamp of its own to age against. A future path
that hard-deletes an agent *inside* its window would silently turn this worker
into a data-loss bug — ``tests/unit/test_agent_fs_gc_race.py`` pins the
invariant so that change fails a test instead. (The admin GDPR purge,
``AdminService.hard_delete_user``, does remove rows outside the window; its
artifacts being reclaimed on the next run is the intent of a GDPR purge.)

The ``CreatedAt`` floor is the backstop for orphans the invariant does not
explain — chiefly the race where Docker has auto-created a volume on container
create but the agent's row is not yet visible. It never blocks a legitimate
purge, since a volume is created before its agent is deleted.

**Fail-open.** A GC that leaks is today's bug; a GC that deletes live data is a
much worse one. Every ambiguous case keeps the artifact.

**Dry-run by default.** The sweep is retroactive by construction, so its first
run on a long-lived deployment would purge every artifact leaked since the
feature shipped, at once and irreversibly. It therefore reports what it would
purge and removes nothing until ``SMAP_AGENT_FS_GC_ARMED`` is set. Read a run's
output before arming it.

Docker SDK import is lazy so the module imports cleanly without a daemon; both
enumeration halves are isolated so one failure never strands the other.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import sqlalchemy as sa

from app.workers.tasks.retention import SOFT_DELETE_RETENTION_DAYS
from contexts.agents.infrastructure import tables as t
from contexts.conversation.infrastructure import tables as ct
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.observability.metrics import AGENT_FS_GC_ARTIFACTS

_log = logging.getLogger(__name__)

# Imported, not redeclared: the invariant this worker rests on is that retention
# removes an agents row only past this window. Two independent 60s would let a
# change to one silently invalidate the other.
_RETENTION_DAYS = SOFT_DELETE_RETENTION_DAYS

# The first armed run reclaims every artifact leaked since the feature shipped —
# potentially years of them, each MinIO object its own round trip. The worker's
# default 600 s job_timeout would kill that mid-flight, and the sweep runs in
# `asyncio.to_thread`, which arq cannot actually cancel: the thread would keep
# deleting while the job was marked failed and its metrics never published. Give
# it headroom instead. arq's cron already defaults to max_tries=1, so a timeout
# does not retry this into overlapping destructive sweeps.
AGENT_FS_GC_TIMEOUT_S = 3600

_GC_BATCH_SIZE = 500
_VOLUME_PREFIX = "smap-agent-fs-"
# Per-(agent, chatroom) session volumes ([R12.03b]). One is created per room the
# agent serves, so unlike the per-agent volume these accumulate with use: a
# deployment that never collected them would leak one volume per conversation.
_SESSION_VOLUME_PREFIX = "smap-agent-session-"
_ARMED_ENV = "SMAP_AGENT_FS_GC_ARMED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RFC3339_FRACTION = re.compile(r"^(?P<head>[^.]*)\.(?P<fraction>\d+)(?P<rest>.*)$")

# Reasons _classify can return. `live` and `_EXPECTED_KEEPS` are the healthy
# steady state and must stay quiet: every agent soft-deleted in the last 60 days
# is `in_window`, so logging those at warning would bury the signal this worker
# exists to raise. Only genuinely ambiguous keeps are `declined`.
_EXPECTED_KEEPS = frozenset({"in_window", "orphan_young"})


@dataclass(frozen=True)
class SweepReport:
    """What one pass saw and did. Emitted as metrics and logged.

    Every count is in **artifacts** — one volume, or one agent's workspace
    prefix — so the numbers stay comparable across both halves. ``seen`` counts
    only artifacts whose name parsed as an agent's; anything else on the daemon
    or in the bucket is not ours and is never counted.

    ``objects_removed`` is the one exception, and is a detail *of* ``purged``:
    purging one workspace prefix deletes many objects, and an operator reading a
    dry run wants that number.

    ``retained`` (inside its recovery window, or too young to judge) is the
    healthy state and is separate from ``declined``, which counts only artifacts
    the sweep could not make a confident call on. Conflating them would make
    every deployment look permanently unhealthy.
    """

    seen: int = 0
    live: int = 0
    retained: int = 0
    purged: int = 0
    would_purge: int = 0
    declined: int = 0
    objects_removed: int = 0
    dry_run: bool = True


def _is_armed() -> bool:
    return os.environ.get(_ARMED_ENV, "").strip().lower() in _TRUTHY


def _volume_name(agent_id: uuid.UUID) -> str:
    return f"{_VOLUME_PREFIX}{agent_id}"


def _parse_uuid_strict(text: str) -> uuid.UUID | None:
    """Parse a uuid only in its canonical form.

    ``uuid.UUID()`` alone is not a validator: it accepts braces, a ``urn:uuid:``
    prefix (stripped from anywhere in the string), arbitrary hyphen placement,
    no hyphens at all, uppercase, and non-ASCII digits — mapping all of them to
    the same id. This worker destroys data based on this parse, so require the
    exact form we ourselves emit. Anything else is not an artifact we created,
    and the safe answer is to leave it alone.
    """
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return None
    return parsed if str(parsed) == text else None


def parse_agent_id(name: str) -> uuid.UUID | None:
    """Extract the agent uuid from a volume name, or None if it is not ours.

    Public: `smap.maintenance.purge_session_dirs` enumerates the same volumes and
    must apply the same strictness. A second parser there would be exactly the
    laxity this one exists to prevent.

    A name we cannot parse belongs to something else on the same daemon — the
    host had 144 unrelated volumes when this was written — and must never be
    touched.
    """
    if not name.startswith(_VOLUME_PREFIX):
        return None
    return _parse_uuid_strict(name[len(_VOLUME_PREFIX) :])


def _session_volume_name(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    """Must match ``docker_runsc._session_volume_name`` — see the note there."""
    return f"{_SESSION_VOLUME_PREFIX}{agent_id}-{chatroom_id}"


def _parse_session_ids(name: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Extract ``(agent_id, chatroom_id)`` from a session volume name.

    Both halves are split at a fixed offset rather than by ``rsplit("-")``: a
    canonical uuid contains hyphens, so splitting on the separator would silently
    accept names that are not two whole uuids. Anything that does not parse
    belongs to something else on this daemon and must never be touched.
    """
    if not name.startswith(_SESSION_VOLUME_PREFIX):
        return None
    body = name[len(_SESSION_VOLUME_PREFIX) :]
    canonical_len = 36  # 8-4-4-4-12
    if len(body) != canonical_len * 2 + 1 or body[canonical_len] != "-":
        return None
    agent_id = _parse_uuid_strict(body[:canonical_len])
    chatroom_id = _parse_uuid_strict(body[canonical_len + 1 :])
    if agent_id is None or chatroom_id is None:
        return None
    return agent_id, chatroom_id


def _parse_prefix_agent_id(object_name: str) -> uuid.UUID | None:
    """Extract the agent uuid from a top-level ``{agent_id}/`` common prefix.

    Requires the trailing slash and exactly one path segment: unlike Docker
    volume names, MinIO keys legally contain ``{``, ``:`` and Unicode, so a bare
    object at the bucket root must not be mistaken for an agent's prefix.
    """
    if not object_name.endswith("/"):
        return None
    body = object_name[:-1]
    if "/" in body:
        return None
    return _parse_uuid_strict(body)


def _parse_created_at(raw: str | None) -> datetime | None:
    """Parse Docker's ``CreatedAt``. None (→ keep) if it cannot be read.

    Docker emits Go's RFC3339Nano, whose fraction can carry up to nine digits —
    more than ``fromisoformat`` accepts before 3.11 semantics settle — and whose
    trailing zeros are *trimmed*, so ``.5``, ``.123`` and ``.123456789`` are all
    producible. Isolate the fraction from the offset before truncating: they are
    both digits, and conflating them silently yields a wrong time rather than a
    parse failure.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    match = _RFC3339_FRACTION.match(text)
    if match:
        text = f"{match['head']}.{match['fraction'][:6]}{match['rest']}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def docker_client() -> Any:
    """Seam: the daemon connection, so tests can fake it without one."""
    import docker

    return docker.from_env()


def _minio_client() -> Any:
    """Seam, mirroring ``docker_client``."""
    from shared_kernel.storage import get_minio_client

    return get_minio_client()


def _classify(
    agent_id: uuid.UUID,
    rows: dict[uuid.UUID, datetime | None],
    cutoff: datetime,
    created_at: Callable[[], datetime | None],
) -> tuple[bool, str]:
    """Decide one artifact's fate. Returns (purge?, reason).

    ``created_at`` is a thunk: establishing a workspace prefix's age costs a
    MinIO call, so it is only paid for orphan candidates.
    """
    if agent_id in rows:
        deleted_at = rows[agent_id]
        if deleted_at is None:
            return False, "live"
        if deleted_at >= cutoff:
            return False, "in_window"
        return True, "expired"
    created = created_at()
    if created is None:
        return False, "orphan_age_unknown"
    if created >= cutoff:
        return False, "orphan_young"
    return True, "orphan"


async def _agent_deleted_at(ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime | None]:
    """Map ``{agent_id: deleted_at}`` for every id that HAS a row.

    Ids absent from the result have no row at all. Batched ``IN (...)`` — never
    one query per artifact (§9).
    """
    if not ids:
        return {}
    found: dict[uuid.UUID, datetime | None] = {}
    maker = get_sessionmaker()
    async with maker() as session:
        for start in range(0, len(ids), _GC_BATCH_SIZE):
            chunk = ids[start : start + _GC_BATCH_SIZE]
            rows = (
                await session.execute(
                    sa.select(t.agents.c.id, t.agents.c.deleted_at).where(t.agents.c.id.in_(chunk))
                )
            ).all()
            for row in rows:
                found[row[0]] = row[1]
    return found


async def _chatroom_deleted_at(ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime | None]:
    """Map ``{chatroom_id: deleted_at}`` for every id that HAS a row.

    Mirrors :func:`_agent_deleted_at` exactly, and for the same reason: a session
    volume outlives its room, and the same retention sweep hard-deletes chatrooms
    and agents against the same cutoff (``retention._SOFT_DELETE_TABLES``), so
    "no row" carries the identical meaning on both dimensions.
    """
    if not ids:
        return {}
    found: dict[uuid.UUID, datetime | None] = {}
    maker = get_sessionmaker()
    async with maker() as session:
        for start in range(0, len(ids), _GC_BATCH_SIZE):
            chunk = ids[start : start + _GC_BATCH_SIZE]
            rows = (
                await session.execute(
                    sa.select(ct.chatrooms.c.id, ct.chatrooms.c.deleted_at).where(
                        ct.chatrooms.c.id.in_(chunk)
                    )
                )
            ).all()
            for row in rows:
                found[row[0]] = row[1]
    return found


async def _chatrooms_table_has_any() -> bool:
    """The chatrooms half of the blast-radius probe (see :func:`_agents_table_has_any`).

    Without it, a wrong DSN or a restoring replica reads as "every chatroom is
    gone", and every session volume on the host classifies as garbage.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        found = (await session.execute(sa.select(ct.chatrooms.c.id).limit(1))).first()
    return found is not None


async def _agents_table_has_any() -> bool:
    """Is there ANY agents row at all? Distinguishes 'this host's agents are all
    gone' (orphans — purge them) from 'this is not the right database' (refuse).
    Only asked when nothing matched, so it costs nothing on the normal path."""
    maker = get_sessionmaker()
    async with maker() as session:
        found = (await session.execute(sa.select(t.agents.c.id).limit(1))).first()
    return found is not None


def _enumerate_volumes() -> list[tuple[uuid.UUID, Any]]:
    """Every ``smap-agent-fs-{uuid}`` volume on the daemon, as (agent_id, volume).

    Isolated: a daemon failure yields nothing rather than aborting the MinIO half.
    ``volumes.list()`` is a single unpaginated call, acceptable nightly (§9).
    """
    try:
        client = docker_client()
        volumes = client.volumes.list()
    except Exception as exc:
        _log.warning("agent_fs_gc: volume enumeration failed, skipping volume half: %s", exc)
        return []
    found: list[tuple[uuid.UUID, Any]] = []
    for volume in volumes:
        agent_id = parse_agent_id(getattr(volume, "name", "") or "")
        if agent_id is not None:
            found.append((agent_id, volume))
    return found


def _enumerate_session_volumes() -> list[tuple[uuid.UUID, uuid.UUID, Any]]:
    """Every ``smap-agent-session-{agent}-{room}`` volume, as (agent, room, volume).

    Separate pass over the same listing as :func:`_enumerate_volumes` rather than
    one loop returning both shapes: the two are classified against different
    tables and guarded separately, and fusing them would couple those.
    """
    try:
        client = docker_client()
        volumes = client.volumes.list()
    except Exception as exc:
        _log.warning("agent_fs_gc: session volume enumeration failed, skipping that half: %s", exc)
        return []
    found: list[tuple[uuid.UUID, uuid.UUID, Any]] = []
    for volume in volumes:
        ids = _parse_session_ids(getattr(volume, "name", "") or "")
        if ids is not None:
            found.append((ids[0], ids[1], volume))
    return found


def _sweep_session_volumes(
    volumes: list[tuple[uuid.UUID, uuid.UUID, Any]],
    agent_rows: dict[uuid.UUID, datetime | None],
    room_rows: dict[uuid.UUID, datetime | None],
    cutoff: datetime,
    armed: bool,
) -> SweepReport:
    """Collect a session volume once EITHER its agent or its chatroom is gone.

    Both dimensions run through :func:`_classify`, so a soft-deleted room keeps
    its volume for the same recovery window a soft-deleted agent does, and the
    `orphan_young` floor still protects a volume Docker auto-created moments ago.
    """
    live = retained = purged = would = declined = 0
    for agent_id, chatroom_id, volume in volumes:
        name = _session_volume_name(agent_id, chatroom_id)
        age = partial(_volume_created_at, volume)
        try:
            agent_purge, agent_reason = _classify(agent_id, agent_rows, cutoff, age)
            room_purge, room_reason = _classify(chatroom_id, room_rows, cutoff, age)
        except Exception as exc:
            declined += 1
            _log.warning("agent_fs_gc: failed to age session volume %s: %s", name, exc)
            continue
        should_purge = agent_purge or room_purge
        # Name the dimension that decided, so an operator reading the log knows
        # whether the agent or the room went away.
        reason = f"agent:{agent_reason}" if agent_purge else f"room:{room_reason}"
        if not should_purge:
            if agent_reason == "live" and room_reason == "live":
                live += 1
            elif agent_reason in _EXPECTED_KEEPS or room_reason in _EXPECTED_KEEPS:
                retained += 1
                _log.debug("agent_fs_gc: keeping session volume %s (%s/%s)", name, agent_reason, room_reason)
            else:
                declined += 1
                _log.warning(
                    "agent_fs_gc: declining to purge session volume %s (%s/%s)",
                    name,
                    agent_reason,
                    room_reason,
                )
            continue
        would += 1
        disposition = _remove_or_report(volume, name, reason, "session volume", armed=armed)
        if disposition == "purged":
            purged += 1
        elif disposition == "declined":
            declined += 1
    return SweepReport(
        seen=len(volumes),
        live=live,
        retained=retained,
        purged=purged,
        would_purge=would,
        declined=declined,
        dry_run=not armed,
    )


def _enumerate_workspace_prefixes(client: Any) -> list[uuid.UUID]:
    """Every top-level ``{agent_id}/`` prefix in the agent-workspace bucket.

    ``recursive=False`` returns common prefixes only, so a live agent's objects
    are never materialised (FU-1).
    """
    try:
        entries = client.list_objects_sync(client.agent_workspace_bucket, recursive=False)
    except Exception as exc:
        _log.warning("agent_fs_gc: workspace enumeration failed, skipping workspace half: %s", exc)
        return []
    found = []
    for entry in entries:
        agent_id = _parse_prefix_agent_id(getattr(entry, "object_name", "") or "")
        if agent_id is not None:
            found.append(agent_id)
    return found


def _volume_created_at(volume: Any) -> datetime | None:
    return _parse_created_at(volume.attrs.get("CreatedAt"))


def _newest_object(objects: Iterable[Any]) -> datetime | None:
    """Age a prefix by its newest object. None (→ keep) if ANY object lacks a
    timestamp: an unstamped object could be arbitrarily recent, and a prefix is
    purged whole, so one unknown makes the whole prefix unknown."""
    stamps = []
    for obj in objects:
        stamp = getattr(obj, "last_modified", None)
        if stamp is None:
            return None
        stamps.append(stamp)
    return max(stamps) if stamps else None


def _prefix_newest(client: Any, bucket: str, prefix: str, sink: list[Any]) -> datetime | None:
    """Age a workspace prefix by its newest object, filling `sink` with the
    listing so the caller can reuse it for the delete."""
    sink.extend(client.list_objects_sync(bucket, prefix=prefix))
    return _newest_object(sink)


def _remove_or_report(volume: Any, name: str, reason: str, kind: str, *, armed: bool) -> str:
    """Perform the destructive step for one artifact. Returns its disposition.

    Shared by both sweeps: this is the only code in the module that deletes
    anything, and duplicating it put the ``force=False`` decision below -- the
    module's single most consequential line -- in two places that could drift
    apart. Classification stays in the callers, which genuinely differ (one
    table vs two).

    Returns one of ``"would"`` / ``"purged"`` / ``"declined"``.
    """
    if not armed:
        _log.warning(
            "agent_fs_gc: DRY RUN would purge %s %s (%s) — set %s to arm", kind, name, reason, _ARMED_ENV
        )
        return "would"
    try:
        # force=False deliberately: the daemon's "volume is in use by container
        # X" refusal is the one guard here that does not depend on the DB join
        # being right, and it is the last thing standing between a
        # misclassification and a running agent's live data.
        volume.remove(force=False)
    except Exception as exc:
        _log.warning("agent_fs_gc: failed to remove %s: %s", name, exc)
        return "declined"
    _log.info("agent_fs_gc: removed %s %s (%s)", kind, name, reason)
    return "purged"


def _sweep_volumes(
    volumes: list[tuple[uuid.UUID, Any]],
    rows: dict[uuid.UUID, datetime | None],
    cutoff: datetime,
    armed: bool,
) -> SweepReport:
    live = retained = purged = would = declined = 0
    for agent_id, volume in volumes:
        name = _volume_name(agent_id)
        try:
            should_purge, reason = _classify(agent_id, rows, cutoff, partial(_volume_created_at, volume))
        except Exception as exc:
            declined += 1
            _log.warning("agent_fs_gc: failed to age volume %s: %s", name, exc)
            continue
        if not should_purge:
            if reason == "live":
                live += 1
            elif reason in _EXPECTED_KEEPS:
                retained += 1
                _log.debug("agent_fs_gc: keeping volume %s (%s)", name, reason)
            else:
                declined += 1
                _log.warning("agent_fs_gc: declining to purge volume %s (%s)", name, reason)
            continue
        would += 1
        disposition = _remove_or_report(volume, name, reason, "volume", armed=armed)
        if disposition == "purged":
            purged += 1
        elif disposition == "declined":
            declined += 1
    return SweepReport(
        seen=len(volumes),
        live=live,
        retained=retained,
        purged=purged,
        would_purge=would,
        declined=declined,
        dry_run=not armed,
    )


def _sweep_workspace_objects(
    client: Any,
    prefixes: list[uuid.UUID],
    rows: dict[uuid.UUID, datetime | None],
    cutoff: datetime,
    armed: bool,
) -> SweepReport:
    bucket = client.agent_workspace_bucket
    live = retained = purged = would = declined = removed_objects = 0
    for agent_id in prefixes:
        prefix = f"{agent_id}/"
        # Listing a prefix's objects is the only way to age it, and the same list
        # is what we delete — so cache it across the two steps rather than paying
        # MinIO twice. A live agent's prefix is never listed at all (FU-1).
        objects: list[Any] = []
        try:
            should_purge, reason = _classify(
                agent_id, rows, cutoff, partial(_prefix_newest, client, bucket, prefix, objects)
            )
        except Exception as exc:
            _log.warning("agent_fs_gc: failed to age workspace prefix %s: %s", prefix, exc)
            declined += 1
            continue
        if not should_purge:
            if reason == "live":
                live += 1
            elif reason in _EXPECTED_KEEPS:
                retained += 1
                _log.debug("agent_fs_gc: keeping workspace prefix %s (%s)", prefix, reason)
            else:
                declined += 1
                _log.warning("agent_fs_gc: declining to purge workspace prefix %s (%s)", prefix, reason)
            continue
        try:
            if not objects:
                # `expired` short-circuits _classify before the age thunk runs.
                objects.extend(client.list_objects_sync(bucket, prefix=prefix))
            names = [o.object_name for o in objects if o.object_name]
            would += 1
            if not armed:
                _log.warning(
                    "agent_fs_gc: DRY RUN would purge workspace prefix %s (%s, %d objects) — set %s to arm",
                    prefix,
                    reason,
                    len(names),
                    _ARMED_ENV,
                )
                continue
            for name in names:
                client.remove_object_sync(bucket, name)
                removed_objects += 1
            purged += 1
            _log.info("agent_fs_gc: removed workspace prefix %s (%s, %d objects)", prefix, reason, len(names))
        except Exception as exc:
            declined += 1
            _log.warning("agent_fs_gc: failed to purge workspace objects for %s: %s", agent_id, exc)
    return SweepReport(
        seen=len(prefixes),
        live=live,
        retained=retained,
        purged=purged,
        would_purge=would,
        declined=declined,
        objects_removed=removed_objects,
        dry_run=not armed,
    )


def _publish(kind: str, report: SweepReport) -> None:
    for disposition, value in (
        ("seen", report.seen),
        ("live", report.live),
        ("retained", report.retained),
        ("purged", report.purged),
        ("would_purge", report.would_purge),
        ("declined", report.declined),
    ):
        AGENT_FS_GC_ARTIFACTS.labels(kind=kind, disposition=disposition).set(value)


def sweep_report_dict(report: SweepReport) -> dict[str, int]:
    """The arq job result. `dry_run` is included because an unarmed sweep with a
    non-zero `would_purge` is the normal state and must not read as a no-op."""
    return {
        "seen": report.seen,
        "live": report.live,
        "retained": report.retained,
        "removed": report.purged,
        "objects_removed": report.objects_removed,
        "would_purge": report.would_purge,
        "declined": report.declined,
        "dry_run": int(report.dry_run),
    }


async def sweep_once(*, now: datetime | None = None) -> SweepReport:
    """Single pass over both halves. Returns the combined report.

    Raises if the ``agents`` liveness query fails: nothing is purged, because
    liveness is unknown and 'no row' must never be inferred from an error (§9).
    """
    ts = now or datetime.now(tz=UTC)
    cutoff = ts - timedelta(days=_RETENTION_DAYS)
    armed = _is_armed()

    volumes = await asyncio.to_thread(_enumerate_volumes)
    session_volumes = await asyncio.to_thread(_enumerate_session_volumes)
    # Each half is isolated: a Docker hiccup must not strand the workspace
    # objects for another day, and vice versa.
    try:
        minio: Any | None = _minio_client()
        prefixes = await asyncio.to_thread(_enumerate_workspace_prefixes, minio)
    except Exception as exc:
        _log.warning("agent_fs_gc: MinIO unavailable, skipping workspace half: %s", exc)
        minio, prefixes = None, []

    ids = sorted(
        {agent_id for agent_id, _ in volumes}
        | {agent_id for agent_id, _, _ in session_volumes}
        | set(prefixes),
        key=str,
    )
    rows = await _agent_deleted_at(ids)
    room_ids = sorted({chatroom_id for _, chatroom_id, _ in session_volumes}, key=str)
    room_rows = await _chatroom_deleted_at(room_ids)

    # Blast-radius guard. Refusing to infer "no row" from an *exception* is not
    # enough: a query that succeeds and returns nothing carries the same meaning
    # with none of the alarm — it says every artifact on this host is an orphan,
    # and one armed run would destroy every tenant's data irreversibly. A wrong
    # DSN, a re-seeded replica, a restore in progress, or a search_path that
    # resolves `agents` elsewhere all look exactly like that.
    #
    # `rows` being empty is NOT itself the signal: a host whose every agent was
    # deleted and reaped legitimately has orphans and no matching rows, and
    # purging those is the entire point of this worker. The signal is the agents
    # table being empty *altogether*, which no live deployment holding agent
    # volumes can be. Distinguish the two with one cheap probe.
    if ids and not rows and not await _agents_table_has_any():
        _log.error(
            "agent_fs_gc: %d agent artifacts exist on this host but the agents table is empty — "
            "refusing to sweep. That is indistinguishable from a wrong or restoring database, and "
            "purging on it would destroy every tenant's data. Nothing was removed. If every agent "
            "really is gone, purge manually.",
            len(ids),
        )
        report = SweepReport(seen=len(ids), declined=len(ids), dry_run=not armed)
        _publish("volume", report)
        return report

    # Same guard, chatrooms dimension. A session volume is judged on two tables,
    # so an empty `chatrooms` would classify every one of them as garbage on its
    # own — the agents probe above cannot see that, because the agents table may
    # be perfectly healthy while the conversation half is not.
    if room_ids and not room_rows and not await _chatrooms_table_has_any():
        _log.error(
            "agent_fs_gc: %d session volumes exist on this host but the chatrooms table is empty — "
            "refusing to sweep them. Nothing was removed.",
            len(room_ids),
        )
        session_report = SweepReport(
            seen=len(session_volumes), declined=len(session_volumes), dry_run=not armed
        )
    else:
        session_report = await asyncio.to_thread(
            _sweep_session_volumes, session_volumes, rows, room_rows, cutoff, armed
        )
    _publish("session_volume", session_report)

    vol_report = await asyncio.to_thread(_sweep_volumes, volumes, rows, cutoff, armed)
    _publish("volume", vol_report)

    obj_report = SweepReport(dry_run=not armed)
    if minio is not None:
        obj_report = await asyncio.to_thread(_sweep_workspace_objects, minio, prefixes, rows, cutoff, armed)
        _publish("workspace_prefix", obj_report)
    # else: leave the workspace gauges at their last good values rather than
    # stamping zeros — a half that could not run must not read as "nothing to do".

    combined = SweepReport(
        seen=vol_report.seen + obj_report.seen + session_report.seen,
        live=vol_report.live + obj_report.live + session_report.live,
        retained=vol_report.retained + obj_report.retained + session_report.retained,
        purged=vol_report.purged + obj_report.purged + session_report.purged,
        would_purge=vol_report.would_purge + obj_report.would_purge + session_report.would_purge,
        declined=vol_report.declined + obj_report.declined + session_report.declined,
        objects_removed=obj_report.objects_removed,
        dry_run=not armed,
    )
    # An empty work list is no longer the success case: the old worker logged the
    # same healthy line whether or not it had anything to do, which is why this
    # bug survived to production (§5). Escalate only on states that need a human:
    # work waiting behind the safety catch, or artifacts we could not judge.
    # `retained` is deliberately not a trigger — it is non-zero on every healthy
    # deployment, so warning on it would make the warning meaningless.
    needs_attention = bool(combined.declined) or (combined.dry_run and combined.would_purge)
    _log.log(
        logging.WARNING if needs_attention else logging.INFO,
        "agent_fs_gc: seen=%d live=%d retained=%d purged=%d (%d objects) would_purge=%d "
        "declined=%d dry_run=%s",
        combined.seen,
        combined.live,
        combined.retained,
        combined.purged,
        combined.objects_removed,
        combined.would_purge,
        combined.declined,
        combined.dry_run,
    )
    return combined


async def run_once(*, now: datetime | None = None) -> int:
    """Single pass — exposed for tests + manual invocation. Returns artifacts purged."""
    report = await sweep_once(now=now)
    return report.purged


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await run_once()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())


__all__ = ["AGENT_FS_GC_TIMEOUT_S", "SweepReport", "run_once", "sweep_once", "sweep_report_dict"]
