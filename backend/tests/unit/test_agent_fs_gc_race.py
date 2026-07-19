"""Unit tests for the per-agent filesystem GC (docs/tasks/2026-07-17-agent-fs-gc-retention-race).

The GC used to take its work list from ``agents`` rows with ``deleted_at`` past
the 60-day cutoff — the exact rows ``retention_sweep`` hard-deletes 90 minutes
earlier, and rows a project cascade never stamps at all. It therefore found
nothing, every night. These tests pin the reclamation sweep that replaced it:
enumerate what exists, diff against what should, and purge the difference.

The Docker SDK is a hard dependency but the daemon is not, so ``_docker_client``
is the seam every test fakes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.workers import agent_fs_gc as gc

_NOW = datetime(2026, 7, 17, 5, 0, 0, tzinfo=UTC)
_WELL_INSIDE = _NOW - timedelta(days=1)
_WELL_PAST = _NOW - timedelta(days=61)
_ANCIENT = _NOW - timedelta(days=400)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeVolume:
    """Mirrors docker.models.volumes.Volume: `.name`, `.attrs["CreatedAt"]`.

    Verified against a real daemon: `CreatedAt` is Go's RFC3339Nano, which trims
    trailing zeros — so `.5`, `.123` and `.123456789` are all producible. The
    fake emits a fraction by default because a second-precision fake hid a
    parser bug that returned None for `.123Z`.
    """

    def __init__(self, name: str, created_at: datetime, *, stamp: str | None = None) -> None:
        self.name = name
        self.attrs = {"CreatedAt": stamp or created_at.strftime("%Y-%m-%dT%H:%M:%S.123Z")}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeVolumeCollection:
    def __init__(self, volumes: list[_FakeVolume]) -> None:
        self._volumes = volumes

    def list(self) -> list[_FakeVolume]:
        return list(self._volumes)


class _FakeDocker:
    def __init__(self, volumes: list[_FakeVolume]) -> None:
        self.volumes = _FakeVolumeCollection(volumes)


class _FakeObject:
    def __init__(self, object_name: str, last_modified: datetime | None = None) -> None:
        self.object_name = object_name
        self.last_modified = last_modified


class _FakeMinio:
    """Enough of the MinIO client for the workspace half.

    ``recursive=False`` returns top-level common prefixes (``object_name``
    ending in ``/``); ``recursive=True`` returns the objects under a prefix.
    """

    def __init__(self, objects: dict[str, list[_FakeObject]]) -> None:
        self._objects = objects
        self.removed: list[str] = []

    @property
    def agent_workspace_bucket(self) -> str:
        return "agent-workspace"

    def list_objects_sync(
        self, bucket: str, *, prefix: str | None = None, recursive: bool = True
    ) -> list[_FakeObject]:
        if not recursive:
            return [_FakeObject(p) for p in self._objects]
        return list(self._objects.get(prefix or "", []))

    def remove_object_sync(self, bucket: str, key: str) -> None:
        self.removed.append(key)


def _sessionmaker_returning(
    rows: dict[uuid.UUID, datetime | None],
    *,
    populated: bool = True,
    room_rows: dict[uuid.UUID, datetime | None] | None = None,
    rooms_populated: bool = True,
):
    """Fake sessionmaker modelling the queries the sweep makes.

    `rows` is the ``{agent_id: deleted_at}`` the id lookup finds; ids absent from
    it have no ``agents`` row — the orphan case. `room_rows` is the same for
    ``chatrooms``, which session volumes are judged against as well ([R12.03b]).

    `populated` / `rooms_populated` answer the separate "is this table empty
    altogether?" probes. Both default to True because that is reality: a host
    holding these volumes has rows in both. False is the wrong-database case,
    and only the blast-radius guards' tests set it.

    Statements are routed by target table rather than by call order: the two
    dimensions must be independently falsifiable, or a test claiming the
    chatrooms guard fires would pass on the agents guard firing instead.
    """
    room_rows = room_rows or {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def execute(self, stmt):
            is_rooms = "chatrooms" in str(stmt)
            table_rows = room_rows if is_rooms else rows
            table_populated = rooms_populated if is_rooms else populated
            # `col.in_(chunk)` compiles to an EXPANDING bindparam, so the param
            # value is a LIST of uuids, not a uuid. Flatten it — reading only
            # scalars here silently matched everything and left _agent_deleted_at's
            # id filtering and batching untested.
            values = stmt.compile().params.values()
            requested = {
                item
                for value in values
                for item in (value if isinstance(value, list) else [value])
                if isinstance(item, uuid.UUID)
            }
            if not requested:
                # No uuid params — the "does the table hold anything" probe.
                probe = (uuid.uuid4(),) if table_populated else None
                return SimpleNamespace(first=lambda: probe, all=lambda: [probe] if probe else [])
            matched = [(k, v) for k, v in table_rows.items() if k in requested]
            return SimpleNamespace(all=lambda: matched, first=lambda: matched[0] if matched else None)

    # get_sessionmaker() returns the maker; the maker is then called per session.
    return lambda: _Session


@pytest.fixture
def armed(monkeypatch):
    """Arm the sweep. Disarmed is the default (D-2) — see the dry-run test."""
    monkeypatch.setenv(gc._ARMED_ENV, "true")


def _install(
    monkeypatch,
    *,
    volumes=None,
    objects=None,
    rows=None,
    populated=True,
    room_rows=None,
    rooms_populated=True,
) -> _FakeMinio:
    docker = _FakeDocker(volumes or [])
    minio = _FakeMinio(objects or {})
    monkeypatch.setattr(gc, "_docker_client", lambda: docker)
    monkeypatch.setattr(gc, "_minio_client", lambda: minio)
    monkeypatch.setattr(
        gc,
        "get_sessionmaker",
        _sessionmaker_returning(
            rows or {}, populated=populated, room_rows=room_rows, rooms_populated=rooms_populated
        ),
    )
    return minio


# ---------------------------------------------------------------------------
# AC-1 / AC-2 / AC-3 — the headline: an artifact with no agents row is purged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_volume_with_no_agents_row_is_purged(monkeypatch, armed) -> None:
    """AC-1. Fails before the fix: _list_purgeable_agent_ids returns [] and
    run_once exits 0. This single case pins BOTH roads — retention having
    hard-deleted the row at 03:30 (Road A) and a project cascade having taken it
    (Road B) both present here as 'a volume whose uuid has no row'."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={})

    purged = await gc.run_once(now=_NOW)

    assert vol.removed is True
    assert purged == 1


@pytest.mark.asyncio
async def test_expired_agent_row_still_present_is_purged(monkeypatch, armed) -> None:
    """AC-7's other half: when retention's 200-row batch limit leaves the row
    behind, the GC purges from the row itself. Same set, either order."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={agent_id: _WELL_PAST})

    await gc.run_once(now=_NOW)

    assert vol.removed is True


# ---------------------------------------------------------------------------
# AC-4 / AC-9 — the guards, which are what make the first run safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_agent_volume_is_never_purged_however_old(monkeypatch, armed) -> None:
    """AC-4. CreatedAt is ancient but the agent is alive — liveness wins."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={agent_id: None})

    purged = await gc.run_once(now=_NOW)

    assert vol.removed is False
    assert purged == 0


@pytest.mark.asyncio
async def test_agent_deleted_inside_the_window_is_kept(monkeypatch, armed) -> None:
    """AC-4. Deleted yesterday — squarely inside the 60-day recovery window."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={agent_id: _WELL_INSIDE})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_unparseable_volume_name_is_never_touched(monkeypatch, armed) -> None:
    """AC-4. Someone else's volume on the same daemon."""
    foreign = _FakeVolume("postgres-data", _ANCIENT)
    almost = _FakeVolume("smap-agent-fs-not-a-uuid", _ANCIENT)
    _install(monkeypatch, volumes=[foreign, almost], rows={})

    purged = await gc.run_once(now=_NOW)

    assert foreign.removed is False
    assert almost.removed is False
    assert purged == 0


@pytest.mark.asyncio
async def test_young_orphan_is_kept_by_the_floor(monkeypatch, armed) -> None:
    """AC-4, and the floor's real job: Docker auto-creates the volume on
    container create, so a just-created agent can have a volume before its row
    is visible. Keep anything younger than the window."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _WELL_INSIDE)
    _install(monkeypatch, volumes=[vol], rows={})

    purged = await gc.run_once(now=_NOW)

    assert vol.removed is False
    assert purged == 0


@pytest.mark.asyncio
async def test_orphan_with_unparseable_created_at_is_kept(monkeypatch, armed) -> None:
    """Fail-open (§9): an artifact whose age cannot be established is kept."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT, stamp="not-a-timestamp")
    _install(monkeypatch, volumes=[vol], rows={})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_redeleted_agent_ages_from_its_latest_deletion(monkeypatch, armed) -> None:
    """AC-9, the sharp one. Deleted 100d ago, restored 99d ago, deleted again
    yesterday. The agent has been deleted for ONE day, not a hundred: keep.

    D-1 makes this pass by construction — deleted_at is the only clock and
    restore rewrites it — but it is exactly what any reintroduced carried
    timestamp (a tombstone, a volume marker) would get wrong, which is why it
    stays in the suite.
    """
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _NOW - timedelta(days=100))
    _install(monkeypatch, volumes=[vol], rows={agent_id: _WELL_INSIDE})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_a_live_volume_is_never_force_removed(monkeypatch, armed) -> None:
    """The daemon's 'volume is in use' refusal is the only guard here that does
    not depend on the DB join being right, so the sweep must not use force=True
    and discard it."""
    agent_id = uuid.uuid4()
    calls: list[bool] = []

    class _Recording(_FakeVolume):
        def remove(self, force: bool = False) -> None:
            calls.append(force)
            self.removed = True

    vol = _Recording(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={})

    await gc.run_once(now=_NOW)

    assert calls == [False], "force=True would override the daemon's in-use refcount check"


@pytest.mark.asyncio
async def test_a_volume_the_daemon_refuses_is_declined_not_retried(monkeypatch, armed) -> None:
    """If the daemon says the volume is in use, that is a decline — the sweep
    must not treat it as purged."""
    agent_id = uuid.uuid4()

    class _InUse(_FakeVolume):
        def remove(self, force: bool = False) -> None:
            raise RuntimeError("volume is in use by container abc123")

    _install(monkeypatch, volumes=[_InUse(gc._volume_name(agent_id), _ANCIENT)], rows={})

    report = await gc.sweep_once(now=_NOW)

    assert report.purged == 0
    assert report.declined == 1


# ---------------------------------------------------------------------------
# AC-5 — the MinIO half, on the same terms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphaned_workspace_prefix_is_reclaimed(monkeypatch, armed) -> None:
    """AC-5. The agent-workspace bucket has no TTL by design, so this sweep is
    the only path that would ever remove these objects."""
    agent_id = uuid.uuid4()
    objects = {
        f"{agent_id}/": [
            _FakeObject(f"{agent_id}/notes.md", _ANCIENT),
            _FakeObject(f"{agent_id}/data.csv", _ANCIENT),
        ]
    }
    minio = _install(monkeypatch, objects=objects, rows={})

    report = await gc.sweep_once(now=_NOW)

    assert sorted(minio.removed) == sorted([f"{agent_id}/notes.md", f"{agent_id}/data.csv"])
    # Counts are in artifacts: one prefix purged, two objects deleted doing it.
    assert report.purged == 1
    assert report.objects_removed == 2


@pytest.mark.asyncio
async def test_live_agents_workspace_prefix_is_left_alone(monkeypatch, armed) -> None:
    """AC-5's guard."""
    agent_id = uuid.uuid4()
    objects = {f"{agent_id}/": [_FakeObject(f"{agent_id}/notes.md", _ANCIENT)]}
    minio = _install(monkeypatch, objects=objects, rows={agent_id: None})

    await gc.run_once(now=_NOW)

    assert minio.removed == []


@pytest.mark.asyncio
async def test_recently_touched_orphan_prefix_is_kept(monkeypatch, armed) -> None:
    """The floor applies to the workspace half too: a prefix whose newest object
    is inside the window is kept."""
    agent_id = uuid.uuid4()
    objects = {
        f"{agent_id}/": [
            _FakeObject(f"{agent_id}/old.md", _ANCIENT),
            _FakeObject(f"{agent_id}/fresh.md", _WELL_INSIDE),
        ]
    }
    minio = _install(monkeypatch, objects=objects, rows={})

    await gc.run_once(now=_NOW)

    assert minio.removed == []


# ---------------------------------------------------------------------------
# AC-10 — the dry-run default (D-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_is_the_default_and_purges_nothing(monkeypatch) -> None:
    """AC-10. Note: no `armed` fixture. An artifact that satisfies every purge
    condition must survive an unarmed run, and still be reported."""
    agent_id = uuid.uuid4()
    monkeypatch.delenv(gc._ARMED_ENV, raising=False)
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    objects = {f"{agent_id}/": [_FakeObject(f"{agent_id}/notes.md", _ANCIENT)]}
    minio = _install(monkeypatch, volumes=[vol], objects=objects, rows={})

    report = await gc.sweep_once(now=_NOW)

    assert vol.removed is False
    assert minio.removed == []
    assert report.purged == 0
    assert report.would_purge == 2  # the volume + its one workspace object
    assert report.dry_run is True


@pytest.mark.asyncio
async def test_arming_the_sweep_makes_it_purge(monkeypatch, armed) -> None:
    """The other side of AC-10 — the flag is what separates the two."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={})

    report = await gc.sweep_once(now=_NOW)

    assert vol.removed is True
    assert report.dry_run is False
    assert report.purged == 1


# ---------------------------------------------------------------------------
# AC-6 — the sweep reports what it saw; empty is no longer "success"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_counts_seen_live_and_declined(monkeypatch, armed) -> None:
    """AC-6. §5's 'why it is invisible': the old worker logged the same
    healthy-looking line whether it had work or not."""
    live_id, expired_id, young_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    volumes = [
        _FakeVolume(gc._volume_name(live_id), _ANCIENT),
        _FakeVolume(gc._volume_name(expired_id), _ANCIENT),
        _FakeVolume(gc._volume_name(young_id), _WELL_INSIDE),
    ]
    _install(monkeypatch, volumes=volumes, rows={live_id: None})

    report = await gc.sweep_once(now=_NOW)

    assert report.seen == 3
    assert report.live == 1
    assert report.purged == 1
    # The young orphan is `retained`, NOT `declined`: it is the healthy expected
    # state, and lumping it in with genuinely-unjudgeable artifacts is what would
    # make the declined signal (and its WARNING) permanently meaningless.
    assert report.retained == 1
    assert report.declined == 0


@pytest.mark.asyncio
async def test_the_healthy_steady_state_does_not_warn(monkeypatch, armed, caplog) -> None:
    """AC-6's other edge. Every agent soft-deleted in the last 60 days is
    `in_window` — the normal state of any real deployment. If that logged at
    WARNING it would do so nightly, forever, burying the signal this worker
    exists to raise. Warnings must mean something."""
    live_id, in_window_id = uuid.uuid4(), uuid.uuid4()
    volumes = [
        _FakeVolume(gc._volume_name(live_id), _ANCIENT),
        _FakeVolume(gc._volume_name(in_window_id), _ANCIENT),
    ]
    _install(monkeypatch, volumes=volumes, rows={live_id: None, in_window_id: _WELL_INSIDE})

    with caplog.at_level("DEBUG", logger=gc.__name__):
        report = await gc.sweep_once(now=_NOW)

    assert report.retained == 1
    assert report.declined == 0
    warnings = [r for r in caplog.records if r.levelno >= 30]
    assert warnings == [], f"healthy sweep emitted warnings: {[r.getMessage() for r in warnings]}"


# ---------------------------------------------------------------------------
# Fail-open (§9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_agents_table_refuses_to_sweep(monkeypatch, armed) -> None:
    """The blast-radius guard, and the worst realistic failure this worker has.

    A query that SUCCEEDS and returns nothing says 'every artifact here is an
    orphan' — which is exactly what a wrong DSN, a re-seeded replica, or a
    restore-in-progress looks like. Purging on that destroys every tenant's data
    in one armed run. Refuse instead; a leak survives one more night.
    """
    volumes = [_FakeVolume(gc._volume_name(uuid.uuid4()), _ANCIENT) for _ in range(3)]
    _install(monkeypatch, volumes=volumes, rows={}, populated=False)

    report = await gc.sweep_once(now=_NOW)

    assert all(v.removed is False for v in volumes)
    assert report.purged == 0
    assert report.declined == 3


@pytest.mark.asyncio
async def test_orphans_are_purged_when_other_agents_are_live(monkeypatch, armed) -> None:
    """The other side of the guard: with at least one live row proving the DB is
    real, genuine orphans are still reclaimed — the whole point of the fix."""
    live_id, orphan_id = uuid.uuid4(), uuid.uuid4()
    live_vol = _FakeVolume(gc._volume_name(live_id), _ANCIENT)
    orphan_vol = _FakeVolume(gc._volume_name(orphan_id), _ANCIENT)
    _install(monkeypatch, volumes=[live_vol, orphan_vol], rows={live_id: None})

    await gc.run_once(now=_NOW)

    assert orphan_vol.removed is True
    assert live_vol.removed is False


@pytest.mark.asyncio
async def test_db_failure_purges_nothing(monkeypatch, armed) -> None:
    """§9: never infer 'no row' from an error. An artifact whose liveness cannot
    be established is kept."""
    agent_id = uuid.uuid4()
    vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    _install(monkeypatch, volumes=[vol], rows={})

    def _boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(gc, "get_sessionmaker", _boom)

    with pytest.raises(RuntimeError):
        await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_docker_enumeration_failure_does_not_block_the_minio_half(monkeypatch, armed) -> None:
    """A daemon hiccup must not strand the workspace objects for another day."""
    agent_id = uuid.uuid4()
    objects = {f"{agent_id}/": [_FakeObject(f"{agent_id}/notes.md", _ANCIENT)]}
    minio = _install(monkeypatch, objects=objects, rows={})

    def _boom():
        raise RuntimeError("docker daemon unreachable")

    monkeypatch.setattr(gc, "_docker_client", _boom)

    await gc.run_once(now=_NOW)

    assert minio.removed == [f"{agent_id}/notes.md"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestTheInvariantTheSweepRestsOn:
    """AC-11 / §8.6. The sweep purges an orphan on the reasoning that 'no agents
    row' *proves* the 60-day window elapsed. That holds only while retention is
    the sole remover of agents rows within the retention policy, and only ever
    past the cutoff.

    These are characterization tests: they pass today. Their job is to fail the
    day someone lets retention delete an agent row inside its window, which would
    otherwise silently convert this GC from a leak into a data-loss bug with
    nothing else in the suite noticing.
    """

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_retention_only_deletes_agent_rows_past_the_cutoff(self, _now, _audit) -> None:
        from app.workers.tasks.retention import _purge_soft_deleted_tenancy

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 0
        result.scalars.return_value.all.return_value = []  # no doomed projects (F-24 teardown)
        session.execute.return_value = result

        await _purge_soft_deleted_tenancy(session)

        deletes = [
            call.args[0]
            for call in session.execute.await_args_list
            if isinstance(call.args[0], sa.sql.dml.Delete)
        ]

        # `agents` is only one of the three roads to an agent row disappearing:
        # agents.project_id is ON DELETE CASCADE (tables.py:22), and retention
        # purges orgs and projects in the same pass, so a cutoff dropped from
        # EITHER of those destroys in-window agents' artifacts just as surely.
        # Guarding only the direct sweep would leave Road B — the one this
        # dossier's §5 calls the larger population — unpinned.
        for table in ("agents", "projects", "orgs"):
            matching = [d for d in deletes if d.table.name == table]
            assert len(matching) == 1, f"{table} must be swept exactly once per pass"

            compiled = matching[0].compile(dialect=postgresql.dialect())
            sql = " ".join(str(compiled).split())
            assert "deleted_at IS NOT NULL" in sql, f"{table} sweep lost its deleted_at guard"
            assert "deleted_at <" in sql, f"{table} sweep lost its cutoff comparison"

            cutoffs = [v for v in compiled.params.values() if isinstance(v, datetime)]
            assert cutoffs, f"the {table} sweep must bind a cutoff"
            assert all(c == _NOW - timedelta(days=gc._RETENTION_DAYS) for c in cutoffs), (
                f"the {table} sweep's cutoff must stay in lockstep with the GC's window; if these "
                "ever diverge, 'no row implies past window' stops being true and the GC starts "
                "destroying data inside its recovery window"
            )


class TestParsing:
    def test_volume_name_roundtrips(self) -> None:
        agent_id = uuid.uuid4()
        assert gc._parse_agent_id(gc._volume_name(agent_id)) == agent_id

    @pytest.mark.parametrize(
        "name",
        [
            "postgres-data",
            "smap-agent-fs-",
            "smap-agent-fs-not-a-uuid",
            "prefix-smap-agent-fs-00000000-0000-0000-0000-000000000000",
            "",
            # uuid.UUID() accepts every form below and maps them all to the same
            # id. None is a name we emit, so none may be claimed as ours: the
            # whole safety of an irreversible worker rests on this parse.
            "smap-agent-fs-{00000000-0000-0000-0000-000000000001}",
            "smap-agent-fs-urn:uuid:00000000-0000-0000-0000-000000000001",
            "smap-agent-fs-00000000000000000000000000000001",
            "smap-agent-fs-0000-0000-0000-0000-0000-0000-0000-0001",
            "smap-agent-fs-00000000-0000-0000-0000-00000000000A",
            "smap-agent-fs-00000000-0000-0000-0000-000000000001urn:",
        ],
    )
    def test_rejects_non_agent_volumes(self, name: str) -> None:
        assert gc._parse_agent_id(name) is None

    def test_prefix_roundtrips(self) -> None:
        agent_id = uuid.uuid4()
        assert gc._parse_prefix_agent_id(f"{agent_id}/") == agent_id

    @pytest.mark.parametrize(
        "entry",
        [
            "",
            "not-a-uuid/",
            "00000000-0000-0000-0000-000000000001",  # bare object, no trailing slash
            "00000000-0000-0000-0000-000000000001/nested/",  # more than one segment
            "{00000000-0000-0000-0000-000000000001}/",
            "urn:uuid:00000000-0000-0000-0000-000000000001/",
        ],
    )
    def test_rejects_non_agent_prefixes(self, entry: str) -> None:
        """MinIO keys legally contain '{', ':' and Unicode, unlike Docker volume
        names — so this parser is the looser attack surface of the two."""
        assert gc._parse_prefix_agent_id(entry) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
            # Go's RFC3339Nano trims trailing zeros, so every fraction width is
            # producible. `.123Z` used to parse as None, which silently recreated
            # the never-purges bug for any volume Docker stamped that way.
            ("2026-01-02T03:04:05.123Z", datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)),
            ("2026-01-02T03:04:05.5Z", datetime(2026, 1, 2, 3, 4, 5, 500000, tzinfo=UTC)),
            ("2026-01-02T03:04:05.123456789Z", datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)),
        ],
    )
    def test_parses_docker_created_at(self, raw: str, expected: datetime) -> None:
        assert gc._parse_created_at(raw) == expected

    def test_fraction_digits_are_not_confused_with_offset_digits(self) -> None:
        """Both are digits. Truncating them together dropped the offset and
        produced a WRONG time rather than a parse failure — the worse outcome,
        since the age check would then silently use it."""
        parsed = gc._parse_created_at("2026-01-02T03:04:05.12+08:00")
        assert parsed == datetime(2026, 1, 2, 3, 4, 5, 120000, tzinfo=timezone(timedelta(hours=8)))

    @pytest.mark.parametrize("raw", [None, "", "not-a-timestamp", "2026-13-45T99:99:99Z"])
    def test_unparseable_created_at_is_none(self, raw) -> None:
        assert gc._parse_created_at(raw) is None


# ---------------------------------------------------------------------------
# Session volumes (2026-07-19-session-dir-room-isolation) ??AC-8, AC-9, AC-11
#
# These accumulate one per (agent, chatroom) rather than one per agent, so a GC
# that did not recognise the name shape would leak a volume per conversation.
# ---------------------------------------------------------------------------


def _session_vol(agent_id: uuid.UUID, room_id: uuid.UUID, created=_ANCIENT) -> _FakeVolume:
    return _FakeVolume(gc._session_volume_name(agent_id, room_id), created)


class TestSessionVolumeNameParsing:
    def test_round_trips(self) -> None:
        agent_id, room_id = uuid.uuid4(), uuid.uuid4()
        assert gc._parse_session_ids(gc._session_volume_name(agent_id, room_id)) == (agent_id, room_id)

    def test_agrees_with_the_name_the_sandbox_actually_creates(self) -> None:
        """The GC and the sandbox each build these names, coupled only by a comment.

        Drift is silent and permanent: an unparseable name is dropped by design, so
        the GC would simply stop seeing session volumes and leak one per
        conversation, with nothing to notice. Same for the per-agent volume.
        """
        from contexts.agents.infrastructure.sandbox import docker_runsc as ds

        agent_id, room_id = uuid.uuid4(), uuid.uuid4()
        assert gc._session_volume_name(agent_id, room_id) == ds._session_volume_name(agent_id, room_id)
        assert gc._volume_name(agent_id) == ds._agent_volume_name(agent_id)
        # And the GC can parse what the sandbox creates, not merely match its string.
        assert gc._parse_session_ids(ds._session_volume_name(agent_id, room_id)) == (agent_id, room_id)
        assert gc._parse_agent_id(ds._agent_volume_name(agent_id)) == agent_id

    def test_agent_volume_is_not_mistaken_for_a_session_volume(self) -> None:
        """The two prefixes must not overlap in either direction."""
        agent_id = uuid.uuid4()
        assert gc._parse_session_ids(gc._volume_name(agent_id)) is None
        assert gc._parse_agent_id(gc._session_volume_name(agent_id, uuid.uuid4())) is None

    @pytest.mark.parametrize(
        "name",
        [
            "smap-agent-session-",
            "smap-agent-session-not-a-uuid",
            # One valid uuid but no room half ??the shape that a naive
            # rsplit("-") would have accepted by chopping a uuid in two.
            f"smap-agent-session-{uuid.uuid4()}",
            f"smap-agent-session-{uuid.uuid4()}-{uuid.uuid4()}-{uuid.uuid4()}",
            f"prefix-smap-agent-session-{uuid.uuid4()}-{uuid.uuid4()}",
            f"smap-agent-session-{uuid.uuid4()}-{str(uuid.uuid4()).upper()}",
            f"smap-agent-session-{uuid.uuid4()}-{str(uuid.uuid4()).replace('-', '')}",
            "postgres-data",
        ],
    )
    def test_rejects_anything_that_is_not_two_canonical_uuids(self, name: str) -> None:
        assert gc._parse_session_ids(name) is None


@pytest.mark.asyncio
async def test_session_volume_is_purged_when_its_agent_is_gone(monkeypatch, armed) -> None:
    """AC-8/AC-9. No agents row ??the same inference the per-agent volume uses."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={}, room_rows={room_id: None})

    await gc.run_once(now=_NOW)

    assert vol.removed is True


@pytest.mark.asyncio
async def test_session_volume_is_purged_when_its_chatroom_is_gone(monkeypatch, armed) -> None:
    """AC-8. The room half: a live agent does not keep a dead room's volume."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={agent_id: None}, room_rows={})

    await gc.run_once(now=_NOW)

    assert vol.removed is True


@pytest.mark.asyncio
async def test_session_volume_of_a_live_agent_and_live_room_is_kept(monkeypatch, armed) -> None:
    """AC-8. Both alive ??the steady state, however old the volume is."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={agent_id: None}, room_rows={room_id: None})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_session_volume_of_a_room_deleted_inside_the_window_is_kept(monkeypatch, armed) -> None:
    """AC-8. A soft-deleted room can be restored (`ChatroomService.admin_restore`),
    so its volume gets the same recovery window a soft-deleted agent's does."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={agent_id: None}, room_rows={room_id: _WELL_INSIDE})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_session_volume_of_a_room_past_the_window_is_purged(monkeypatch, armed) -> None:
    """AC-8. Past the window, purge from the row itself ??the batch-limit case."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={agent_id: None}, room_rows={room_id: _WELL_PAST})

    await gc.run_once(now=_NOW)

    assert vol.removed is True


@pytest.mark.asyncio
async def test_young_orphan_session_volume_is_kept(monkeypatch, armed) -> None:
    """AC-8. The auto-create race: Docker makes the volume on container create,
    before the room's row is visible to this worker. The CreatedAt floor covers
    session volumes too, or a turn starting mid-sweep loses its attachments."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id, created=_NOW)
    _install(monkeypatch, volumes=[vol], rows={}, room_rows={})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_empty_chatrooms_table_refuses_to_sweep_session_volumes(monkeypatch, armed) -> None:
    """AC-11. The blast-radius guard, chatrooms dimension.

    A wrong DSN or a restoring replica makes every room look deleted. The agents
    guard cannot catch this: the agents table is perfectly healthy here, which is
    exactly what makes the conversation half's emptiness so easy to act on.
    """
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(
        monkeypatch,
        volumes=[vol],
        rows={agent_id: None},  # agents table healthy and the agent is alive
        room_rows={},
        rooms_populated=False,  # ...but chatrooms is empty altogether
    )

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_session_volumes_are_not_swept_when_the_agents_guard_fires(monkeypatch, armed) -> None:
    """AC-11. The agents guard refuses the whole pass, session volumes included ??    it returns before any sweep runs, and that must stay true."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={}, populated=False, room_rows={room_id: None})

    await gc.run_once(now=_NOW)

    assert vol.removed is False


@pytest.mark.asyncio
async def test_both_volume_shapes_sweep_in_one_pass(monkeypatch, armed) -> None:
    """AC-9. After an agent is collected, no volume bearing its id survives in
    either name shape ??the leak this task could otherwise have introduced."""
    agent_id, room_a, room_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agent_vol = _FakeVolume(gc._volume_name(agent_id), _ANCIENT)
    sess_a = _session_vol(agent_id, room_a)
    sess_b = _session_vol(agent_id, room_b)
    _install(monkeypatch, volumes=[agent_vol, sess_a, sess_b], rows={}, room_rows={})

    await gc.run_once(now=_NOW)

    assert agent_vol.removed is True
    assert sess_a.removed is True
    assert sess_b.removed is True


@pytest.mark.asyncio
async def test_unarmed_sweep_removes_no_session_volume(monkeypatch) -> None:
    """AC-7's posture applied here: destructive by opt-in only. No `armed` fixture."""
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()
    vol = _session_vol(agent_id, room_id)
    _install(monkeypatch, volumes=[vol], rows={}, room_rows={})

    await gc.run_once(now=_NOW)

    assert vol.removed is False
