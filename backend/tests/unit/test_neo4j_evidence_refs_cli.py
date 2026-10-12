"""Exit status of ``python -m smap.maintenance neo4j-evidence-refs``.

The command's "remaining=0" is the gate for the rename's next release, so a run
that leaves edges behind (a busy config) must fail a scripted invocation. The copy
itself is covered against real Neo4j in
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
        return CliRunner().invoke(app, ["neo4j-evidence-refs"]).exit_code


@pytest.mark.parametrize(
    ("report", "code"),
    [
        (cmd.EvidenceRefsReport(dry_run=True, pending=3, copied=0, remaining=3, busy=()), 0),
        (cmd.EvidenceRefsReport(dry_run=False, pending=3, copied=3, remaining=0, busy=()), 0),
        (
            cmd.EvidenceRefsReport(dry_run=False, pending=3, copied=1, remaining=2, busy=(uuid.uuid4(),)),
            1,
        ),
    ],
    ids=["dry-run", "armed-complete", "armed-busy"],
)
def test_exit_status(report: cmd.EvidenceRefsReport, code: int) -> None:
    assert _invoke(report) == code


def test_arming_needs_an_explicit_truthy_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cmd.ARMED_ENV, raising=False)
    assert cmd.is_armed() is False
    monkeypatch.setenv(cmd.ARMED_ENV, "False")
    assert cmd.is_armed() is False
    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    assert cmd.is_armed() is True
