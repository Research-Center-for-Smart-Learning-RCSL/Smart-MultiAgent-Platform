"""The revisions table is internal ([R30.25]; research data integrity spec, AC-13).

A revision holds ``validator_config``, which can carry an answer key. Nothing at
the HTTP or WebSocket layer, and no facade, may name the table: the only readers
are the activities context's own repository and the migration.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_TABLE = "activity_type_revisions"


def test_no_route_or_facade_names_the_revisions_table() -> None:
    surfaces = [
        *(_BACKEND / "app").rglob("*.py"),
        *(_BACKEND / "contexts").glob("*/interfaces/*.py"),
    ]
    assert surfaces, "the scan found no files; the paths above are wrong"
    offenders = [str(p.relative_to(_BACKEND)) for p in surfaces if _TABLE in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_the_scan_would_see_a_reference() -> None:
    """The repository does name it, so a scan that cannot find it there is blind."""
    repo = _BACKEND / "contexts/activities/infrastructure/repositories/type_repo.py"
    assert _TABLE in repo.read_text(encoding="utf-8")
