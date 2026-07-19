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


def test_the_documented_invocation_actually_dispatches(monkeypatch) -> None:
    """The command must be reachable as `python -m smap.maintenance purge-session-dirs`.

    Typer collapses a single-command app into the root, which drops the
    subcommand name: the documented invocation then dies with "Got unexpected
    extra argument" before any of the code below runs. Shipped that way once.
    The failure is invisible to every other test here, because they all import
    the implementation and bypass the CLI layer entirely.
    """
    from typer.testing import CliRunner

    from smap.maintenance.__main__ import app

    monkeypatch.setattr(ps, "docker_client", lambda: _FakeDocker([]))

    result = CliRunner().invoke(app, ["purge-session-dirs"])

    assert "extra argument" not in result.output
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),  # unset
        ("", False),
        ("false", False),
        ("False", False),  # the exact string typer handed us when the flag was absent
        ("0", False),
        ("no", False),
        ("maybe", False),  # unrecognised is never consent
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_arming_requires_an_explicit_truthy_environment_value(monkeypatch, value, expected) -> None:
    """The single decision separating a report from an irreversible delete.

    This lives on an env var, not a --arm flag, because typer 0.12.5 against the
    installed click hands a bool option the *string* "False" when the flag is
    absent -- truthy -- and None when it is passed. That inverted this exact
    check: an unarmed run deleted, and --arm did not. Shipped that way in
    1acc4c8 and caught by the CLI-layer test above, which is the only test here
    that does not bypass typer.
    """
    monkeypatch.delenv(ps._ARMED_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(ps._ARMED_ENV, value)

    assert ps.is_armed() is expected


def test_run_defaults_to_the_environment_and_never_infers_consent(monkeypatch) -> None:
    """`run()` with no argument must ask the environment, and a non-bool argument
    must read as "not armed" rather than as truthiness."""
    monkeypatch.setattr(ps, "_enumerate_agent_volumes", lambda: [uuid.uuid4()])
    box = _FakeSandbox()
    monkeypatch.setattr(ps, "DockerRunscSandbox", lambda: box)

    monkeypatch.delenv(ps._ARMED_ENV, raising=False)
    assert ps.run().dry_run is True

    monkeypatch.setenv(ps._ARMED_ENV, "true")
    assert ps.run().dry_run is False

    # The shape that caused the incident: a truthy non-bool must not arm.
    monkeypatch.delenv(ps._ARMED_ENV, raising=False)
    assert ps.run(armed="False").dry_run is True  # type: ignore[arg-type]


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
