"""Migration 0101's frozen blank-name predicate (no database needed).

The migration carries its own copy of the visible-character rule so a later
change to ``shared_kernel.labels`` cannot alter what it does. These pin the copy
against the live rule as of this revision, in the unit tier, so a drift fails the
fast CI job rather than only the db one. Invisible characters are built with
``chr``, for the reason ``shared_kernel/labels.py`` gives at ``_KEEP``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from shared_kernel.labels import MAX_GUEST_LABEL, normalise_label

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0101_blank_display_names.py"
_spec = importlib.util.spec_from_file_location("_migration_0101_rule", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0101 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0101)

_ZWJ = chr(0x200D)
_VS16 = chr(0xFE0F)
_ACUTE = chr(0x0301)
_FILLER = chr(0x3164)
_FAMILY = chr(0x1F468) + _ZWJ + chr(0x1F469) + _ZWJ + chr(0x1F467)

_SAMPLES = [
    _FILLER,
    chr(0xFFA0),
    chr(0x2800),
    _ACUTE,
    _ACUTE + chr(0x0308),
    _ZWJ,
    _VS16,
    _ZWJ + _VS16 + _ZWJ,
    f" {_FILLER} {chr(0x2800)} ",
    chr(0x3000),
    "\n\t",
    chr(0x202E),
    "Carol Chen",
    f"Al{_FILLER}ice",
    "e" + _ACUTE,
    _FAMILY,
    chr(0x2764) + _VS16,
    '"',
    "[ ]",
]


@pytest.mark.parametrize("raw", _SAMPLES)
def test_the_frozen_predicate_matches_the_live_rule(raw: str) -> None:
    assert migration_0101._is_blank(raw) == (normalise_label(raw, max_len=MAX_GUEST_LABEL) is None)


def test_the_keep_list_is_written_as_escapes() -> None:
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert _ZWJ not in source
    assert _VS16 not in source
    assert migration_0101._KEEP == (_ZWJ, _VS16)
