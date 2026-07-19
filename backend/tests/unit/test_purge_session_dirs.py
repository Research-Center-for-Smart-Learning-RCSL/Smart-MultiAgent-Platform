"""Unit tests for the one-shot legacy-session-dir purge (AC-7).

`docs/tasks/2026-07-19-session-dir-room-isolation`. The container-level clearing
is tested in `test_workspace_volume_reconcile.py`, which drives the real
`_RECONCILE` script against a real filesystem. What is tested here is the sweep
around it: which volumes it picks, that it deletes nothing unarmed, and that one
volume's failure does not abandon the rest.
"""

from __future__ import annotations

import uuid

import pytest

from smap.maintenance import purge_session_dirs as ps


class _FakeVolume:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeDocker:
    def __init__(self, names: list[str]) -> None:
        self.volumes = type("_C", (), {"list": lambda _s: [_FakeVolume(n) for n in names]})()


class _FakeSandbox:
    """Records the purges attempted; optionally fails for chosen agents."""

    def __init__(self, failing: set[uuid.UUID] | None = None) -> None:
        self.purged: list[uuid.UUID] = []
        self._failing = failing or set()

    async def purge_legacy_session_dirs(self, *, agent_id: uuid.UUID) -> None:
        if agent_id in self._failing:
            raise RuntimeError("docker daemon unreachable")
        self.purged.append(agent_id)


def _install(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(ps, "docker_client", lambda: _FakeDocker(names))


def test_enumerates_only_agent_volumes(monkeypatch) -> None:
    """Someone else's volumes share the daemon; the host had 144 unrelated ones
    when the GC was written. A laxer parser here would undo that care."""
    agent_id = uuid.uuid4()
    _install(
        monkeypatch,
        [
            f"smap-agent-fs-{agent_id}",
            "postgres-data",
            "smap-agent-fs-not-a-uuid",
            # Session volumes are the NEW shape and hold no legacy tree: purging
            # one would be clearing a directory that was never there.
            f"smap-agent-session-{uuid.uuid4()}-{uuid.uuid4()}",
        ],
    )

    assert ps._enumerate_agent_volumes() == [agent_id]


async def test_unarmed_run_deletes_nothing(monkeypatch) -> None:
    """The posture that makes a destructive repair safe to point at production."""
    agent_id = uuid.uuid4()
    box = _FakeSandbox()

    report = await ps._purge_all([agent_id], armed=False, sandbox=box)

    assert box.purged == []
    assert report.would_purge == 1
    assert report.purged == 0
    assert report.dry_run is True


async def test_armed_run_purges_every_volume(monkeypatch) -> None:
    agent_ids = [uuid.uuid4(), uuid.uuid4()]
    box = _FakeSandbox()

    report = await ps._purge_all(agent_ids, armed=True, sandbox=box)

    assert box.purged == agent_ids
    assert report.purged == 2
    assert report.failed == 0
    assert report.dry_run is False


async def test_one_failure_does_not_abandon_the_remaining_volumes(monkeypatch) -> None:
    """Fail-open per volume. A repair that stops at the first unreachable daemon
    leaves every later agent exposed for no reason, and hides how many there were."""
    ok_first, broken, ok_last = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    box = _FakeSandbox(failing={broken})

    report = await ps._purge_all([ok_first, broken, ok_last], armed=True, sandbox=box)

    assert box.purged == [ok_first, ok_last]
    assert report.purged == 2
    assert report.failed == 1
    assert report.seen == 3


def test_unreachable_daemon_raises_instead_of_reporting_a_clean_host(monkeypatch) -> None:
    """The distinction that matters for a one-shot: "nothing to repair" and "could
    not look" must never render the same. `agent_fs_gc` degrades to an empty list
    here because it runs again tomorrow; this command does not get a tomorrow."""

    def _boom():
        raise RuntimeError("daemon unreachable")

    monkeypatch.setattr(ps, "docker_client", _boom)

    with pytest.raises(ps.PurgeUnavailable):
        ps.run(armed=False)


def test_run_on_a_host_with_no_agent_volumes_is_a_clean_noop(monkeypatch) -> None:
    _install(monkeypatch, ["postgres-data"])

    report = ps.run(armed=True)

    assert report.seen == 0
    assert report.purged == 0
    assert report.failed == 0


@pytest.mark.parametrize("armed", [True, False])
def test_run_reports_dry_run_state_honestly(monkeypatch, armed: bool) -> None:
    """`dry_run` drives what the CLI tells the operator, including on the empty
    host: a run that deleted nothing because it was unarmed must not read the
    same as one that found nothing to delete."""
    _install(monkeypatch, [])

    report = ps.run(armed=armed)

    assert report.dry_run is not armed
