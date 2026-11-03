"""Exit status of ``python -m smap.maintenance neo4j-evidence-refs --remove-legacy``.

The command's "remaining=0" is what the rename's last step is gated on, so a run
that leaves edges behind (a busy config) must fail a scripted invocation. The
removal itself is covered against real Neo4j in
``tests/integration/test_neo4j_evidence_refs_expand.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from smap.maintenance import neo4j_evidence_refs as cmd
from smap.maintenance.__main__ import app


def _invoke(report: cmd.EvidenceRefsReport) -> int:
    with patch.object(cmd, "run", AsyncMock(return_value=report)):
        return CliRunner().invoke(app, ["neo4j-evidence-refs", "--remove-legacy"]).exit_code


@pytest.mark.parametrize(
    ("report", "code"),
    [
        (cmd.EvidenceRefsReport(dry_run=True, pending=3, removed=0, remaining=3, busy=()), 0),
        (cmd.EvidenceRefsReport(dry_run=False, pending=3, removed=3, remaining=0, busy=()), 0),
        (
            cmd.EvidenceRefsReport(dry_run=False, pending=3, removed=1, remaining=2, busy=(uuid.uuid4(),)),
            1,
        ),
    ],
    ids=["dry-run", "armed-complete", "armed-busy"],
)
def test_exit_status(report: cmd.EvidenceRefsReport, code: int) -> None:
    assert _invoke(report) == code


def test_the_bare_command_refuses_without_touching_the_graph() -> None:
    # The bare form ran the retired release A copy; an old runbook line must not
    # silently start a different operation.
    run = AsyncMock()
    with patch.object(cmd, "run", run):
        result = CliRunner().invoke(app, ["neo4j-evidence-refs"])
    assert result.exit_code == 2
    run.assert_not_called()


def test_arming_needs_an_explicit_truthy_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cmd.ARMED_ENV, raising=False)
    assert cmd.is_armed() is False
    monkeypatch.setenv(cmd.ARMED_ENV, "False")
    assert cmd.is_armed() is False
    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    assert cmd.is_armed() is True
