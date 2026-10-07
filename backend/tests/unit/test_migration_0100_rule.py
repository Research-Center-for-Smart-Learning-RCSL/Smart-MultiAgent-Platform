"""Migration 0100's frozen copy of the account-name rule (no database needed).

The migration carries its own copy of ``normalise_label`` so a later change to
that module cannot alter what it does. These pin the copy against the live rule
as of this revision, in the unit tier, so a drift fails the fast CI job rather
than only the db one.

Invisible characters are built with ``chr`` here and written as escapes in the
sources, for the reason ``shared_kernel/labels.py`` gives at ``_KEEP``: a tool
that strips invisible characters would otherwise empty the keep-list silently.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from shared_kernel.labels import MAX_DISPLAY_NAME, normalise_label

_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _BACKEND / "alembic" / "versions" / "0100_normalise_account_display_names.py"
_spec = importlib.util.spec_from_file_location("_migration_0100_rule", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0100 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0100)

_ZWJ = chr(0x200D)
_VS16 = chr(0xFE0F)
_ACUTE = chr(0x0301)
_RLO = chr(0x202E)
_FAMILY = chr(0x1F468) + _ZWJ + chr(0x1F469) + _ZWJ + chr(0x1F467)

_SAMPLES = [
    f"Alice{_RLO}eciwlA",
    "Bob\nTeacher",
    "\n\t",
    "Carol Chen",
    "W" * 49 + _FAMILY,
    "x" * 60,
    "  padded  ",
    ("cafe" + _ACUTE) * 13,
    "a" + _ACUTE * 60,
    chr(0x2764) + _VS16 + " heart",
]


@pytest.mark.parametrize("raw", _SAMPLES)
def test_the_frozen_rule_matches_the_live_one(raw: str) -> None:
    assert migration_0100._normalise(raw) == normalise_label(raw, max_len=MAX_DISPLAY_NAME)


@pytest.mark.parametrize(
    "path",
    [_MIGRATION_PATH, _BACKEND / "shared_kernel" / "labels.py"],
    ids=["migration_0100", "labels"],
)
def test_the_keep_list_is_written_as_escapes(path: Path) -> None:
    """Literal ZWJ/VS16 in source is one editor pass from becoming ``""``, after
    which every emoji sequence is stripped -- irreversibly, in a migration."""
    source = path.read_text(encoding="utf-8")

    assert _ZWJ not in source
    assert _VS16 not in source


def test_the_keep_list_still_holds_both_characters() -> None:
    assert migration_0100._KEEP == (_ZWJ, _VS16)
